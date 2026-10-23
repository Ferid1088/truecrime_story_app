import asyncio
import json

import pytest
from conftest import _delete_case_tree
from factories import make_story_version

from app.autopilot import runner as R
from app.autopilot import state as S
from app.core.ai_config import ai_config
from app.db.models import (
    AutopilotRun,
    Case,
    CaseTitleCandidate,
    DocumentaryJob,
    Fact,
    PublishRequest,
    ShortProductionJob,
    Video,
)
from app.services.budget import BudgetService
from app.utils import utc_now

LANGS = ["en", "de"]


class FakePorts(R.Ports):
    def __init__(self, approve=LANGS, titles=7):
        self.calls: list[str] = []
        self.approve, self.titles = approve, titles

    async def authorize(self):
        self.calls.append("authorize")

    async def research(self, db, case, check_cancel):
        self.calls.append("research")
        db.add(Fact(case_id=case.id, claim="A fact."))
        db.commit()

    async def write_texts(self, db, case, languages):
        self.calls.append("texts")
        for lang in self.approve:
            make_story_version(db, case, kind="final", language=lang, approved_at=utc_now(),
                               approved_by="automatic")
        return {"languages": [{"language": lang, "status": "done"} for lang in self.approve]}

    async def write_titles(self, db, case, languages):
        self.calls.append(f"titles:{','.join(languages)}")
        for lang in languages:
            for n in range(self.titles):
                db.add(CaseTitleCandidate(case_id=case.id, language=lang, title=f"{lang} {n}",
                                          title_norm=f"{lang}{n}-{case.id}"))
        db.commit()


@pytest.fixture
def case(db_session, monkeypatch):
    monkeypatch.setattr("app.documentary.jobs.launch", lambda job_id: None)
    for stale in db_session.query(Case).filter_by(slug="autopilot-runner-test").all():
        _delete_case_tree(db_session, stale.id)
    c = Case(canonical_title="Autopilot runner", slug="autopilot-runner-test", language="en")
    db_session.add(c)
    db_session.commit()
    yield c
    db_session.expire_all()
    _delete_case_tree(db_session, c.id)


@pytest.fixture
def run(db_session, case):
    row = AutopilotRun(case_id=case.id, languages_json=json.dumps(LANGS),
                       steps_json=json.dumps(S.initial_steps()))
    db_session.add(row)
    db_session.commit()
    return row


def runner(db_session, ports):
    return R.AutopilotRunner(session_factory=lambda: db_session, ports=ports)


def passes(db_session, run, ports, n=1):
    """n advances on ONE loop: the texts task the runner spawns keeps running between passes,
    the way it does on the worker's long-lived loop."""
    r = runner(db_session, ports)

    async def drive():
        for _ in range(n):
            await r.advance(db_session, run)

    asyncio.run(drive())
    db_session.refresh(run)


def open_visual_gate(db_session, run, lang="en"):
    """Record the base language's film job as past its case-level visual searches — the point
    where the other languages' film jobs are released."""
    job = db_session.get(DocumentaryJob, S.film_job_ids(run)[lang])
    stages = json.loads(job.stages_json or "[]")
    for stage in stages:
        if stage["name"] == "visual_gaps":
            stage["status"] = "done"
    job.stages_json = json.dumps(stages)
    db_session.commit()


def finish_films(db_session, run, status="completed"):
    for job_id in S.film_job_ids(run).values():
        db_session.get(DocumentaryJob, job_id).status = status
    db_session.commit()


def test_research_runs_only_when_the_case_has_no_facts(db_session, run):
    ports = FakePorts()
    passes(db_session, run, ports)
    assert ports.calls[:2] == ["authorize", "research"]
    assert S.step_status(run, "research") == "done"
    passes(db_session, run, ports)
    assert ports.calls.count("research") == 1


def test_one_film_job_per_language_is_made_from_the_approved_final_with_the_configured_profile(
        db_session, run, case):
    ports = FakePorts()
    passes(db_session, run, ports)
    assert set(S.film_job_ids(run)) == {"en"}  # de waits for en's visual searches
    open_visual_gate(db_session, run)
    passes(db_session, run, ports)
    ids = S.film_job_ids(run)
    assert set(ids) == set(LANGS)
    for lang, job_id in ids.items():
        job = db_session.get(DocumentaryJob, job_id)
        assert json.loads(job.languages_json) == [lang]
        assert job.mode == "full"
        assert job.render_profile == ai_config.autopilot_settings().render_profile
        assert job.master_version_id is not None and not job.from_zero
    passes(db_session, run, ports)
    assert S.film_job_ids(run) == ids
    assert db_session.query(DocumentaryJob).filter_by(case_id=case.id).count() == 2


def test_a_language_whose_text_is_not_approved_is_listed_and_the_others_still_get_a_film(
        db_session, run):
    ports = FakePorts(approve=["en"])
    passes(db_session, run, ports)
    assert set(S.film_job_ids(run)) == {"en"}
    need = next(n for n in S.needs(run) if n["kind"] == "text_not_approved")
    assert need["language"] == "de" and need["blocking"] is True
    assert run.status == "waiting_owner"


def test_a_stopped_film_is_listed_with_its_reason_and_clears_when_the_job_is_resumed(
        db_session, run):
    ports = FakePorts()
    passes(db_session, run, ports)
    open_visual_gate(db_session, run)
    passes(db_session, run, ports)
    job = db_session.get(DocumentaryJob, S.film_job_ids(run)["de"])
    job.status, job.error = "failed", "the narrator voice was refused"
    db_session.commit()
    passes(db_session, run, ports)
    need = next(n for n in S.needs(run) if n["kind"] == "film_stopped")
    assert need["language"] == "de" and "narrator voice was refused" in need["message"]
    assert run.status == "waiting_owner"
    job.status, job.error = "queued", None
    db_session.commit()
    passes(db_session, run, ports)
    assert not any(n["kind"] == "film_stopped" for n in S.needs(run))
    assert run.status == "running"


def test_titles_are_made_for_languages_that_lack_seven_and_never_confirmed(
        db_session, run, case):
    ports = FakePorts(titles=3)
    passes(db_session, run, ports)
    open_visual_gate(db_session, run)
    passes(db_session, run, ports)
    finish_films(db_session, run)
    passes(db_session, run, ports)
    assert "titles:en,de" in ports.calls
    need = next(n for n in S.needs(run) if n["kind"] == "titles_short")
    assert "3 of 7" in need["message"]
    assert db_session.query(CaseTitleCandidate).filter_by(
        case_id=case.id, status="selected").count() == 0
    assert S.step_status(run, "titles") == "waiting"


def test_a_failed_shorts_item_blocks_and_a_skipped_one_only_informs(db_session, run, case):
    ports = FakePorts()
    passes(db_session, run, ports)
    open_visual_gate(db_session, run)
    passes(db_session, run, ports)
    finish_films(db_session, run)
    job_id = S.film_job_ids(run)["en"]
    passes(db_session, run, ports)
    db_session.add_all([
        ShortProductionJob(unique_key=f"t-{job_id}-a", case_id=case.id, film_job_id=job_id,
                           language="en", stage="script_render", status="failed",
                           error="no render"),
        ShortProductionJob(unique_key=f"t-{job_id}-b", case_id=case.id, film_job_id=job_id,
                           language="en", stage="script_render", status="skipped",
                           error="audit")])
    db_session.commit()
    passes(db_session, run, ports)
    kinds = {n["kind"]: n["blocking"] for n in S.needs(run)}
    assert kinds["short_failed"] is True and kinds["short_skipped"] is False


def test_the_run_completes_with_a_dry_run_queue_and_sends_nothing(db_session, run, case):
    ports = FakePorts()
    passes(db_session, run, ports)
    open_visual_gate(db_session, run)
    passes(db_session, run, ports)
    finish_films(db_session, run)
    for lang, job_id in S.film_job_ids(run).items():
        db_session.add(Video(case_id=case.id, job_id=job_id, language=lang, state="rendered",
                             file_path=f"data/{lang}.mp4"))
    db_session.commit()
    passes(db_session, run, ports)
    for row in db_session.query(ShortProductionJob).filter_by(case_id=case.id):
        row.status = "done"
    db_session.commit()
    passes(db_session, run, ports)
    assert run.status == "completed" and run.completed_at is not None
    assert S.step_status(run, "publish") == "done"
    rows = db_session.query(PublishRequest).filter_by(run_id=run.id).all()
    assert {r.kind for r in rows} == {"film"} and {r.language for r in rows} == set(LANGS)
    assert all(r.status == "dry_run" and json.loads(r.request_json)["sent"] is False
               for r in rows)
    assert {json.loads(r.request_json)["title"] for r in rows} == {"en 0", "de 0"}
    assert any(n["kind"] == "confirm_titles" and not n["blocking"] for n in S.needs(run))


def test_a_film_with_no_approved_avatar_is_kept_out_of_the_publish_queue(db_session, run, case):
    """A3: the autopilot never queues a film for publish without an approved avatar look for
    its language; it only ever asks the owner to approve one, and the other language is
    unaffected."""
    import json as J

    from factories import make_story_version

    from app.db.models import ProductionScript

    ports = FakePorts()
    passes(db_session, run, ports)
    open_visual_gate(db_session, run)
    passes(db_session, run, ports)
    finish_films(db_session, run)
    sv = make_story_version(db_session, case, language="en")
    draft = ProductionScript(case_id=case.id, story_version_id=sv.id, language="en", mode="full",
                             status="composed", duration_seconds=600.0,
                             script_json=J.dumps({"avatar_used": False}))
    db_session.add(draft)
    # the en film passes the O2 outfit gate; the avatar gate is what fires
    from app.documentary import outfits as OF
    OF.select_outfit(db_session, OF.create_outfit(db_session, case.id, "en", "Navy",
                                                outfit_ref="en/blazerJeans"))
    db_session.commit()
    for lang, job_id in S.film_job_ids(run).items():
        db_session.add(Video(case_id=case.id, job_id=job_id, language=lang, state="rendered",
                             file_path=f"data/{lang}.mp4",
                             production_script_id=draft.id if lang == "en" else None))
    db_session.commit()
    passes(db_session, run, ports)
    for row in db_session.query(ShortProductionJob).filter_by(case_id=case.id):
        row.status = "done"
    db_session.commit()
    passes(db_session, run, ports)
    rows = db_session.query(PublishRequest).filter_by(run_id=run.id).all()
    assert {r.language for r in rows if r.kind == "film"} == {"de"}
    need = next(n for n in S.needs(run) if n["kind"] == "approve_avatar")
    assert need["language"] == "en" and need["blocking"] is True


def test_a_film_without_a_chosen_outfit_asks_for_one_and_is_kept_out(db_session, run, case):
    """O2: the final version needs an outfit chosen for its language — a film whose language
    has none is kept out of the publish queue and gets a 'choose outfit' need; the other
    language is unaffected."""
    import json as J

    from factories import make_story_version

    from app.db.models import ProductionScript
    from app.documentary import outfits as OF

    ports = FakePorts()
    passes(db_session, run, ports)
    open_visual_gate(db_session, run)
    passes(db_session, run, ports)
    finish_films(db_session, run)
    sv = make_story_version(db_session, case, language="en")
    draft = ProductionScript(case_id=case.id, story_version_id=sv.id, language="en", mode="full",
                             status="composed", duration_seconds=600.0,
                             script_json=J.dumps({"avatar_used": True}))
    db_session.add(draft)
    # de gets a real chosen outfit; en keeps none
    OF.select_outfit(db_session, OF.create_outfit(db_session, case.id, "de", "Navy",
                                                outfit_ref="de/blazerJeans"))
    db_session.commit()
    for lang, job_id in S.film_job_ids(run).items():
        db_session.add(Video(case_id=case.id, job_id=job_id, language=lang, state="rendered",
                             file_path=f"data/{lang}.mp4",
                             production_script_id=draft.id if lang == "en" else None))
    db_session.commit()
    passes(db_session, run, ports)
    for row in db_session.query(ShortProductionJob).filter_by(case_id=case.id):
        row.status = "done"
    db_session.commit()
    passes(db_session, run, ports)
    need = next(n for n in S.needs(run) if n["kind"] == "choose_outfit")
    assert need["language"] == "en" and need["blocking"] is True
    assert "choose outfit" in need["message"]
    # the de film (no production script → no host gate) is not blocked by the en outfit need
    rows = db_session.query(PublishRequest).filter_by(run_id=run.id).all()
    assert "de" in {r.language for r in rows if r.kind == "film"}
    assert not any(n["language"] == "de" and n["kind"] == "choose_outfit"
                   for n in S.needs(run))


def test_a_film_with_a_youtube_clip_pending_review_asks_the_owner(db_session, run, case):
    """V5: a YouTube clip whose rights still say editorial_review_required is never a silent
    skip — the film waits and a blocking 'review the clips' need names the assets."""
    import json as J

    from factories import make_story_version

    from app.db.models import ProductionScript, VisualAsset
    from app.documentary import outfits as OF

    ports = FakePorts()
    passes(db_session, run, ports)
    open_visual_gate(db_session, run)
    passes(db_session, run, ports)
    finish_films(db_session, run)
    sv = make_story_version(db_session, case, language="en")
    db_session.add(VisualAsset(
        case_id=case.id, asset_code="VIS_YT1", asset_type="video", provider="youtube_clip",
        rights_status="editorial_review_required", verification_status="verified",
        local_path="data/x.mp4"))
    script = J.dumps({"avatar_used": True,
                      "shots": [{"asset_id": "VIS_YT1", "kind": "video"}]})
    draft = ProductionScript(case_id=case.id, story_version_id=sv.id, language="en", mode="full",
                             status="composed", duration_seconds=600.0, script_json=script)
    db_session.add(draft)
    OF.select_outfit(db_session, OF.create_outfit(db_session, case.id, "en", "Navy",
                                                outfit_ref="en/blazerJeans"))
    db_session.commit()
    for lang, job_id in S.film_job_ids(run).items():
        db_session.add(Video(case_id=case.id, job_id=job_id, language=lang, state="rendered",
                             file_path=f"data/{lang}.mp4",
                             production_script_id=draft.id if lang == "en" else None))
    db_session.commit()
    passes(db_session, run, ports)
    for row in db_session.query(ShortProductionJob).filter_by(case_id=case.id):
        row.status = "done"
    db_session.commit()
    passes(db_session, run, ports)
    need = next(n for n in S.needs(run) if n["kind"] == "review_youtube_clips")
    assert need["language"] == "en" and need["blocking"] is True
    assert "VIS_YT1" in need["message"]
    rows = db_session.query(PublishRequest).filter_by(run_id=run.id).all()
    assert "en" not in {r.language for r in rows if r.kind == "film"}


def test_the_case_budget_stops_the_run_before_any_paid_call(db_session, run, case):
    BudgetService(db_session).record(case_id=case.id, stage="spent", model=None, units=1,
                                     cost_eur=ai_config.budget.total_cap_eur + 1)
    ports = FakePorts()
    passes(db_session, run, ports)
    need = next(n for n in S.needs(run) if n["kind"] == "budget")
    assert "budget" in need["message"] and run.status == "waiting_owner"
    assert ports.calls == []


def test_a_cancelled_run_stops_without_doing_more_work(db_session, run):
    run.status = "cancelled"
    db_session.commit()
    ports = FakePorts()
    asyncio.run(R.AutopilotRunner(ports=ports).run(run.id))
    assert ports.calls == []


def test_a_failed_research_is_listed_with_its_reason(db_session, run):
    class Boom(FakePorts):
        async def research(self, db, case, check_cancel):
            raise ValueError("search layer exploded")

    passes(db_session, run, Boom())
    need = next(n for n in S.needs(run) if n["kind"] == "research_failed")
    assert "search layer exploded" in need["message"] and run.status == "waiting_owner"


def test_a_language_left_unapproved_is_not_paid_for_again_on_every_pass(db_session, run):
    ports = FakePorts(approve=["en"])
    passes(db_session, run, ports, n=4)
    assert ports.calls.count("texts") == 1
    need = next(n for n in S.needs(run) if n["kind"] == "text_not_approved")
    assert need["language"] == "de" and run.status == "waiting_owner"


def test_the_reason_a_text_is_not_approved_names_its_open_issue(db_session, run, case):
    ports = FakePorts(approve=["en"])
    notes = {"checks": {"open_issues": [{"severity": "high", "kind": "wrong_detail",
                                         "problem": "The ending states a date as certain."}]}}
    make_story_version(db_session, case, kind="final", language="de", status="open_issues",
                       critic_notes=json.dumps(notes))
    passes(db_session, run, ports)
    need = next(n for n in S.needs(run) if n["kind"] == "text_not_approved")
    assert "The ending states a date as certain." in need["message"]


def test_a_failing_provider_call_is_tried_the_configured_number_of_times_then_left(
        db_session, run):
    class Boom(FakePorts):
        async def research(self, db, case, check_cancel):
            self.calls.append("research")
            raise ValueError("search layer exploded")

    ports = Boom()
    limit = ai_config.autopilot_settings().max_attempts_per_phase
    passes(db_session, run, ports, n=limit + 3)
    assert ports.calls.count("research") == limit
    assert any("exploded" in n["message"] for n in S.needs(run))


def test_a_phase_that_waits_for_the_owner_is_shown_as_waiting(db_session, run):
    passes(db_session, run, FakePorts(approve=["en"]))
    assert S.step_status(run, "texts") == "waiting"


def test_a_text_the_owner_gets_approved_lets_the_autopilot_try_the_other_languages_again(
        db_session, run, case):
    ports = FakePorts(approve=[])
    passes(db_session, run, ports, n=2)
    assert ports.calls.count("texts") == 1
    make_story_version(db_session, case, kind="final", language="en", approved_at=utc_now(),
                       approved_by="owner")
    passes(db_session, run, ports)
    assert ports.calls.count("texts") == 2


def test_the_other_film_jobs_wait_for_the_english_films_visual_searches(db_session, run):
    ports = FakePorts()
    passes(db_session, run, ports)
    assert set(S.film_job_ids(run)) == {"en"}
    detail = S.step_detail(run, "films") or ""
    assert "de wait" in detail and "visual searches" in detail
    open_visual_gate(db_session, run)
    passes(db_session, run, ports)
    assert set(S.film_job_ids(run)) == set(LANGS)


def test_a_stopped_english_film_releases_the_waiting_languages(db_session, run):
    ports = FakePorts()
    passes(db_session, run, ports)
    en = db_session.get(DocumentaryJob, S.film_job_ids(run)["en"])
    en.status, en.error = "failed", "render exploded"
    db_session.commit()
    passes(db_session, run, ports)
    assert set(S.film_job_ids(run)) == set(LANGS)
    need = next(n for n in S.needs(run) if n["kind"] == "film_stopped")
    assert need["language"] == "en"


def test_a_failed_english_text_does_not_hold_the_other_languages_films(db_session, run):
    ports = FakePorts(approve=["de"])
    passes(db_session, run, ports)
    assert set(S.film_job_ids(run)) == {"de"}
    need = next(n for n in S.needs(run) if n["kind"] == "text_not_approved")
    assert need["language"] == "en"


def test_the_english_film_starts_while_the_other_texts_are_still_written(
        db_session, run, case):
    release = asyncio.Event()

    class Slow(FakePorts):
        def __init__(self):
            super().__init__(approve=[])

        async def write_texts(self, db, case, languages):
            self.calls.append("texts")
            for lang in languages:
                if lang != "en" and not release.is_set():
                    continue
                if R.approved_final(db, case.id, lang) is None:
                    make_story_version(db, case, kind="final", language=lang,
                                       approved_at=utc_now(), approved_by="automatic")
            if not release.is_set():
                await release.wait()
            return {"languages": [{"language": lang, "status": "done"} for lang in languages]}

    ports = Slow()
    passes(db_session, run, ports)
    assert set(S.film_job_ids(run)) == {"en"}
    assert R.approved_final(db_session, case.id, "de") is None
    release.set()
    passes(db_session, run, ports)
    assert R.approved_final(db_session, case.id, "de") is not None
    assert set(S.film_job_ids(run)) == {"en"}
    open_visual_gate(db_session, run)
    passes(db_session, run, ports)
    assert set(S.film_job_ids(run)) == set(LANGS)


def test_a_failed_texts_write_is_retried_up_to_the_limit_then_listed(db_session, run):
    class Boom(FakePorts):
        async def write_texts(self, db, case, languages):
            self.calls.append("texts")
            raise ValueError("gateway exploded")

    ports = Boom()
    limit = ai_config.autopilot_settings().max_attempts_per_phase
    passes(db_session, run, ports, n=limit + 3)
    assert ports.calls.count("texts") == limit
    need = next(n for n in S.needs(run) if n["kind"] == "texts_failed")
    assert "gateway exploded" in need["message"]
