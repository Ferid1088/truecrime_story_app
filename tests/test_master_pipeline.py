"""Tests for the section-based master pipeline: per-act generation,
surgical grounding repair, structured edit ops, length contracts,
acceptance/rollback, diminishing-return and budget guards.

All generation calls are scripted — no real credits are spent.
"""
import asyncio
import json
import uuid

from app.core.ai_config import ai_config
from app.providers.generation.base import GenerationResult
from app.utils import strip_narration_artifacts


def _mk_case(db):
    from app.db.models import Case
    from app.utils import slugify

    title = f"MP Case {uuid.uuid4().hex[:8]}"
    c = Case(canonical_title=title, slug=slugify(title), language="en")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _add_fact(db, case, claim="F", **kw):
    from app.db.models import Fact

    f = Fact(
        case_id=case.id, claim=claim,
        category=kw.pop("category", "context"),
        confidence=kw.pop("confidence", 0.9), **kw,
    )
    db.add(f)
    db.commit()
    return f


class ScriptGen:
    """Scripted generation provider: deterministic, token/cost reporting."""

    def __init__(self, tokens=0, cost=0.0):
        self.structured = {}   # role -> dict | callable(system, user) -> dict
        self.texts = {}        # role -> str | callable(system, user) -> str
        self.calls = []        # ("text"|"json", role, user)
        self.tokens = tokens
        self.cost = cost

    def is_configured(self):
        return True

    def _result(self, text):
        return GenerationResult(
            text=text, model="m/fake", provider="fake",
            total_tokens=self.tokens or None,
            cost_usd=self.cost or None,
        )

    async def generate_text(self, role, system, user):
        self.calls.append(("text", role, user))
        v = self.texts.get(role, _sized_text)
        out = v(system, user) if callable(v) else v
        return self._result(out)

    async def generate_structured(self, role, system, user):
        self.calls.append(("json", role, user))
        v = self.structured.get(role)
        out = v(system, user) if callable(v) else v
        if out is None:
            out = _default_structured(role)
        return out, self._result("{}")

    def count(self, kind, role):
        return sum(1 for c in self.calls if c[0] == kind and c[1] == role)


def _sized_text(system, user):
    """Default text generator: produce ~the requested word budget."""
    u = json.loads(user)
    n = (
        (u.get("act") or {}).get("target_words")
        or len((u.get("section_text") or "").split())
        or len((u.get("paragraph") or "").split())
        or 400
    )
    n = max(int(n), 5)
    return "narration " + " ".join(f"w{i}" for i in range(n))


def _default_structured(role):
    table = {
        "story_director": {
            "title": "t", "central_question": "q",
            "acts": [
                {"id": "act1", "purpose": "p1", "evidence_ids": []},
                {"id": "act2", "purpose": "p2", "evidence_ids": []},
            ],
        },
        "engagement_critic": {"score": 95, "sections": [], "problems": []},
        "section_critic": {"score": 90, "problems": []},
        "grounding_validator": {
            "grounding_score": 0.95, "supported_claims": [],
            "unsupported_claims": [], "uncertainty_errors": [],
        },
        "consistency_checker": {"violations": []},
    }
    for master_role, base in (
        ("master_story_director", "story_director"),
        ("master_engagement_critic", "engagement_critic"),
    ):
        table[master_role] = table[base]
    return table.get(role, {})


def _pipeline(fake, monkeypatch):
    import app.agents.story as story_mod

    monkeypatch.setattr(story_mod, "get_generation_provider", lambda: fake)
    return story_mod.StoryPipeline()


# ---------------------------------------------------------------------------
# Word budgets / per-act generation
# ---------------------------------------------------------------------------


def test_act_budgets_normalized_to_target():
    from app.agents.story import StoryPipeline

    plan = {"acts": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    StoryPipeline()._normalize_act_budgets(plan, 6000)
    total = sum(a["target_words"] for a in plan["acts"])
    assert abs(total - 6000) <= len(plan["acts"])
    min_words = 6000 * ai_config.master_generation.min_act_budget_share
    assert all(a["target_words"] >= min_words for a in plan["acts"])


def test_budget_rescaling_preserves_proportions():
    from app.agents.story import StoryPipeline

    plan = {"acts": [
        {"id": "a", "target_words": 10},
        {"id": "b", "target_words": 30},
    ]}
    StoryPipeline()._normalize_act_budgets(plan, 4000)
    assert plan["acts"][1]["target_words"] > plan["acts"][0]["target_words"]
    assert abs(
        sum(a["target_words"] for a in plan["acts"]) - 4000
    ) <= 2


def test_acts_generated_separately(db_session, monkeypatch):
    fake = ScriptGen()
    fake.structured["story_director"] = {
        "title": "t", "central_question": "q",
        "acts": [
            {"id": "a1", "purpose": "p", "evidence_ids": ["F001"]},
            {"id": "a2", "purpose": "p", "evidence_ids": []},
            {"id": "a3", "purpose": "p", "evidence_ids": []},
        ],
    }
    pipe = _pipeline(fake, monkeypatch)
    case = _mk_case(db_session)
    _add_fact(db_session, case)

    v = asyncio.run(pipe.run(
        db_session, case, target_minutes=10, language="en",
        tone="t", iterations=1,
    ))
    assert fake.count("text", "writer") == 3  # one call per act
    assert len(v.story_text.split()) > 0


# ---------------------------------------------------------------------------
# Surgical grounding repair
# ---------------------------------------------------------------------------


def test_span_repair_is_local_and_length_preserving(db_session, monkeypatch):
    fake = ScriptGen()
    pipe = _pipeline(fake, monkeypatch)
    case = _mk_case(db_session)
    p1 = " ".join(["alpha"] * 30)
    p2 = " ".join(["beta"] * 30)
    p3 = " ".join(["gamma"] * 30)
    section = {"id": "act1", "text": f"{p1}\n\n{p2}\n\n{p3}"}
    fake.texts["rewriter"] = lambda s, u: " ".join(["fixed"] * 30)
    report = {
        "grounding_score": 0.5,
        "unsupported_claims": [{
            "claim": "unsupported beta claim",
            "exact_text_span": p2[:40],
            "nearest_supported_evidence_ids": [],
        }],
        "uncertainty_errors": [],
    }
    new_text, log = asyncio.run(
        pipe._repair_section_spans(
            db_session, case, section, report, {"facts": []}, "en"
        )
    )
    paras = new_text.split("\n\n")
    assert paras[0] == p1 and paras[2] == p3      # neighbours untouched
    assert "beta" not in paras[1] and "fixed" in paras[1]
    assert abs(len(new_text.split()) - 90) <= 5  # approximate length kept
    assert log[0]["repaired"] is True


def test_span_repair_rejects_length_violation(db_session, monkeypatch):
    fake = ScriptGen()
    fake.texts["rewriter"] = lambda s, u: "tiny"
    pipe = _pipeline(fake, monkeypatch)
    case = _mk_case(db_session)
    para = " ".join(["beta"] * 40)
    section = {"id": "act1", "text": para}
    report = {
        "unsupported_claims": [{"claim": "x", "exact_text_span": para[:30]}],
        "uncertainty_errors": [],
    }
    new_text, log = asyncio.run(
        pipe._repair_section_spans(
            db_session, case, section, report, {"facts": []}, "en"
        )
    )
    assert new_text == para                       # previous text survives
    assert log[0]["rewrite_rejected"] is True
    assert log[0]["reason"] == "length_violation"


def test_span_repair_rejects_too_long(db_session, monkeypatch):
    fake = ScriptGen()
    fake.texts["rewriter"] = lambda s, u: " ".join(["x"] * 200)
    pipe = _pipeline(fake, monkeypatch)
    case = _mk_case(db_session)
    para = " ".join(["beta"] * 40)
    section = {"id": "act1", "text": para}
    report = {
        "unsupported_claims": [{"claim": "x", "exact_text_span": para[:30]}],
        "uncertainty_errors": [],
    }
    new_text, log = asyncio.run(
        pipe._repair_section_spans(
            db_session, case, section, report, {"facts": []}, "en"
        )
    )
    assert new_text == para
    assert log[0]["reason"] == "length_violation"


# ---------------------------------------------------------------------------
# Targeted revision ops
# ---------------------------------------------------------------------------


def _three_sections():
    return [
        {"id": "a1", "text": " ".join(["one"] * 100)},
        {"id": "a2", "text": " ".join(["two"] * 100)},
        {"id": "a3", "text": " ".join(["three"] * 100)},
    ]


def test_only_weak_sections_edited(db_session, monkeypatch):
    fake = ScriptGen()
    fake.texts["rewriter"] = lambda s, u: " ".join(["revised"] * 100)
    pipe = _pipeline(fake, monkeypatch)
    case = _mk_case(db_session)
    sections = _three_sections()
    scores = [
        {"_section_id": "a1", "score": 90},
        {"_section_id": "a2", "score": 20},
        {"_section_id": "a3", "score": 85},
    ]
    critique = {"sections": [{
        "section_id": "a2", "score": 20,
        "problems": [{"type": "REPHRASE", "instruction": "tighten"}],
    }]}
    out, edits, _ = asyncio.run(pipe._apply_revision_ops(
        db_session, case, sections, scores, {"facts": []}, {}, critique, "en"
    ))
    assert out[0]["text"].startswith("one")
    assert out[2]["text"].startswith("three")
    assert out[1]["text"].startswith("revised")
    assert any(e.get("accepted") for e in edits)


def test_add_human_detail_without_evidence_flagged(db_session, monkeypatch):
    fake = ScriptGen()
    pipe = _pipeline(fake, monkeypatch)
    case = _mk_case(db_session)
    sections = _three_sections()
    critique = {"sections": [{
        "section_id": "a2", "score": 20,
        "problems": [{"type": "ADD_HUMAN_DETAIL", "instruction": "add warmth"}],
    }]}
    out, edits, _ = asyncio.run(pipe._apply_revision_ops(
        db_session, case, sections, [], {"facts": [], "human_details": []},
        {}, critique, "en",
    ))
    assert out[1]["text"].startswith("two")  # untouched
    assert edits[0]["insufficient_evidence_for_requested_edit"] is True


def test_grounding_regression_reverts_edit(db_session, monkeypatch):
    def failing_grounding(system, user):
        u = json.loads(user)
        bad = "UNSUPPORTED" in u.get("story", "")
        return {
            "grounding_score": 0.2 if bad else 0.95,
            "unsupported_claims": [{"claim": "bad"}] if bad else [],
            "uncertainty_errors": [],
        }

    fake = ScriptGen()
    fake.structured["grounding_validator"] = failing_grounding
    fake.texts["rewriter"] = lambda s, u: "UNSUPPORTED " + " ".join(["x"] * 99)
    pipe = _pipeline(fake, monkeypatch)
    case = _mk_case(db_session)
    sections = _three_sections()
    critique = {"sections": [{
        "section_id": "a2", "score": 20,
        "problems": [{"type": "REPHRASE", "instruction": "x"}],
    }]}
    out, edits, _ = asyncio.run(pipe._apply_revision_ops(
        db_session, case, sections, [], {"facts": []}, {}, critique, "en"
    ))
    assert out[1]["text"].startswith("two")  # reverted
    assert any(
        e.get("reason") == "grounding_regression" for e in edits
    )


# ---------------------------------------------------------------------------
# Revision-loop acceptance, diminishing returns, budgets
# ---------------------------------------------------------------------------


def _run_pipeline(db_session, monkeypatch, fake, iterations=2, **kw):
    pipe = _pipeline(fake, monkeypatch)
    case = _mk_case(db_session)
    _add_fact(db_session, case)
    v = asyncio.run(pipe.run(
        db_session, case, target_minutes=10, language="en",
        tone="t", iterations=iterations, **kw,
    ))
    return v


def _weak_critic(scores):
    seq = iter(scores)

    def critique(system, user):
        try:
            score = next(seq)
        except StopIteration:
            score = scores[-1]
        return {
            "score": score,
            "sections": [{
                "section_id": "act1", "score": 30,
                "problems": [{"type": "REPHRASE", "instruction": "improve"}],
            }],
            "problems": [],
        }
    return critique


def test_underperforming_candidate_rolled_back(db_session, monkeypatch):
    fake = ScriptGen()
    fake.structured["engagement_critic"] = _weak_critic([40, 42, 95])
    fake.texts["rewriter"] = lambda s, u: (
        "REVISED " + " ".join(["x"] * (len(json.loads(u)["section_text"].split()) - 1))
    )
    v = _run_pipeline(db_session, monkeypatch, fake)
    notes = json.loads(v.critic_notes)
    assert any(
        e.get("candidate_rejected") for e in notes["revision_log"]
    )
    # Rolled back — the destructive edit text is not in the stored story.
    assert "REVISED" not in v.story_text


def test_diminishing_returns_stops_cycles(db_session, monkeypatch):
    monkeypatch.setattr(
        ai_config.master_generation, "max_revision_cycles", 5
    )
    fake = ScriptGen()
    fake.structured["engagement_critic"] = _weak_critic(
        [40, 41, 41, 41, 41, 90]
    )
    v = _run_pipeline(db_session, monkeypatch, fake, iterations=5)
    notes = json.loads(v.critic_notes)
    # two consecutive no-gain evaluations -> loop terminated early,
    # never reaching the high score at the end of the script
    assert len(notes["score_history"]) <= 3


def test_token_budget_exhaustion_stops_edits(db_session, monkeypatch):
    fake = ScriptGen(tokens=500)
    fake.structured["engagement_critic"] = _weak_critic([40, 90])
    monkeypatch.setattr(
        ai_config.cost_control, "max_master_tokens_per_case", 500
    )
    v = _run_pipeline(db_session, monkeypatch, fake)
    notes = json.loads(v.critic_notes)
    assert notes["budget_exhausted"] is True
    assert not any(e.get("accepted") for e in notes["revision_log"])


def test_cost_budget_exhaustion_stops_edits(db_session, monkeypatch):
    fake = ScriptGen(cost=0.50)
    fake.structured["engagement_critic"] = _weak_critic([40, 90])
    monkeypatch.setattr(
        ai_config.cost_control, "max_master_cost_usd", 0.10
    )
    v = _run_pipeline(db_session, monkeypatch, fake)
    notes = json.loads(v.critic_notes)
    assert notes["budget_exhausted"] is True


# ---------------------------------------------------------------------------
# Evidence plan / coverage
# ---------------------------------------------------------------------------


def test_evidence_usage_tracked_in_structure(db_session, monkeypatch):
    fake = ScriptGen()
    fake.structured["story_director"] = {
        "title": "t", "central_question": "q",
        "acts": [
            {"id": "a1", "purpose": "p", "evidence_ids": ["F001"]},
            {"id": "a2", "purpose": "p", "evidence_ids": ["F002"]},
        ],
    }
    pipe = _pipeline(fake, monkeypatch)
    case = _mk_case(db_session)
    _add_fact(db_session, case, "fact one")
    _add_fact(db_session, case, "fact two")
    _add_fact(db_session, case, "fact three")  # unassigned
    v = asyncio.run(pipe.run(
        db_session, case, target_minutes=10, language="en",
        tone="t", iterations=1,
    ))
    struct = json.loads(v.narrative_structure)
    usage = struct["evidence_usage"]
    assert set(usage["used_evidence_ids"]) == {"F001", "F002"}
    assert "F003" in usage["unused_evidence_ids"]
    assert struct["acts"][0]["target_words"] > 0


# ---------------------------------------------------------------------------
# Capacity gate + italic markdown
# ---------------------------------------------------------------------------


def test_insufficient_capacity_blocks_master(client, db_session):
    case = _mk_case(db_session)
    _add_fact(db_session, case)
    r = client.post(
        f"/api/cases/{case.id}/master-story/generate",
        json={"target_minutes": 45, "language": "en"},
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["capacity"]["status"] == "insufficient_for_requested_length"
    assert detail["capacity"]["estimated_supported_minutes"] < 45


def test_capacity_endpoint_reports(client, db_session):
    """Sufficiently deep evidence supports the requested duration: mixed
    full/partial source depth, human + scene + investigation details,
    quotes, a dense timeline and contradictions -> ready.

    Bare fact counts alone must not inflate the estimate (see the
    shallow-evidence counterpart test below)."""
    from app.db.models import Contradiction, Source

    case = _mk_case(db_session)
    for i in range(4):
        db_session.add(
            Source(
                case_id=case.id, title=f"F{i}", url=f"https://e/f{i}",
                source_type="article", content_status="full_text",
                raw_text="text " * 400, source_family=f"fam{i}",
            )
        )
    for i in range(3):
        db_session.add(
            Source(
                case_id=case.id, title=f"P{i}", url=f"https://e/p{i}",
                source_type="article", content_status="partial_text",
                raw_text="text " * 100, source_family=f"pfam{i}",
            )
        )
    # Narrative-value mix across every typed bucket, all dated.
    nvs = ["human_detail", "scene_detail", "investigation", "quote"]
    for i in range(40):
        _add_fact(
            db_session, case, f"fact {i}",
            narrative_value=nvs[i % len(nvs)],
            event_date=f"2000-01-{(i % 28) + 1:02d}",
        )
    for i in range(3):
        db_session.add(
            Contradiction(
                case_id=case.id, topic=f"t{i}",
                description="accounts differ", severity="medium",
            )
        )
    db_session.commit()

    r = client.get(
        f"/api/cases/{case.id}/research/capacity?target_minutes=45"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["estimated_supported_minutes"] >= body["minimum_required_minutes"]
    depth = body["depth"]
    assert depth["full_text_sources"] == 4
    assert depth["partial_text_sources"] == 3
    assert depth["human_details"] > 0
    assert depth["scene_details"] > 0
    assert depth["investigation_details"] > 0
    assert depth["quotes"] > 0
    assert depth["timeline_events"] == 40
    assert depth["contradictions"] == 3
    assert depth["independent_source_families"] == 7


def test_capacity_shallow_evidence_insufficient(client, db_session):
    """Many facts but only shallow evidence (summary-only sources, untyped
    claims, no dates) must NOT be judged ready — volume ≠ depth."""
    from app.db.models import Source

    case = _mk_case(db_session)
    for i in range(10):
        db_session.add(
            Source(
                case_id=case.id, title=f"Shallow{i}", url=f"https://e/s{i}",
                source_type="article", content_status="summary_only",
                publisher=f"pub{i}",
            )
        )
    db_session.commit()
    for i in range(40):
        _add_fact(db_session, case, f"bare fact {i}")

    r = client.get(
        f"/api/cases/{case.id}/research/capacity?target_minutes=45"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "insufficient_for_requested_length"
    assert body["estimated_supported_minutes"] < body["minimum_required_minutes"]
    assert "deep source text" in body["weak_areas"]


def test_italic_bold_markdown_stripped():
    out = strip_narration_artifacts(
        "The *Hesperus* arrived; **three men** were gone. Rate: 3 * 4."
    )
    assert "*Hesperus*" not in out and "Hesperus" in out
    assert "**three men**" not in out and "three men" in out
    assert "3 * 4" in out


# ---------------------------------------------------------------------------
# improve() kind preservation
# ---------------------------------------------------------------------------


def test_improve_preserves_master_kind(db_session, monkeypatch):
    """Regression: improving a master must produce another master version,
    not a 'direct' version that orphans the localization lineage."""
    from app.db.models import StoryVersion

    fake = ScriptGen()
    fake.texts["rewriter"] = lambda s, u: " ".join(["revised"] * 60)
    pipe = _pipeline(fake, monkeypatch)
    case = _mk_case(db_session)
    _add_fact(db_session, case)

    master = StoryVersion(
        case_id=case.id,
        version=1,
        narrative_angle="angle",
        narrative_structure=json.dumps({"sections": []}),
        language="en",
        kind="master",
        # 60 words so the fake rewriter's 60-word candidate stays inside the
        # improve() length contract (±15%).
        story_text="original master text " * 20,
        text_hash="h1",
        engagement_score=40,
        similarity_score=0.1,
        similarity_status="ok",
        status="needs_revision",
        is_best=True,
        critic_notes="{}",
    )
    db_session.add(master)
    db_session.commit()

    new = asyncio.run(
        pipe.improve(db_session, case, master, "tighten act two")
    )
    assert new.kind == "master"
    assert new.version == master.version + 1
    assert new.id != master.id

    # The master line is still queryable through the kind filter.
    masters = (
        db_session.query(StoryVersion)
        .filter(StoryVersion.case_id == case.id, StoryVersion.kind == "master")
        .all()
    )
    assert {m.id for m in masters} == {master.id, new.id}


def test_master_generation_timeout_scales_with_the_film_length(client, db_session, monkeypatch):
    """A 45-minute master takes far longer than the flat 300 s that cancelled
    its first stage; the job gets the pipeline's own target-scaled budget."""
    from app.api import masters
    from app.core.ai_config import ai_config
    from app.services import background_jobs

    async def authorized():
        return {"authorized": True}

    created = {}

    def fake_create_job(db, **kwargs):
        created.update(kwargs)
        return type("Job", (), {"id": 1, "status": "queued"})()

    monkeypatch.setattr(masters.deps, "require_generation_authorized", authorized)
    monkeypatch.setattr(masters, "build_readiness", lambda *a, **k: {
        "master_readiness": {"status": "ready"}})
    monkeypatch.setattr(background_jobs, "create_job", fake_create_job)
    monkeypatch.setattr(background_jobs, "launch", lambda job_id: None)
    case = _mk_case(db_session)
    r = client.post(f"/api/cases/{case.id}/master-story/generate",
                    json={"target_minutes": 45, "language": "fa"})
    assert r.status_code == 200
    budget = ai_config.cost_control.stage_time_budgets["master_story"]
    assert created["timeout_seconds"] == int(
        budget.base_seconds + budget.seconds_per_target_minute * 45)
    assert created["timeout_seconds"] > 300


def test_director_prompt_stays_small_for_a_case_with_thousands_of_facts(monkeypatch):
    """1,700 extracted facts, each repeated in the disputed list and in the
    narrative buckets, made a 1.18M-token director prompt (limit 1M). The
    director plans with one short line per item; the act writers still get
    full detail by id."""
    from app.agents.story import build_evidence_pack, director_view
    from app.db.models import Fact

    monkeypatch.setattr(ai_config.story_quality, "max_evidence_per_story", 5000)
    facts = [
        Fact(id=i, case_id=1, claim=f"Claim {i} " + "word " * 80, confidence=0.9,
             category="evidence", narrative_value=["human_detail", "quote", "scene_detail"][i % 3],
             evidence_strength="secondary", original_claim="Originaltext " * 40,
             original_language="de", supporting_text="verbatim passage " * 30,
             source_ids_json="[1,2]", chunk_ids_json="[3,4]", event_date="1924-05-21")
        for i in range(1, 1701)
    ]
    pack = build_evidence_pack(facts, [], [])
    view = director_view(pack)

    assert len(json.dumps(view, ensure_ascii=False)) < len(json.dumps(pack, ensure_ascii=False)) / 5
    assert len(view["facts"]) == 1700
    item = view["facts"][0]
    assert item["id"] == "F001" and len(item["claim"]) <= 220
    assert not {"original_claim", "supporting_text", "source_ids", "chunk_ids"} & set(item)
    assert view["human_details"][0] == "F003" and view["approved_quotes"][0] == "F001"
    assert pack["facts"][0]["supporting_text"]            # the full pack is untouched


def test_act_pack_keeps_only_the_strongest_evidence_up_to_the_cap(monkeypatch):
    """One act was handed ~800 facts, each in full and again in the narrative
    buckets: an 818k-token writer prompt. An act carries at most
    story_quality.max_evidence_per_act items, the strongest first."""
    from app.agents.story import build_act_pack, build_evidence_pack
    from app.db.models import Fact

    monkeypatch.setattr(ai_config.story_quality, "max_evidence_per_act", 50)
    strengths = ["weak", "secondary", "primary"]
    facts = [Fact(id=i, case_id=1, claim=f"Claim {i}", confidence=0.5 + (i % 5) / 10,
                  category="evidence", narrative_value="human_detail",
                  evidence_strength=strengths[i % 3], source_ids_json="[1]",
                  event_date="1924-05-21")
             for i in range(1, 901)]
    pack = build_evidence_pack(facts, [], [])
    act = build_act_pack(pack, [f["id"] for f in pack["facts"]])

    assert len(act["facts"]) == 50
    assert {f["evidence_strength"] for f in act["facts"]} == {"primary"}
    assert [f["confidence"] for f in act["facts"]] == sorted(
        (f["confidence"] for f in act["facts"]), reverse=True)
    kept = {f["id"] for f in act["facts"]}
    assert {f["id"] for f in act["human_details"]} == kept
    with_timeline = build_act_pack(pack, [f["id"] for f in pack["facts"]] + ["T001"])
    assert len(with_timeline["facts"]) == 50 and len(with_timeline["timeline"]) == 1
    small = build_act_pack(pack, ["F001", "F002"])
    assert [f["id"] for f in small["facts"]] == ["F001", "F002"]


def test_director_is_told_how_many_evidence_ids_an_act_can_use(db_session):
    """Left to itself the director listed ids for most of 1,700 facts in every
    act and its plan overran max_tokens; an act only uses the strongest
    max_evidence_per_act, and the director is told so."""
    from app.agents.story import StoryDirector
    from app.core.prompts import prompt

    case = _mk_case(db_session)
    seen = {}

    class Spy:
        async def generate_structured(self, role, system, user, images=None):
            seen["user"] = json.loads(user)
            return {"acts": []}, GenerationResult(text="{}", model="m", provider="fake")

    director = StoryDirector()
    director.gen = Spy()
    asyncio.run(director.design(case, {"facts": []}, 45, "fa"))
    assert seen["user"]["max_evidence_ids_per_act"] == ai_config.story_quality.max_evidence_per_act
    assert "max_evidence_ids_per_act" in prompt("agents/story/design")


def test_act_without_assigned_evidence_gets_the_strongest_capped_not_the_whole_case(monkeypatch):
    """An act the director assigned no ids fell back to the entire pack: a
    1.18M-token writer prompt for a case with 1,700 facts."""
    from app.agents.story import build_act_pack, build_evidence_pack
    from app.db.models import Fact

    monkeypatch.setattr(ai_config.story_quality, "max_evidence_per_act", 50)
    facts = [Fact(id=i, case_id=1, claim=f"Claim {i}", confidence=0.9, category="evidence",
                  evidence_strength="primary" if i > 850 else "weak", source_ids_json="[1]",
                  event_date="1924-05-21") for i in range(1, 901)]
    pack = build_evidence_pack(facts, [], [])
    for nothing in (None, []):
        act = build_act_pack(pack, nothing)
        assert len(act["facts"]) == 50
        assert {f["evidence_strength"] for f in act["facts"]} == {"primary"}
        assert act["timeline"]
    small = build_evidence_pack(facts[:10], [], [])
    assert build_act_pack(small, None) is small


def test_evidence_pack_holds_a_diverse_selection_of_the_strongest_facts(monkeypatch):
    """Every whole-story check serialized all 1,700 facts (over a million
    tokens, and money for each call). The pack keeps story_quality.
    max_evidence_per_story facts: the strongest, round-robin over fact types,
    numbered in their original order, the same selection every time."""
    from app.agents.story import build_evidence_pack
    from app.db.models import Fact

    monkeypatch.setattr(ai_config.story_quality, "max_evidence_per_story", 40)
    values = ["human_detail", "scene_detail", "quote", "investigation_detail"]
    strengths = ["weak", "secondary", "primary"]
    facts = [Fact(id=i, case_id=1, claim=f"Claim {i}", confidence=0.5 + (i % 5) / 10,
                  category="evidence", narrative_value=values[i % 4],
                  evidence_strength=strengths[(i // 4) % 3], source_ids_json="[1]")
             for i in range(1, 401)]
    pack = build_evidence_pack(facts, [], [])

    assert len(pack["facts"]) == 40
    kinds = [f["narrative_value"] for f in pack["facts"]]
    assert {kinds.count(v) for v in values} == {10}
    assert {f["evidence_strength"] for f in pack["facts"]} == {"primary"}
    claims = [f["claim"] for f in pack["facts"]]
    assert [f["id"] for f in pack["facts"]] == [f"F{i:03d}" for i in range(1, 41)]
    assert claims == sorted(claims, key=lambda c: int(c.split()[1]))     # original order
    assert build_evidence_pack(list(reversed(facts)), [], [])["facts"] == pack["facts"]
    assert len(build_evidence_pack(facts[:10], [], [])["facts"]) == 10
