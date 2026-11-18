# ruff: noqa: F811  (the `world` fixture is imported from test_publication_registry and used by name)
"""Phase 3: the AI publishing plan with a scripted model (no paid call): nothing runs unless asked, a run is saved and read back, the metadata and the calendar are checked by the app,
what was approved or written by the owner is never overwritten, the one-click approval says what it approves and what it leaves out, the Monitoring page shows the run once."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest
from test_publication_registry import _video, events, planned, pub, world  # noqa: F401
from test_publication_safeguards import approve_both, meet_every_film_requirement  # noqa: F401

from app.db.models import AgentRun, Publication, PublishingPlanRun
from app.providers.generation.base import GenerationResult
from app.publishing.config import publishing_config
from app.publishing.intelligence import schedule as SCH
from app.publishing.intelligence import service as S
from app.publishing.intelligence import validate as V
from app.publishing.registry import PublishingRefused

CFG = publishing_config()


class Model:
    """A scripted provider: it answers the planner and the metadata writer from the request itself and counts every call."""

    def __init__(self, planner=None, writer=None, critic=None, fixer=None, proofreader=None):
        self.calls: list[tuple[str, dict]] = []
        self.planner, self.writer, self.critic, self.fixer, self.proofreader = planner, writer, critic, fixer, proofreader

    async def generate_structured(self, role, system, user, images=None, **extra):
        data = json.loads(user)
        self.calls.append((role, data))
        answer = {"publishing_planner": lambda: (self.planner or self.default_plan)(data), "publishing_hook_critic": lambda: (self.critic or self.default_critic)(data),
                                                                                                                                                  "publishing_orthography_fixer": lambda: (self.fixer or (lambda d: {"fields": {}}))(data),
                                                                                                                                                  "publishing_proofreader": lambda: (self.proofreader or self.default_proof)(data)}.get(role, lambda: (self.writer or self.default_metadata)(data))()
        if isinstance(answer, Exception):
            raise answer
        return answer, GenerationResult(text=json.dumps(answer), model=f"fake-{role}", provider="fake", input_tokens=1000, output_tokens=500, total_tokens=1500, cost_usd=0.01)

    @staticmethod
    def default_plan(view):
        keys = [c["clip_key"] for c in view["clips"]]
        items = []
        for destination in ("youtube", "facebook", "instagram", "tiktok"):
            if "film" in view["platforms"].get(destination, {}).get("kinds", []):
                items.append({"destination": f"{destination}/film", "asset": "film", "date_offset_days": 0, "local_time": "20:30", "reason": "film first"})
            for n, key in enumerate(reversed(keys), start=1):
                items.append({"destination": f"{destination}/short", "asset": key, "date_offset_days": n, "local_time": "20:30", "reason": f"slot {n}"})
        return {"clips": [{"clip_key": k, "scores": {s: 5 + i % 4 for s in V.SCORES}, "platform_fit": {"youtube": 7, "instagram": 8}, "strengths": f"{k} is concrete.", "risks": "none"} for i, k in enumerate(keys)],
                "sequences": {p: [{"clip_key": k, "reason": f"{k} on {p}"} for k in reversed(keys)] for p in view["platforms"] if "short" in view["platforms"][p]["kinds"]},
                "schedule": {"items": items, "summary": "Film first, then one Short a day.", "assumptions": ["No audience data exists."]},
                "routing": {f"{p}/short": {"method": view["platforms"][p]["routing_methods"][0], "reason": "supported"} for p in view["platforms"] if "short" in view["platforms"][p]["kinds"]},
                "warnings": []}

    @staticmethod
    def default_proof(view):
        """Nothing to correct: every text comes back as it went."""
        return {"items": [{**{k: i[k] for k in ("key", "title", "text", "hashtags", "keywords", "cta")}, "changes": []} for i in view["items"]]}

    @staticmethod
    def default_critic(view):
        """Every hook is strong: the critic changes nothing unless a test scripts it."""
        return {"items": [{"asset": i["asset"], "stakes": 8, "gap": 8, "concrete": 8, "natural": 8, "risk": "none", "direction": ""} for i in view["items"]]}

    @staticmethod
    def default_metadata(view):
        limits = view["limits"]
        items = []
        for entry in view["items"]:
            limit = limits[entry["kind"]]
            items.append({"asset": entry["asset"], "title": "عنوان آزمایشی" if limit["title_max"] else "", "text": f"سطر اول جذاب برای {'فیلم' if entry['asset'] == 'film' else entry['asset']}.\nادامهٔ متن.",
                          "hashtags": ["#پرونده_جنایی", "#رد_خاموش"], "keywords": ["پرونده جنایی"] if limit["keywords_max_chars"] else [],
                          "chapters": [{"time": c["time"], "title": c["title"]} for c in (view["film"] or {}).get("chapters", [])] if entry["kind"] == "film" and limit["chapters"] else [],
                          "cta": "فیلم کامل در یوتیوب.", "routing_note": "Points to the film.", "facts_used": [f"clip:{entry['asset']}"] if entry["kind"] == "short" else ["film"]})
        return {"platform": view["platform"], "items": items, "warnings": []}


@pytest.fixture(autouse=True)
def _no_proofreading(monkeypatch):
    """The proofreader has its own tests below; the others count calls without it."""
    monkeypatch.setattr(CFG.intelligence.generation, "proofread_languages", [])


def proofreading(monkeypatch, languages=("fa",)):
    monkeypatch.setattr(CFG.intelligence.generation, "proofread_languages", list(languages))


def fix_word(old: str, new: str):
    """A scripted proofreader: the same one correction on every text of the asset."""
    def answer(view):
        items = []
        for i in view["items"]:
            fixed = {k: (i[k].replace(old, new) if isinstance(i[k], str) else [t.replace(old, new) for t in i[k]]) for k in ("title", "text", "hashtags", "keywords", "cta")}
            items.append({"key": i["key"], **fixed, "changes": [{"field": "text", "old": old, "new": new, "why": "spelling"}] if fixed != {k: i[k] for k in fixed} else []})
        return {"items": items}
    return answer


def generate(world, model, *, force=False):
    run = S.start(world.db, world.registry, world.case, "fa", force=force)
    asyncio.run(S.execute(run.id, gen=model))
    world.db.expire_all()
    return world.db.get(PublishingPlanRun, run.id)


# --- nothing runs unless the owner asks -----------------------------------------------------------------------------------------------------------------------------

def test_reading_the_state_never_calls_a_model(world, client, monkeypatch):
    called = []
    monkeypatch.setattr("app.publishing.intelligence.agents.get_generation_provider", lambda: called.append("provider") or Model())
    planned(world)
    state = S.state(world.db, world.registry, world.case, "fa")
    assert called == [] and state["latest"] is None and state["running"] is None and state["estimate"]["calls"] == 9
    api_state = client.get(f"/api/cases/{world.case.id}/publishing/fa/ai").json()
    studio = client.get(f"/api/cases/{world.case.id}/publishing/fa").json()
    assert called == [] and api_state["estimate"]["eur_high"] > api_state["estimate"]["eur_low"] > 0 and studio["planned"] is True
    assert world.db.query(PublishingPlanRun).count() == 0 and world.db.query(AgentRun).filter(AgentRun.case_id == world.case.id).count() == 0


def test_the_estimate_names_the_models_and_is_not_a_price(world):
    planned(world)
    estimate = S.state(world.db, world.registry, world.case, "fa")["estimate"]
    assert estimate["models"] == {"publishing_planner": "claude-sonnet-5-5", "publishing_metadata_writer": "claude-sonnet-5-5", "publishing_hook_critic": "claude-sonnet-5-5"} and "Not a price" in estimate["basis"]


def test_a_language_without_a_schedule_cannot_be_planned(world):
    with pytest.raises(S.PlanRunError, match="no publishing schedule"):
        S.start(world.db, world.registry, world.case, "en")
    assert S.state(world.db, world.registry, world.case, "en")["reason"].startswith("Language 'en' has no publishing schedule")


# --- a run: saved, read back, applied ---------------------------------------------------------------------------------------------------------------------------------

def test_a_run_saves_its_recommendations_and_writes_them_into_the_publications(world):
    model = Model()
    run = generate(world, model)
    assert run.status == "done" and len(model.calls) == 9 and [r for r, _ in model.calls].count("publishing_planner") == 1 and [r for r, _ in model.calls].count("publishing_hook_critic") == 4
    result = json.loads(run.result_json)
    assert set(result) >= {"analysis", "schedule", "metadata", "usage", "models", "fingerprint"} and result["usage"]["calls"] == 9 and result["usage"]["input_tokens"] == 9000 and result["usage"]["output_tokens"] == 4500
    assert result["usage"]["priced_calls"] == 9 and result["usage"]["cost_eur"] >= 0 and "ledger" in result["usage"]["cost_source"] and len(result["usage"]["detail"]) == 9      # the ledger books every call
    assert result["usage"]["models"] == ["fake-publishing_hook_critic", "fake-publishing_metadata_writer", "fake-publishing_planner"] and len(result["usage"]["agent_run_ids"]) == 9
    assert [s["status"] for s in json.loads(run.progress_json)] == ["done"] * 6
    short = pub(world, "instagram", "short", "C1")
    assert short.metadata_source == "ai" and short.caption.startswith("سطر اول جذاب برای C1") and "ElevenLabs" in short.caption
    meta = json.loads(short.metadata_json)
    assert meta["hashtags"] == ["#پرونده_جنایی", "#رد_خاموش"] and meta["cta"] == "فیلم کامل در یوتیوب." and short.sequence == 5       # the planner ranked C5 first
    assert pub(world, "instagram", "short", "C5").sequence == 1
    film = pub(world, "youtube", "film", f"V{world.film.id}")
    assert json.loads(film.metadata_json)["title"] == "عنوان آزمایشی" and film.sequence is None


def test_the_calendar_puts_each_platforms_film_first_and_follows_the_ranked_order(world):
    generate(world, Model())
    for platform in ("youtube", "facebook"):
        film = pub(world, platform, "film", f"V{world.film.id}")
        shorts = sorted((pub(world, platform, "short", k) for k in ("C1", "C2", "C3", "C4", "C5")), key=lambda p: p.sequence)
        assert all(s.scheduled_at > film.scheduled_at for s in shorts)
        assert [s.scheduled_at for s in shorts] == sorted(s.scheduled_at for s in shorts) and len({s.scheduled_at.date() for s in shorts}) == 5
        assert film.scheduled_at.isoformat()[11:16] == "17:00"                                    # 20:30 in Tehran
    assert pub(world, "tiktok", "short", "C5").scheduled_at < pub(world, "tiktok", "short", "C1").scheduled_at


def test_reopening_reads_the_saved_plan_and_a_second_start_is_refused_unless_forced(world, client, monkeypatch):
    generate(world, Model())
    monkeypatch.setattr("app.publishing.intelligence.agents.get_generation_provider", lambda: (_ for _ in ()).throw(AssertionError("a model was asked")))
    state = client.get(f"/api/cases/{world.case.id}/publishing/fa/ai").json()
    assert state["latest"]["status"] == "done" and state["up_to_date"] is True and state["latest"]["result"]["schedule"]["slots"]
    assert state["latest"]["usage"]["calls"] == 9 and state["latest"]["result"]["schedule"]["notice"].startswith("No audience analytics")
    with pytest.raises(S.PlanRunError, match="up to date"):
        S.start(world.db, world.registry, world.case, "fa")
    assert S.start(world.db, world.registry, world.case, "fa", force=True).status == "running"


def test_a_changed_clip_makes_the_saved_plan_stale(world):
    generate(world, Model())
    from app.db.models import ShortClip

    row = world.db.query(ShortClip).filter_by(case_id=world.case.id, clip_key="C1").one()
    row.script_json = json.dumps({"hook": "a new hook"})
    world.db.commit()
    assert S.state(world.db, world.registry, world.case, "fa")["stale"] is True


def test_only_one_run_goes_at_a_time_and_an_old_one_counts_as_interrupted(world):
    run = S.start(world.db, world.registry, world.case, "fa")
    with pytest.raises(S.PlanRunError, match="already being generated"):
        S.start(world.db, world.registry, world.case, "fa", force=True)
    run.updated_at = run.updated_at - timedelta(seconds=CFG.intelligence.generation.running_timeout_seconds + 5)
    world.db.commit()
    assert S.effective_status(run, CFG) == "interrupted" and S.state(world.db, world.registry, world.case, "fa")["failed"]["status"] == "interrupted"
    assert S.start(world.db, world.registry, world.case, "fa", force=True).status == "running"       # an interrupted run does not block a new one


# --- what the app checks -------------------------------------------------------------------------------------------------------------------------------------------

def test_a_metadata_answer_over_a_limit_is_sent_back_once_and_the_repair_is_used(world):
    state = {"n": 0}

    def writer(view):
        answer = Model.default_metadata(view)
        if view["platform"] == "instagram":
            state["n"] += 1
            if state["n"] == 1:
                answer["items"][0]["text"] = "ن" * 3000
        return answer

    model = Model(writer=writer)
    run = generate(world, model)
    instagram = [d for r, d in model.calls if r == "publishing_metadata_writer" and d["platform"] == "instagram"]
    assert len(instagram) == 2 and "problems_to_fix" in instagram[1] and any("limit is 2200" in p for p in instagram[1]["problems_to_fix"])
    assert json.loads(run.result_json)["unresolved"] == {} and run.status == "done"


def test_what_is_still_wrong_after_the_repair_is_kept_visible_and_blocks_approval(world):
    def writer(view):
        answer = Model.default_metadata(view)
        if view["platform"] == "tiktok":
            answer["items"][0]["hashtags"] = [f"#tag{i}" for i in range(40)]
        return answer

    run = generate(world, Model(writer=writer))
    result = json.loads(run.result_json)
    assert result["unresolved"]["tiktok"] and any("hashtags, the limit is 20" in p for p in result["unresolved"]["tiktok"])
    flagged = world.registry.view(pub(world, "tiktok", "short", "C1"))
    assert any("limit is 20" in f["message"] for f in flagged["metadata_findings"] if f["severity"] == "error")
    assert any("Metadata:" in b for b in flagged["blockers"]) and flagged["eligibility"]["approve_content"] is False


def test_the_planners_unusable_answer_fails_the_run_and_changes_nothing(world):
    run = generate(world, Model(planner=lambda view: {"clips": "no"}))
    assert run.status == "failed" and "planner's answer is unusable" in run.error
    assert pub(world, "instagram", "short", "C1").metadata_source == "default" and run.finished_at is not None
    assert [s["status"] for s in json.loads(run.progress_json)][0] == "failed"


def test_one_platform_failing_does_not_stop_the_others(world):
    def writer(view):
        if view["platform"] == "facebook":
            return RuntimeError("provider down")
        return Model.default_metadata(view)

    run = generate(world, Model(writer=writer))
    result = json.loads(run.result_json)
    assert run.status == "done" and "facebook" in result["platform_errors"] and pub(world, "facebook", "short", "C1").metadata_source == "default"
    assert pub(world, "youtube", "short", "C1").metadata_source == "ai"


def test_routing_advice_with_a_method_the_platform_lacks_is_dropped_and_noted(world):
    def planner(view):
        plan = Model.default_plan(view)
        plan["routing"]["instagram/short"] = {"method": "related_video", "reason": "not an Instagram method"}
        return plan

    run = generate(world, Model(planner=planner))
    analysis = json.loads(run.result_json)["analysis"]
    assert "instagram/short" not in analysis["routing"] and any("instagram/short" in n for n in analysis["notes"])


def test_the_validators_catch_the_bad_things_and_say_which(world):
    limit = CFG.intelligence.metadata_limits["instagram/short"]
    base = {"title": "", "text": "متن", "hashtags": [], "keywords": [], "chapters": [], "cta": "", "routing_note": "", "facts_used": []}
    ok = dict(fact_ids={"F1"}, clip_keys={"C1"}, known_words={"nancy"}, chapter_times=[])

    def kinds(**change):
        item, _ = V.tidy({**base, **change})
        return {f["type"] for f in V.metadata_findings(item, "short", limit, **ok)}

    assert kinds() == set()
    assert "web_address" in kinds(text="ببینید www.example.com") and "web_address" in kinds(text="https://x.org/y")
    short = CFG.intelligence.metadata_limits["youtube/short"]                                       # a YouTube Short may carry the link to its film in the description
    item, _ = V.tidy({**base, "text": "فیلم کامل را ببینید\nhttps://www.youtube.com/watch?v=vcxqTGeT5Ss"})
    assert short.web_address_allowed and not limit.web_address_allowed
    assert not {f["type"] for f in V.metadata_findings(item, "short", short, **ok)} & {"web_address", "unknown_name"}
    assert "unknown_name" in kinds(text="Zorro was there") and "unknown_name" not in kinds(text="Nancy گفت") and "unknown_name" not in kinds(text="در YouTube")
    assert "keywords_not_supported" in kinds(keywords=["a"]) and "chapters_not_supported" in kinds(chapters=[{"time": "00:00", "title": "x"}])
    assert "unknown_fact" in kinds(facts_used=["F99"]) and "unknown_fact" not in kinds(facts_used=["F1", "clip:C1", "film"])
    assert "title_not_supported" in kinds(title="x") and "many_hashtags" in kinds(hashtags=[f"#t{i}" for i in range(6)]) and "too_many_hashtags" in kinds(hashtags=[f"#t{i}" for i in range(31)])
    tidy, fixes = V.tidy({**base, "hashtags": ["پرونده جنایی", "#پرونده_جنایی", "#"]})
    assert tidy["hashtags"] == ["#پرونده_جنایی"] and fixes
    film = CFG.intelligence.metadata_limits["youtube/film"]
    item, _ = V.tidy({**base, "title": "t", "chapters": [{"time": "00:00", "title": "a"}, {"time": "03:00", "title": "b"}]})
    assert "chapters_changed" in {f["type"] for f in V.metadata_findings(item, "film", film, chapter_times=["00:00", "04:00"])}
    assert "chapters_changed" not in {f["type"] for f in V.metadata_findings(item, "film", film, chapter_times=["00:00", "03:00"])}


def test_a_sentence_that_tells_what_comes_later_is_an_error(world, monkeypatch):
    monkeypatch.setattr(V, "reveal_issues", lambda story, cutoff, text: [{"quote": "He confessed."}] if "confessed" in text else [])
    limit = CFG.intelligence.metadata_limits["youtube/short"]
    item, _ = V.tidy({"title": "t", "text": "He confessed. Then.", "hashtags": [], "keywords": [], "chapters": []})
    found = V.metadata_findings(item, "short", limit, story=object(), cutoff=2)
    assert [f["type"] for f in found if f["severity"] == "error"] == ["spoiler"]


def test_the_music_credit_is_added_by_the_app_to_every_short_and_never_to_a_film(world):
    from app.core.ai_config import ai_config

    generate(world, Model())
    credit = ai_config.shorts_host_led.music_attribution_text["fa"]
    assert all(pub(world, p, "short", "C1").caption.count(credit) == 1 for p in ("youtube", "facebook", "instagram", "tiktok"))
    assert credit not in pub(world, "youtube", "film", f"V{world.film.id}").caption


# --- what is never overwritten -------------------------------------------------------------------------------------------------------------------------------------

def test_metadata_written_by_the_owner_is_kept_and_the_ai_version_is_offered(world):
    generate(world, Model())
    short = pub(world, "instagram", "short", "C2")
    world.registry.update(short.id, caption="کپشن خودم", metadata={"title": "", "hashtags": ["#خودم"]})
    assert pub(world, "instagram", "short", "C2").metadata_source == "owner"
    run = generate(world, Model(), force=True)
    kept = pub(world, "instagram", "short", "C2")
    assert kept.caption == "کپشن خودم" and json.loads(kept.metadata_json)["hashtags"] == ["#خودم"]
    assert any(k["id"] == kept.id and "edited by you" in k["why"] for k in json.loads(run.applied_json)["kept"])
    view = world.registry.view(kept, world.registry.suggestions(world.case.id, "fa"))
    assert view["suggestion"]["text"].startswith("سطر اول جذاب برای C2")
    used = world.registry.use_suggestion(kept.id)
    assert used.metadata_source == "ai" and used.caption.startswith("سطر اول جذاب برای C2")


def test_editing_only_the_caption_makes_the_text_yours(world):
    generate(world, Model())
    target = pub(world, "facebook", "short", "C1")
    assert target.metadata_source == "ai"
    world.registry.update(target.id, caption="کپشن من")
    assert pub(world, "facebook", "short", "C1").metadata_source == "owner"
    generate(world, Model(), force=True)
    assert pub(world, "facebook", "short", "C1").caption == "کپشن من"


def test_an_approved_publication_is_not_changed_behind_its_approval(world):
    generate(world, Model())
    short = pub(world, "youtube", "short", "C1")
    approve_both(world, short)
    before = (pub(world, "youtube", "short", "C1").caption, pub(world, "youtube", "short", "C1").scheduled_at)
    run = generate(world, Model(writer=lambda v: {"platform": v["platform"], "items": [{**i, "text": "متن کاملاً تازه"} for i in Model.default_metadata(v)["items"]], "warnings": []}), force=True)
    after = pub(world, "youtube", "short", "C1")
    assert after.status == "publication_approved" and (after.caption, after.scheduled_at) == before
    assert any(k["id"] == after.id and "publication approved" in k["why"] for k in json.loads(run.applied_json)["kept"])
    assert pub(world, "youtube", "short", "C2").caption.startswith("متن کاملاً تازه")


def test_an_edit_of_the_metadata_voids_an_approval(world):
    generate(world, Model())
    short = pub(world, "tiktok", "short", "C3")
    approve_both(world, short)
    world.registry.update(short.id, metadata={"hashtags": ["#تازه"]})
    again = pub(world, "tiktok", "short", "C3")
    assert again.status == "voided" and again.metadata_source == "owner" and events(world, "approval_voided")


def test_reordering_moves_the_slots_with_the_order_and_voids_what_it_changes(world):
    generate(world, Model())
    slots_before = sorted(pub(world, "instagram", "short", k).scheduled_at for k in ("C1", "C2", "C3", "C4", "C5"))
    approve_both(world, pub(world, "instagram", "short", "C5"))
    world.registry.reorder(world.case.id, "fa", "instagram", ["C1", "C2", "C3", "C4", "C5"])
    assert [pub(world, "instagram", "short", k).sequence for k in ("C1", "C2", "C3", "C4", "C5")] == [1, 2, 3, 4, 5]
    assert sorted(pub(world, "instagram", "short", k).scheduled_at for k in ("C1", "C2", "C3", "C4", "C5")) == slots_before
    assert pub(world, "instagram", "short", "C1").scheduled_at == slots_before[0] and pub(world, "instagram", "short", "C5").status == "voided"
    with pytest.raises(PublishingRefused, match="every Short"):
        world.registry.reorder(world.case.id, "fa", "instagram", ["C1", "C2"])


def test_the_calendar_resolver_keeps_the_rules(world):
    cfg = CFG.intelligence.scheduling
    start = date(2031, 1, 6)
    items = [{"destination": "youtube/film", "asset": "film", "date_offset_days": 0, "local_time": "03:00", "reason": "r"},
             *({"destination": "youtube/short", "asset": k, "date_offset_days": 0, "local_time": "25:99", "reason": "r"} for k in ("C1", "C2", "C3"))]
    slots = SCH.resolve(items, plan_start=start, fallback_time="20:30", settings=cfg, config=CFG, order={"youtube": ["C3", "C1", "C2"]},
                        destinations={"youtube/film": ["film"], "youtube/short": ["C1", "C2", "C3"]}, existing_films={"youtube": [start]})
    by = {(s.destination, s.asset): s for s in slots}
    film = by[("youtube/film", "film")]
    assert film.day == start + timedelta(days=1) and film.local_time == "08:00" and "spacing" in film.moved and "03:00" in film.moved       # spaced from the other film, time inside the window
    shorts = sorted((by[("youtube/short", k)] for k in ("C3", "C1", "C2")), key=lambda s: s.day)
    assert [s.asset for s in shorts] == ["C3", "C1", "C2"] and all(s.day > film.day for s in shorts) and len({s.day for s in shorts}) == 3
    assert all(s.local_time == "20:30" for s in shorts)


# --- one-click approval -----------------------------------------------------------------------------------------------------------------------------------------

def test_the_one_click_approval_approves_what_may_be_approved_and_names_what_it_leaves_out(world):
    generate(world, Model())
    preview = world.registry.plan_approval(world.case.id, "fa")
    assert preview["stopped"] is False and not preview["already_approved"]
    approving = {(r["platform"], r["kind"], r["asset_key"]) for r in preview["approve"]}
    assert len(approving) == 20 and all(kind == "short" for _, kind, _ in approving)
    films = [r for r in preview["excluded"] if r["kind"] == "film"]
    assert len(films) == 2 and all(r["reasons"] and not any("profile" in reason for reason in r["reasons"]) for r in films)        # a film stays out for its own reasons, never for its render profile
    assert any("platform requires a person" in note for r in preview["approve"] if r["platform"] == "tiktok" for note in r["notes"])
    assert world.db.query(Publication).filter_by(case_id=world.case.id, status="draft").count() == 22                      # a preview changes nothing
    report = world.registry.approve_plan(world.case.id, "fa")
    assert len(report["approved"]) == 20 and len(report["excluded"]) == 2
    assert {p.status for p in world.db.query(Publication).filter_by(case_id=world.case.id, kind="short").all()} == {"publication_approved"}
    assert {p.status for p in world.db.query(Publication).filter_by(case_id=world.case.id, kind="film").all()} == {"draft"}
    assert len(events(world, "content_approved")) == 20 and len(events(world, "publication_approved")) == 20 and len(events(world, "plan_approved")) == 1
    again = world.registry.plan_approval(world.case.id, "fa")
    assert len(again["already_approved"]) == 20 and not again["approve"]


def test_the_one_click_approval_cannot_bypass_a_blocker_or_a_missing_piece(world, monkeypatch):
    meet_every_film_requirement(world, monkeypatch)
    generate(world, Model())
    from test_publication_safeguards import remove_cover, set_cover

    remove_cover(world, "C1")                                                              # no cover
    set_cover(world, "C2", image="wrong")                                                  # an unusable cover
    pub(world, "youtube", "short", "C3").scheduled_at = None                              # no time
    world.db.commit()
    pub(world, "tiktok", "short", "C4").metadata_source = "default"                       # no metadata
    world.db.commit()
    excluded = {(r["platform"], r["asset_key"]): r["reasons"] for r in world.registry.plan_approval(world.case.id, "fa")["excluded"]}
    assert any("cover is not approved" in r for r in excluded[("youtube", "C1")]) and any("wrong shape" in r for r in excluded[("instagram", "C2")])
    assert any("No publication time" in r for r in excluded[("youtube", "C3")]) and any("no platform metadata" in r for r in excluded[("tiktok", "C4")])
    assert ("youtube", f"V{world.film.id}") not in excluded                                  # the film meets every rule now
    report = world.registry.approve_plan(world.case.id, "fa")
    left = {(r["platform"], r["asset_key"]) for r in report["excluded"]}
    assert {("youtube", "C1"), ("instagram", "C2"), ("youtube", "C3"), ("tiktok", "C4")} <= left
    assert pub(world, "youtube", "short", "C1").status == "draft" and pub(world, "youtube", "film", f"V{world.film.id}").status == "publication_approved"


def test_the_emergency_stop_blocks_generating_and_the_one_click_approval(world):
    from app.services import case_control

    generate(world, Model())
    case_control.set_global_stop(world.db, True, "test")
    try:
        assert world.registry.plan_approval(world.case.id, "fa")["stopped"] is True
        with pytest.raises(PublishingRefused, match="Emergency Stop is active"):
            world.registry.approve_plan(world.case.id, "fa")
        assert world.db.query(Publication).filter_by(case_id=world.case.id, status="publication_approved").count() == 0
        with pytest.raises(S.PlanRunError, match="Emergency Stop"):
            S.start(world.db, world.registry, world.case, "fa", force=True)
    finally:
        case_control.set_global_stop(world.db, False, "test")


def test_a_run_is_stopped_by_the_emergency_stop_before_a_paid_call_and_records_why(world):
    from app.services import case_control

    run = S.start(world.db, world.registry, world.case, "fa")
    model = Model()
    case_control.set_global_stop(world.db, True, "test")
    try:
        asyncio.run(S.execute(run.id, gen=model))
    finally:
        case_control.set_global_stop(world.db, False, "test")
    world.db.expire_all()
    run = world.db.get(PublishingPlanRun, run.id)
    assert run.status == "failed" and model.calls == [] and "Stopped" in run.error


# --- Monitoring -----------------------------------------------------------------------------------------------------------------------------------------------------

def test_monitoring_shows_a_running_a_failed_and_an_interrupted_run_once_each(world, client):
    from app.publishing.monitoring import publishing_issues

    run = S.start(world.db, world.registry, world.case, "fa")
    running = [i for i in publishing_issues(world.db, world.case.id) if "AI publishing plan" in i.title]
    assert [(i.severity, i.title) for i in running] == [("info", "The AI publishing plan (fa) is being made")]
    run.status, run.error = "failed", "ProviderError: the model refused"
    world.db.commit()
    failed = [i for i in publishing_issues(world.db, world.case.id) if "AI publishing plan" in i.title]
    assert [(i.severity, i.title, i.detail) for i in failed] == [("problem", "The AI publishing plan (fa) did not finish", "ProviderError: the model refused")]
    api = [i for i in client.get(f"/api/cases/{world.case.id}/monitoring").json()["issues"] if "AI publishing plan" in i["title"]]
    assert len(api) == 1 and api[0]["actions"][0]["target"] == f"/cases/{world.case.id}?tab=Publishing&language=fa"
    app_wide = [i for i in client.get("/api/monitoring").json()["issues"] if "AI publishing plan" in i["title"] and str(world.case.canonical_title) in i["title"]]
    assert len(app_wide) == 1
    done = generate(world, Model(), force=True)
    assert done.status == "done" and [i for i in publishing_issues(world.db, world.case.id) if "AI publishing plan" in i.title] == []      # a newer finished run ends the alert
    stale = S.start(world.db, world.registry, world.case, "fa", force=True)
    stale.updated_at = stale.updated_at - timedelta(seconds=CFG.intelligence.generation.running_timeout_seconds + 5)
    world.db.commit()
    interrupted = [i for i in publishing_issues(world.db, world.case.id) if "AI publishing plan" in i.title]
    assert [i.severity for i in interrupted] == ["problem"] and "stopped reporting" in interrupted[0].detail


# --- the API ---------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_api_generate_route_starts_one_run_and_refuses_the_rest(world, client, monkeypatch):
    started = []
    monkeypatch.setattr("app.publishing.intelligence.api.spawn", lambda coro, name=None: started.append(coro) or coro.close())
    base = f"/api/cases/{world.case.id}/publishing/fa/ai"
    first = client.post(f"{base}/generate", json={})
    assert first.status_code == 200 and first.json()["status"] == "running" and len(started) == 1
    again = client.post(f"{base}/generate", json={"force": True})
    assert again.status_code == 409 and "already being generated" in again.json()["detail"]["message"]
    assert client.post(f"/api/cases/{world.case.id}/publishing/en/ai/generate", json={}).status_code == 409
    run_id = first.json()["id"]
    assert client.get(f"{base}/runs/{run_id}").json()["progress"][0]["step"] == "analysis"
    assert client.get(f"{base}/runs/999999").status_code == 404


def test_the_api_end_to_end_with_a_scripted_model(world, client):
    generate(world, Model())
    base = f"/api/cases/{world.case.id}/publishing/fa"
    studio = client.get(base).json()
    instagram = next(p for a in studio["assets"] if a["key"] == "C1" for p in a["publications"] if p["platform"] == "instagram")
    assert instagram["metadata_source"] == "ai" and instagram["metadata"]["hashtags"] and instagram["limit"]["text_max"] == 2200 and instagram["sequence"] == 5
    assert studio["assets"][1]["preview_url"] == f"/api/cases/{world.case.id}/short-clips/fa/C1/video" and studio["assets"][1]["cover_url"].endswith("/file?row=" + studio["assets"][1]["cover_url"].split("=")[-1])
    edited = client.patch(f"/api/publications/{instagram['id']}", json={"caption": "کپشن ویرایش‌شده", "metadata": {"hashtags": ["#یک", "#دو"]}}).json()
    assert edited["caption"] == "کپشن ویرایش‌شده" and edited["metadata"]["hashtags"] == ["#یک", "#دو"] and edited["metadata_source"] == "owner"
    order = ["C1", "C2", "C3", "C4", "C5"]
    reordered = client.post(f"{base}/order", json={"platform": "tiktok", "order": order}).json()
    tiktok = {p["asset_key"]: p["sequence"] for a in reordered["assets"] for p in a["publications"] if p["platform"] == "tiktok" and p["kind"] == "short"}
    assert tiktok == {"C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5} and client.post(f"{base}/order", json={"platform": "tiktok", "order": ["C1"]}).status_code == 409
    preview = client.get(f"{base}/approve-plan").json()
    assert len(preview["approve"]) == 20 and len(preview["excluded"]) == 2
    done = client.post(f"{base}/approve-plan").json()
    assert len(done["approved"]) == 20 and all(r["reasons"] for r in done["excluded"])


def test_the_api_uses_a_suggestion_and_refuses_when_there_is_none(world, client):
    planned(world)
    target = pub(world, "youtube", "short", "C1")
    refused = client.post(f"/api/publications/{target.id}/use-suggestion")
    assert refused.status_code == 409 and "no AI suggestion" in refused.json()["detail"]["message"]
    generate(world, Model())
    target = pub(world, "youtube", "short", "C1")
    client.patch(f"/api/publications/{target.id}", json={"caption": "من نوشتم"})
    used = client.post(f"/api/publications/{target.id}/use-suggestion").json()
    assert used["caption"].startswith("سطر اول جذاب برای C1") and used["metadata_source"] == "ai"


def test_the_planner_and_the_writer_are_two_distinct_roles_on_the_model_that_kept_to_json():
    from app.core.ai_config import ai_config

    assert ai_config.alias_for("publishing_planner") == "premium" and ai_config.alias_for("publishing_metadata_writer") == "premium"
    assert ai_config.agents["publishing_planner"].prompt == "publishing/planner" and ai_config.agents["publishing_metadata_writer"].prompt == "publishing/metadata"


def test_the_cost_of_a_run_is_the_ledgers_failed_calls_included(world):
    from app.db.models import ProviderCall
    from app.utils import utc_now

    started = utc_now() - timedelta(minutes=1)
    runs = [AgentRun(case_id=world.case.id, agent_name=name, status=status, model="m", input_tokens=1000 * (i + 1), output_tokens=100 * (i + 1))
            for i, (name, status) in enumerate([("Publishing planner (fa)", "completed"), ("Publishing metadata writer (fa, youtube)", "failed")])]
    world.db.add_all(runs)
    world.db.flush()
    for run, eur in zip(runs, (0.02, 0.2), strict=True):
        world.db.add(ProviderCall(host="budget-ledger", method="POST", path=f"/{run.agent_name}", status=200, ok=True, case_id=world.case.id, cost_eur=eur, tokens_in=run.input_tokens))
    world.db.add(ProviderCall(host="budget-ledger", method="POST", path="/Some other agent", status=200, ok=True, case_id=world.case.id, cost_eur=9.0, tokens_in=1))      # not this run's
    world.db.commit()
    usage = S.usage_of(world.db, [r.id for r in runs], world.case.id, started)
    assert usage["calls"] == 2 and usage["failed_calls"] == 1 and usage["priced_calls"] == 2 and usage["cost_eur"] == 0.22
    assert [d["cost_eur"] for d in usage["detail"]] == [0.02, 0.2] and usage["cost_usd"] > 0
    world.db.query(ProviderCall).filter_by(case_id=world.case.id).delete()
    world.db.query(AgentRun).filter_by(case_id=world.case.id).delete()
    world.db.commit()


def test_the_estimate_follows_the_measured_size_of_persian_requests(world):
    planned(world)
    estimate = S.state(world.db, world.registry, world.case, "fa")["estimate"]
    assert 0.1 < estimate["eur_low"] < estimate["eur_high"] < 3 and "repair" in estimate["basis"]


def test_the_planner_request_names_the_exact_output_keys():
    """A live run showed a model inventing its own shape when only the prompt file described the keys: the request itself repeats them."""
    from app.publishing.intelligence.inputs import PLANNER_TASK
    for key in ("`clips`", "`sequences`", "`schedule`", "`routing`", "`warnings`", "date_offset_days", "follower_conversion"):
        assert key in PLANNER_TASK


def test_persian_text_without_half_spaces_is_sent_back_and_with_them_passes():
    """A live plan stored 261 Persian fields with no half-space at all («روایتهای»): the generation context now flags it; an owner's edit (no context) is never blocked by it."""
    limit = CFG.intelligence.metadata_limits["instagram/short"]
    body = "این یک جمله است که درباره پرونده و روایتهای گوناگون آن شب نوشته شده و باید نیمفاصله داشته باشد " * 2
    item = {"title": "", "text": body, "hashtags": [], "keywords": [], "chapters": [], "cta": "", "routing_note": "", "facts_used": []}
    kinds = lambda i, **c: {f["type"] for f in V.metadata_findings(i, "short", limit, **c)}  # noqa: E731
    context = {"known_words": set(), "fact_ids": set(), "clip_keys": set()}
    assert "no_half_space" in kinds(item, **context)
    assert "no_half_space" not in kinds({**item, "text": body.replace("روایتهای", "روایت‌های")}, **context)
    assert "no_half_space" not in kinds(item)


def test_the_writer_gets_the_languages_top_shorts_as_examples_and_never_more_than_configured():
    """The collection of top-viewed Shorts is third-party text outside the repository: the writer reads at most `examples_per_call` of the language's file, captions cut; no file, no examples."""
    from app.publishing.intelligence.examples import ROOT, examples_for
    settings = CFG.intelligence.generation.model_copy(update={"examples_dir": "tests/_examples_tmp", "examples_per_call": 2, "example_caption_chars": 5})
    folder = ROOT / settings.examples_dir
    folder.mkdir(parents=True, exist_ok=True)
    try:
        (folder / "fa.json").write_text(json.dumps({"examples": [{"title": f"عنوان {n}", "caption": "یک توضیح بلند", "views": 100 - n} for n in range(5)]}, ensure_ascii=False), encoding="utf-8")
        got = examples_for("fa", settings)
        assert [e["title"] for e in got] == ["عنوان 0", "عنوان 1"] and got[0]["caption"] == "یک تو" and got[0]["views"] == 100
        assert examples_for("de", settings) == []
        assert examples_for("fa", settings.model_copy(update={"examples_per_call": 0})) == []
        (folder / "fa.json").write_text("not json", encoding="utf-8")
        assert examples_for("fa", settings) == []
    finally:
        for f in folder.glob("*.json"):
            f.unlink()
        folder.rmdir()


def test_the_metadata_request_carries_the_examples_and_the_prompt_teaches_the_hook(world):
    """Weak titles were the complaint: the request now holds the language's top Shorts, and the writer's instructions name the hook craft and how to use (and not to copy) the examples."""
    from app.core.prompts import prompt
    model = Model()
    generate(world, model)
    requests = [data for role, data in model.calls if role == "publishing_metadata_writer"]
    assert requests and all("examples" in r for r in requests)
    text = prompt("publishing/metadata")
    for needle in ("## Hook craft", "`examples`", "Never copy a title", "open loop", "no spoiler"):
        assert needle in text


# --- the hook critic -------------------------------------------------------------------------------------------------------------------------------------------------

def _scripted_critic(weak_assets, *, then_strong=True):
    """A critic that finds `weak_assets` weak on its first look at each platform and strong on the second."""
    seen: dict[str, int] = {}

    def critic(view):
        n = seen[view["platform"]] = seen.get(view["platform"], 0) + 1
        return {"items": [{"asset": i["asset"], "stakes": 3 if i["asset"] in weak_assets and (n == 1 or not then_strong) else 8, "gap": 8, "concrete": 8, "natural": 8, "risk": "none",
                           "direction": "Lead with the open console and ask where the pistol is." if i["asset"] in weak_assets else ""} for i in view["items"]]}
    return critic


def test_a_weak_hook_is_sent_back_once_with_the_critics_direction_and_the_rewrite_is_kept(world):
    """The owner's complaint: weak hooks. The critic's low score (not its words) sends exactly that Short back; the writer sees the direction; the second answer replaces the first."""
    first_titles: list[str] = []
    writer_calls: list[dict] = []

    def writer(view):
        writer_calls.append(view)
        answer = Model.default_metadata(view)
        for item in answer["items"]:
            item["title"] = f"{item['title']} v{len(writer_calls)}" if item["title"] else ""
        return answer

    model = Model(writer=writer, critic=_scripted_critic({"C2"}))
    run = generate(world, model)
    assert run.status == "done"
    youtube = [c for c in writer_calls if c["platform"] == "youtube"]
    assert len(youtube) == 2 and "problems_to_fix" not in youtube[0] and any("C2" in p and "stakes" in p and "open console" in p for p in youtube[1]["problems_to_fix"])
    assert not any("C1" in p for p in youtube[1]["problems_to_fix"])
    result = json.loads(run.result_json)
    assert result["metadata"]["youtube/short/C2"]["item"]["title"].endswith("v2") and result["unresolved"] == {}
    assert first_titles == []


def test_a_hook_still_weak_after_the_repair_stays_visible_as_a_warning_and_does_not_block(world):
    model = Model(critic=_scripted_critic({"C2"}, then_strong=False))
    run = generate(world, model)
    result = json.loads(run.result_json)
    findings = result["metadata"]["youtube/short/C2"]["findings"]
    assert any(f["type"] == "weak_hook" and f["severity"] == "warning" for f in findings)
    assert any("hook is weak" in p for p in result["unresolved"]["youtube"])
    assert not [f for f in findings if f["severity"] == "error"]


def test_the_scores_decide_not_the_critics_word_and_an_unreadable_answer_judges_nothing(world):
    """A critic that says 'good' with a low score is overruled; a critic whose answer is not scores judges nothing (the writer's text stands)."""
    def lying(view):
        return {"items": [{"asset": i["asset"], "verdict": "pass", "stakes": 2, "gap": 9, "concrete": 9, "natural": 9, "risk": "none", "direction": "x"} for i in view["items"]]}
    model = Model(critic=lying)
    generate(world, model)
    assert sum(1 for r, d in model.calls if r == "publishing_metadata_writer" and d["platform"] == "youtube") == 3      # first look weak, repaired twice, still weak: kept as it stands
    silent = Model(critic=lambda view: {"nonsense": True})
    run = generate(world, silent, force=True)
    assert sum(1 for r, d in silent.calls if r == "publishing_metadata_writer" and d["platform"] == "youtube") == 1 and run.status == "done"


def test_an_accusation_or_a_spoiler_risk_sends_the_hook_back_even_with_good_scores(world):
    def risky(view):
        return {"items": [{"asset": i["asset"], "stakes": 9, "gap": 9, "concrete": 9, "natural": 9, "risk": "names the husband as the killer" if i["asset"] == "C1" else "none", "direction": ""} for i in view["items"]]}
    model = Model(critic=risky)
    generate(world, model)
    youtube = [d for r, d in model.calls if r == "publishing_metadata_writer" and d["platform"] == "youtube"]
    assert any("names the husband" in p for p in youtube[1]["problems_to_fix"])


def test_the_critic_is_off_when_the_threshold_is_zero_and_is_never_asked_about_films(world, monkeypatch):
    model = Model()
    generate(world, model)
    asked = [d for r, d in model.calls if r == "publishing_hook_critic"]
    assert asked and all(i["asset"] != "film" for d in asked for i in d["items"]) and all(d["examples"] is not None for d in asked)
    off = CFG.intelligence.generation.model_copy(update={"hook_min_score": 0})
    monkeypatch.setattr(world.registry.cfg.intelligence, "generation", off)
    silent = Model()
    generate(world, silent, force=True)
    assert not [1 for r, _ in silent.calls if r == "publishing_hook_critic"]


def test_the_critic_is_a_role_with_its_own_prompt_that_names_the_three_scores():
    from app.core.ai_config import ai_config
    from app.core.prompts import prompt
    assert ai_config.alias_for("publishing_hook_critic") == "premium" and ai_config.agents["publishing_hook_critic"].prompt == "publishing/hook_critic"
    text = prompt("publishing/hook_critic")
    for needle in ("`stakes`", "`gap`", "`concrete`", "`direction`", "Calibration"):
        assert needle in text


def test_a_hook_on_a_platform_whose_title_is_the_caption_is_the_first_line_when_no_title_was_written():
    from app.publishing.intelligence.inputs import hook_of
    assert hook_of({"title": "", "text": "\n  خط اول\nخط دوم"}, 2200) == ("first_line", "خط اول")
    assert hook_of({"title": "عنوان", "text": "متن"}, 100) == ("title", "عنوان")
    assert hook_of({"title": "", "text": "متن"}, 0) == ("first_line", "متن")


def test_the_writers_request_itself_repeats_the_persian_half_space_rule():
    from app.publishing.intelligence.inputs import WRITER_TASK
    assert "U+200C" in WRITER_TASK


def test_a_persian_text_without_half_spaces_is_rewritten_from_scratch_not_patched(world):
    """Live, Facebook returned the same unspelled text on every repair because it saw its earlier answer: the repair request leaves `previous` out for this one problem."""
    requests: list[dict] = []

    def writer(view):
        requests.append(view)
        answer = Model.default_metadata(view)
        if view["platform"] == "facebook" and len([r for r in requests if r["platform"] == "facebook"]) == 1:
            for item in answer["items"]:
                item["text"] = "این یک جمله است که درباره پرونده و روایتهای گوناگون آن شب نوشته شده و باید نیمفاصله داشته باشد " * 2
        return answer

    generate(world, Model(writer=writer))
    facebook = [r for r in requests if r["platform"] == "facebook"]
    assert len(facebook) == 2 and "problems_to_fix" in facebook[1] and "previous" not in facebook[1]


UNSPELLED = "این یک جمله است که درباره پرونده و روایتهای گوناگون آن شب نوشته شده و باید نیمفاصله داشته باشد " * 2


def _unspelled_facebook(view):
    answer = Model.default_metadata(view)
    if view["platform"] == "facebook":
        for item in answer["items"]:
            item["text"] = UNSPELLED
    return answer


def test_texts_still_without_half_spaces_after_the_repairs_go_through_the_orthography_fixer(world):
    """Live, Facebook came back without half-spaces on every round: one small call now adds them, and only them, and the findings are judged again."""
    def fixer(data):
        return {"fields": {k: v.replace("روایتهای", "روایت\u200cهای").replace("نیمفاصله", "نیم\u200cفاصله") for k, v in data["fields"].items()}}
    model = Model(writer=_unspelled_facebook, fixer=fixer)
    run = generate(world, model)
    result = json.loads(run.result_json)
    assert [r for r, _ in model.calls].count("publishing_orthography_fixer") == 1 and "facebook" not in result["unresolved"]
    item = result["metadata"]["facebook/short/C1"]
    assert "روایت\u200cهای" in item["item"]["text"] and not any(f["type"] == "no_half_space" for f in item["findings"]) and "half-spaces restored" in item["fixes"][-1]


def test_an_orthography_answer_that_changes_a_word_is_refused_and_the_finding_stays(world):
    model = Model(writer=_unspelled_facebook, fixer=lambda data: {"fields": {k: v.replace("روایتهای", "کلمهٔ دیگر") for k, v in data["fields"].items()}})
    result = json.loads(generate(world, model).result_json)
    item = result["metadata"]["facebook/short/C1"]
    assert item["item"]["text"].startswith(UNSPELLED.strip()) and "کلمهٔ دیگر" not in item["item"]["text"] and any(f["type"] == "no_half_space" for f in item["findings"]) and result["unresolved"]["facebook"]


def test_the_orthography_fixer_is_a_role_with_its_own_prompt():
    from app.core.ai_config import ai_config
    assert ai_config.alias_for("publishing_orthography_fixer") == "premium" and ai_config.agents["publishing_orthography_fixer"].prompt == "publishing/orthography"


def test_an_editor_that_finds_no_place_for_a_half_space_turns_the_block_into_a_warning(world):
    """A past-tense formal text can honestly hold no half-space: the fixer changes nothing, and the check stops blocking (it stays visible)."""
    model = Model(writer=_unspelled_facebook, fixer=lambda data: {"fields": dict(data["fields"])})
    result = json.loads(generate(world, model).result_json)
    item = result["metadata"]["facebook/short/C1"]
    assert [f["severity"] for f in item["findings"] if f["type"] == "no_half_space"] == ["warning"] and "no half-space needed" in item["fixes"][-1] and "facebook" not in result["unresolved"]


# --- the Persian texts go to the proofreader (Sonnet) before they are saved or shown -----------------------------------------------------------------------------

def worded(word: str):
    """A writer that puts one (mis)spelled word into every text."""
    def write(view):
        out = Model.default_metadata(view)
        for item in out["items"]:
            item["text"] = f"{item['text']}\nاین {word} است."
        return out
    return write


def test_the_proofreader_gets_every_text_in_one_call_and_its_corrections_are_saved_everywhere(world, monkeypatch):
    proofreading(monkeypatch)
    model = Model(writer=worded("غلطط"), proofreader=fix_word("غلطط", "غلط"))
    run = generate(world, model)
    calls = [d for r, d in model.calls if r == "publishing_proofreader"]
    assert run.status == "done" and len(calls) == 1                                                     # every text of every film, Short and portal goes in ONE call
    keys = {i["key"] for i in calls[0]["items"]}
    assert len(keys) == 22 and {"youtube/short/C1", "facebook/short/C1", "instagram/short/C1", "tiktok/short/C1", "youtube/film/film"} <= keys
    assert {i["asset"] for i in calls[0]["items"]} == {"film", "C1", "C2", "C3", "C4", "C5"} and calls[0]["language"] == "fa" and "known_words" in calls[0]
    result = json.loads(run.result_json)
    assert result["proofread"]["checked"] == 22 and result["proofread"]["corrected"] == 22 and result["proofread"]["refused"] == {}
    for platform in ("youtube", "facebook", "instagram", "tiktok"):
        assert "غلطط" not in pub(world, platform, "short", "C1").caption and "این غلط است." in pub(world, platform, "short", "C1").caption
    assert any("proofread" in f for f in result["metadata"]["facebook/short/C1"]["fixes"]) and result["metadata"]["facebook/short/C1"]["proofread"][0]["new"] == "غلط"
    assert [s["status"] for s in json.loads(run.progress_json) if s["step"] == "proofread"] == ["done"]


def test_an_unchanged_text_is_not_reported_as_corrected(world, monkeypatch):
    proofreading(monkeypatch)
    result = json.loads(generate(world, Model()).result_json)
    assert result["proofread"] == {"checked": 22, "corrected": 0, "refused": {}} and not any("proofread" in " ".join(v["fixes"]) for v in result["metadata"].values())


@pytest.mark.parametrize("change,why", [
    (lambda i: {"text": "یک متن کاملا دیگر که چیزی از متن اصلی ندارد و حرف دیگری می‌زند."}, "rewritten"),
    (lambda i: {"hashtags": i["hashtags"][:1]}, "lost or gained"),
    (lambda i: {"hashtags": ["پرونده_جنایی", *i["hashtags"][1:]]}, "lost its #"),
    (lambda i: {"text": i["text"] + " investigators"}, "introduce"),
])
def test_an_answer_that_rewrites_loses_a_hashtag_or_adds_an_error_is_refused_and_the_written_text_stays(world, monkeypatch, change, why):
    proofreading(monkeypatch)

    def proof(view):
        out = Model.default_proof(view)
        for item in out["items"]:
            item.update(change(item))
            item["changes"] = [{"field": "text", "old": "a", "new": "b", "why": "spelling"}]
        return out

    run = generate(world, Model(proofreader=proof))
    result = json.loads(run.result_json)
    assert run.status == "done" and result["proofread"]["corrected"] == 0 and len(result["proofread"]["refused"]) == 22
    assert all(why in reason for reason in result["proofread"]["refused"].values()) and "ماینک" not in pub(world, "youtube", "short", "C1").caption


def test_a_changed_credit_line_is_refused(world, monkeypatch):
    from app.core.ai_config import ai_config
    credit = ai_config.shorts_host_led.music_attribution_text["fa"]
    proofreading(monkeypatch)

    def proof(view):
        out = Model.default_proof(view)
        for item in out["items"]:
            item["text"] = item["text"].replace(credit, "موسیقی ساخته شده")
        return out

    result = json.loads(generate(world, Model(proofreader=proof)).result_json)
    short = {k: v for k, v in result["proofread"]["refused"].items() if "/short/" in k}
    assert short and all("credit line" in v for v in short.values())


def test_only_the_languages_that_are_configured_are_proofread(world, monkeypatch):
    model = Model()
    generate(world, model)                                                                         # the fixture switched it off
    assert not [r for r, _ in model.calls if r == "publishing_proofreader"]
    proofreading(monkeypatch, languages=("de",))
    model = Model()
    run = generate(world, model, force=True)
    assert not [r for r, _ in model.calls if r == "publishing_proofreader"] and "proofread" not in {s["step"] for s in json.loads(run.progress_json)}


def test_a_failed_proofreader_call_keeps_the_written_texts_and_the_plan(world, monkeypatch):
    proofreading(monkeypatch)
    run = generate(world, Model(proofreader=lambda view: RuntimeError("the provider is down")))
    result = json.loads(run.result_json)
    assert run.status == "done" and "provider is down" in result["proofread"]["error"] and pub(world, "youtube", "short", "C1").caption
    assert [s["status"] for s in json.loads(run.progress_json) if s["step"] == "proofread"] == ["failed"]


def test_the_proofreader_is_a_role_with_its_own_prompt_and_checks_the_texts_it_is_given():
    from app.core.ai_config import ai_config
    from app.core.prompts import prompt
    assert ai_config.alias_for("publishing_proofreader") == "premium" and ai_config.agents["publishing_proofreader"].prompt == "publishing/proofread"
    text = prompt("publishing/proofread")
    assert "half-space" in text.lower() and "unchanged" in text and "same mistake gets the same correction" in text and CFG.intelligence.generation.proofread_min_similarity <= 1


def test_the_estimate_counts_the_proofreading_calls_for_a_proofread_language(world, monkeypatch):
    inputs = S.build(world.db, world.registry, world.case, "fa")
    without = S.estimate(world.registry, inputs)
    proofreading(monkeypatch)
    with_it = S.estimate(world.registry, inputs)
    assert with_it["calls"] == without["calls"] + 1 and with_it["eur_high"] > without["eur_high"]


def test_the_proofreading_of_the_texts_the_publications_hold_corrects_the_ai_and_the_owner_texts_keeps_their_source_and_leaves_what_is_public(world, client, monkeypatch):
    generate(world, Model(writer=worded("غلطط")))                                                       # the run itself did not proofread (switched off): the mistake is in every text
    proofreading(monkeypatch)
    facebook, instagram = pub(world, "facebook", "short", "C1"), pub(world, "instagram", "short", "C1")
    world.registry.update(facebook.id, caption=pub(world, "facebook", "short", "C1").caption + " و غلطط دیگر")      # an owner's text with the same mistake
    approve_both(world, pub(world, "youtube", "short", "C2"))
    instagram.status = "published"
    world.db.commit()
    model = Model(proofreader=fix_word("غلطط", "غلط"))
    monkeypatch.setattr("app.publishing.intelligence.agents.get_generation_provider", lambda: model)
    answer = client.post(f"/api/cases/{world.case.id}/publishing/fa/proofread")
    assert answer.status_code == 200
    body = answer.json()
    assert body["refused"] == [] and body["checked"] == 21 and len(body["changed"]) == 21 and body["usage"]["calls"] == 1          # one call; the published one is not sent
    assert "غلطط" not in pub(world, "youtube", "short", "C1").caption and "و غلط دیگر" in pub(world, "facebook", "short", "C1").caption
    assert pub(world, "youtube", "short", "C1").metadata_source == "ai" and pub(world, "facebook", "short", "C1").metadata_source == "owner"      # a correction does not make an AI text the owner's
    assert pub(world, "instagram", "short", "C1").status == "published" and "غلطط" in pub(world, "instagram", "short", "C1").caption
    assert pub(world, "youtube", "short", "C2").status == "voided"                                                                    # an edit of an approved text voids its approval
    assert {json.loads(e.context_json)["actor"] for e in events(world, "proofread")} == {"proofreader"}
    assert client.post(f"/api/cases/{world.case.id}/publishing/de/proofread").status_code == 409                                      # not a proofread language


def test_proofreading_for_a_language_that_is_not_configured_is_refused_before_any_call(world, client, monkeypatch):
    model = Model()
    monkeypatch.setattr("app.publishing.intelligence.agents.get_generation_provider", lambda: model)
    answer = client.post(f"/api/cases/{world.case.id}/publishing/fa/proofread")                      # the fixture switched proofreading off
    assert answer.status_code == 409 and model.calls == []
