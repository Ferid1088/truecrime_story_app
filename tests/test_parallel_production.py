# ruff: noqa: F811  (the `case` and `setup` fixtures are imported from their test modules and used by name)
"""The dependency-driven production, checked as task transitions (which task starts when) and not as configuration values: the English final story gates the other languages' texts,
every language's film (and so its voice) starts the moment its own final text is approved, the Persian voice review and the shared picture approval hold back exactly the work that
depends on them, a restart never makes a second job, and the provider and machine limits hold under nine languages at once. No paid provider is called: every provider is a local
substitute."""

from __future__ import annotations

import asyncio
import json

import pytest
from conftest import _delete_case_tree
from factories import make_story_version
from test_autopilot_runner import FakePorts, case, runner  # noqa: F401  (the `case` fixture)
from test_language_pipeline_flow import FakeGen, OrderedGen, _pipeline, setup  # noqa: F401  (the `setup` fixture)
from test_mvp_reference import _candidate, _film

from app.autopilot import state as S
from app.core import concurrency
from app.core.ai_config import ai_config
from app.db.models import AutopilotRun, Case, DocumentaryJob
from app.documentary import jobs as J
from app.documentary import voice_review as VRV
from app.documentary.mvp import visual_gate as VG
from app.documentary.pipeline.errors import AwaitingVoiceReview
from app.orchestration.governor import Governor
from app.orchestration.host import HostSample
from app.utils import utc_now

THREE = ["en", "de", "fa"]


@pytest.fixture(autouse=True)
def no_english_film_rule(monkeypatch):
    monkeypatch.setattr(ai_config.mvp().review, "require_english_first", False)         # the owner's rule: the English final STORY is the gate, not the English film
    concurrency.reset_throttles()
    yield
    concurrency.reset_throttles()


def make_run(db, case, languages) -> AutopilotRun:
    row = AutopilotRun(case_id=case.id, languages_json=json.dumps(languages), steps_json=json.dumps(S.initial_steps()))
    db.add(row)
    db.commit()
    return row


class Staged(FakePorts):
    """The texts of the languages become approved one batch at a time; the next batch waits until the test lets it go."""

    def __init__(self, batches):
        super().__init__(approve=[])
        self.batches, self.go, self.asked = batches, asyncio.Event(), []

    async def write_texts(self, db, case, languages):
        self.calls.append("texts")
        self.asked.append(list(languages))
        for index, batch in enumerate(self.batches):
            for lang in batch:
                make_story_version(db, case, kind="final", language=lang, approved_at=utc_now(), approved_by="automatic")
            if index < len(self.batches) - 1:
                await self.go.wait()
                self.go.clear()
        return {"languages": [{"language": lang, "status": "done"} for batch in self.batches for lang in batch]}


async def settle(r, db, run, passes=2):
    for _ in range(passes):
        await r.advance(db, run)
        await asyncio.sleep(0.05)
        db.expire_all()


# --- the English final story gates the other languages' texts ----------------------------------------------------------------------------

def test_no_other_language_is_written_before_the_english_final_and_then_they_are_written_together(setup):  # noqa: F811
    gen = OrderedGen()
    out = asyncio.run(_pipeline(gen).run_case(setup.id, THREE, 60, parallel_languages=True))
    assert [lg["status"] for lg in out["languages"]] == ["done"] * 3
    english_last = max(i for i, e in enumerate(gen.events) if e[2] == "en")
    others_first = min(i for i, e in enumerate(gen.events) if e[2] in ("de", "fa"))
    assert english_last < others_first, "no German or Persian call is made before every English call (draft to final) has finished"
    rewrite = "language_rewrite_writer"
    assert max(gen.events.index(("start", rewrite, lg)) for lg in ("de", "fa")) < min(gen.events.index(("end", rewrite, lg)) for lg in ("de", "fa")), "after the gate the languages run together"


def test_a_failed_english_final_starts_no_other_language(setup):  # noqa: F811
    gen = FakeGen(fail_language="en")
    out = asyncio.run(_pipeline(gen).run_case(setup.id, THREE, 60, parallel_languages=True))
    assert out["languages"][0]["status"] != "done"
    assert all(lg["status"] == "failed" and lg["stage"] == "base" for lg in out["languages"][1:]), out
    assert gen.count("language_rewrite_writer", "de") == 0 and gen.count("language_rewrite_writer", "fa") == 0


# --- every language's film (and its voice) starts the moment its own text is approved --------------------------------------------------

def test_english_starts_while_german_is_written_and_german_while_persian_is_written(db_session, case, monkeypatch):  # noqa: F811
    launched: list[int] = []
    monkeypatch.setattr(J, "launch", launched.append)
    ports = Staged([["en"], ["de"], ["fa"]])
    run = make_run(db_session, case, THREE)

    async def drive():
        r = runner(db_session, ports)
        await settle(r, db_session, run)
        first = dict(S.film_job_ids(run))
        ports.go.set()
        await settle(r, db_session, run)
        second = dict(S.film_job_ids(run))
        ports.go.set()
        await settle(r, db_session, run)
        return first, second, dict(S.film_job_ids(run))

    first, second, third = asyncio.run(drive())
    assert set(first) == {"en"}, "English voice work starts while German is still being written"
    assert set(second) == {"en", "de"}, "German starts while Persian is still being written"
    assert set(third) == set(THREE)
    assert launched == [third["en"], third["de"], third["fa"]] and second["en"] == first["en"] == third["en"], "each film was launched once, in the order its text became final"
    assert ports.asked == [THREE], "one text task for all the languages; none of them waited for another film"


def test_a_restarted_runner_makes_no_second_job_and_launches_nothing_twice(db_session, case, monkeypatch):  # noqa: F811
    launched: list[int] = []
    monkeypatch.setattr(J, "launch", launched.append)
    ports = FakePorts(approve=THREE)
    run = make_run(db_session, case, THREE)

    async def drive():
        await settle(runner(db_session, ports), db_session, run, passes=3)
        await settle(runner(db_session, ports), db_session, run, passes=3)          # a new runner object: the worker was restarted
        await settle(runner(db_session, ports), db_session, run, passes=3)

    asyncio.run(drive())
    db_session.expire_all()
    ids = S.film_job_ids(run)
    assert set(ids) == set(THREE) and sorted(launched) == sorted(ids.values()), "one job and one launch per language, however often the runner restarts"
    assert db_session.query(DocumentaryJob).filter(DocumentaryJob.case_id == case.id).count() == 3
    assert ports.calls.count("research") == 1


def test_a_resumed_job_keeps_its_finished_stages_so_nothing_paid_is_made_again():
    job = DocumentaryJob(case_id=1, status="failed", error="x", stages_json=json.dumps([
        {"name": "voice:fa", "status": "done", "attempts": 1}, {"name": "render:fa", "status": "failed", "attempts": 1}, {"name": "opening:fa", "status": "running", "attempts": 1}]))
    J.prepare_resume(job)
    stages = {s["name"]: s["status"] for s in json.loads(job.stages_json)}
    assert stages == {"voice:fa": "done", "render:fa": "pending", "opening:fa": "pending"} and job.status == "queued"


def test_skipped_languages_never_start(db_session, case, monkeypatch):  # noqa: F811
    launched: list[int] = []
    monkeypatch.setattr(J, "launch", launched.append)
    ports = Staged([["en", "fa"]])                                         # the run was started for English and Persian only
    run = make_run(db_session, case, ["en", "fa"])

    async def drive():
        await settle(runner(db_session, ports), db_session, run, passes=3)

    asyncio.run(drive())
    assert ports.asked == [["en", "fa"]], "the texts are asked for the chosen languages only"
    assert set(S.film_job_ids(run)) == {"en", "fa"} and len(launched) == 2


def test_a_language_whose_text_never_became_final_starts_no_film(db_session, case, monkeypatch):  # noqa: F811
    monkeypatch.setattr(J, "launch", lambda job_id: None)
    ports = Staged([["en"]])                                               # German and Persian are chosen but their texts are never approved
    run = make_run(db_session, case, THREE)

    async def drive():
        await settle(runner(db_session, ports), db_session, run, passes=3)

    asyncio.run(drive())
    assert set(S.film_job_ids(run)) == {"en"}


# --- the Persian voice review holds back Persian only ------------------------------------------------------------------------------------

def test_only_persian_is_a_language_whose_narration_the_owner_hears_first():
    assert ai_config.voice.review_languages == ["fa"]
    from app.db.models import StoryVersion

    german = StoryVersion(id=987001, case_id=987001, version=1, kind="final", language="de", story_text="t", narrative_angle="a")
    persian = StoryVersion(id=987002, case_id=987001, version=1, kind="final", language="fa", story_text="t", narrative_angle="a")
    VRV.guard(german, "de")                                                # no gate for German: returns at once
    with pytest.raises(AwaitingVoiceReview):
        VRV.guard(persian, "fa")                                           # Persian: nothing goes on before the approved audio


def test_persian_waiting_for_its_voice_review_holds_back_no_other_language(db_session, case, monkeypatch):  # noqa: F811
    launched: list[int] = []
    monkeypatch.setattr(J, "launch", launched.append)
    ports = FakePorts(approve=THREE)
    run = make_run(db_session, case, THREE)

    async def drive():
        r = runner(db_session, ports)
        await settle(r, db_session, run)
        persian = db_session.get(DocumentaryJob, S.film_job_ids(run)["fa"])
        persian.status, persian.error = "failed", "Awaiting voice review: the fa narration is made. Listen to it, approve it, then resume."
        db_session.commit()
        monkeypatch.setattr(VG, "status", lambda db, case_id: {"open": True, "state": "approved"})       # even with the pictures approved, the voice review is not
        marks.append(len(launched))
        await settle(runner(db_session, ports), db_session, run)          # a restarted worker: a new runner object reads the same database and respects the same gates

    marks: list[int] = []
    asyncio.run(drive())
    before = marks[0]
    ids = S.film_job_ids(run)
    persian = db_session.get(DocumentaryJob, ids["fa"])
    assert persian.status == "failed" and "voice review" in persian.error and len(launched) == before, "the picture approval never resumes a film that waits for the voice review"
    for lang in ("en", "de"):
        assert db_session.get(DocumentaryJob, ids[lang]).status in ("queued", "running"), f"{lang} goes on"
    assert [n["language"] for n in S.needs(run) if n["kind"] == "film_stopped"] == ["fa"], "the owner is asked about Persian only"


# --- the shared picture approval ---------------------------------------------------------------------------------------------------------

def test_updated_or_rejected_pictures_close_the_gate_and_films_waiting_at_it_are_not_resumed(db_session, case, monkeypatch):  # noqa: F811
    from app.db.models import MvpCandidate

    launched: list[int] = []
    monkeypatch.setattr(J, "launch", launched.append)
    _, plan = _film(db_session, "en", [100, 100], case)
    _candidate(db_session, case, plan, "B01", "A1", "approved")
    ports = FakePorts(approve=["en", "de"])
    run = make_run(db_session, case, ["en", "de"])

    async def drive():
        r = runner(db_session, ports)
        await settle(r, db_session, run)
        return r

    r = asyncio.run(drive())
    german = db_session.get(DocumentaryJob, S.film_job_ids(run)["de"])
    german.status, german.error = "failed", f"Awaiting picture review: the de film waits for {VG.WAITING_MARK}. Open Review pictures and approve the package."
    db_session.commit()
    launched.clear()

    async def again():
        await settle(r, db_session, run, passes=1)

    asyncio.run(again())
    assert launched == [] and any(n["kind"] == "visual_approval" for n in S.needs(run)), "not approved: one task for the owner, nothing resumed"
    VG.approve(db_session, case.id)
    asyncio.run(again())
    assert launched == [german.id], "approved: the waiting film goes on by itself"
    german.status, german.error = "failed", f"Awaiting picture review: the de film waits for {VG.WAITING_MARK}. Open Review pictures and approve the package."
    db_session.commit()
    launched.clear()
    _candidate(db_session, case, plan, "B02", "A2", "approved")            # a picture is added: the approval no longer matches
    asyncio.run(again())
    assert launched == [] and VG.status(db_session, case.id)["state"] == "changed"
    db_session.query(MvpCandidate).update({"final_status": "rejected"})    # and a rejected picture changes the package as well
    db_session.commit()
    asyncio.run(again())
    assert launched == [] and not VG.status(db_session, case.id)["open"]


# --- provider limits and the machine's guards under nine languages at once -------------------------------------------------------------------

def test_nine_languages_voice_at_once_but_never_more_than_four_elevenlabs_requests_run_together():
    languages = 9
    assert concurrency.limit_size("voice_stage") == 9 and concurrency.limit_size("elevenlabs_tts") == 4
    seen = {"voices": 0, "max_voices": 0, "requests": 0, "max_requests": 0, "done": 0}

    async def request():
        async with concurrency.slot("elevenlabs_tts"):
            seen["requests"] += 1
            seen["max_requests"] = max(seen["max_requests"], seen["requests"])
            await asyncio.sleep(0.01)
            seen["requests"] -= 1

    async def narration():
        async with concurrency.slot("voice_stage"):
            seen["voices"] += 1
            seen["max_voices"] = max(seen["max_voices"], seen["voices"])
            await asyncio.gather(*(request() for _ in range(6)))            # the blocks of one narration, side by side
            seen["voices"] -= 1
            seen["done"] += 1

    async def go():
        await asyncio.gather(*(narration() for _ in range(languages)))

    asyncio.run(go())
    assert seen["max_voices"] == 9, "all nine narrations are in progress together"
    assert seen["max_requests"] == 4, "the extra requests queue behind the provider limit"
    assert seen["done"] == 9, "nothing failed because of the queue"


def test_a_provider_that_says_too_many_requests_gets_a_smaller_limit_and_the_work_still_finishes():
    seen = {"now": 0, "max": 0}

    async def request():
        async with concurrency.slot("elevenlabs_tts"):
            seen["now"] += 1
            seen["max"] = max(seen["max"], seen["now"])
            await asyncio.sleep(0.01)
            seen["now"] -= 1

    async def go():
        concurrency.report_throttle("elevenlabs_tts")
        await asyncio.gather(*(request() for _ in range(12)))

    asyncio.run(go())
    assert concurrency.effective_limit("elevenlabs_tts") <= 2 and seen["max"] <= 2


def test_no_other_gate_serializes_the_voice_stage():
    """Every slot a voice stage takes, with its configured size: only the provider's own limit and the CPU-bound steps are narrow."""
    sizes = {name: concurrency.limit_size(name) for name in ("voice_stage", "elevenlabs_tts", "asr", "mix", "render", "llm", "jobs")}
    assert sizes["voice_stage"] == 9 and sizes["jobs"] >= sizes["voice_stage"], "the film-job cap would otherwise queue languages before their voice"
    assert sizes["elevenlabs_tts"] == 4


def test_the_machine_guards_still_hold_nine_films_back_when_memory_load_or_a_provider_is_short():
    def sample(free=96.0, load=0.2):
        return lambda: HostSample(free_gb=free, total_gb=128.0, load_per_core=load, cores=20)

    cfg = ai_config.orchestrator_settings().model_copy(update={"stagger_seconds": 0.0})
    cap = ai_config.concurrency.jobs
    assert Governor(cfg, sample(), lambda: 0.0).decide(running=cap, cap=cap).code == "cap"
    assert Governor(cfg, sample(free=3.0), lambda: 0.0).decide(running=2, cap=cap).code == "memory"
    assert Governor(cfg, sample(load=9.0), lambda: 0.0).decide(running=2, cap=cap).code == "cpu"
    concurrency.report_throttle("elevenlabs_tts")
    assert Governor(cfg, sample(), lambda: 0.0).decide(running=2, cap=cap).code == "provider"
    concurrency.reset_throttles()
    assert Governor(cfg, sample(), lambda: 0.0).decide(running=2, cap=cap).allowed


@pytest.fixture(autouse=True)
def _clean_cases(db_session):
    yield
    for stale in db_session.query(Case).filter(Case.slug.like("ref-%")).all():
        _delete_case_tree(db_session, stale.id)


# --- no phase waits for the films of the other languages ------------------------------------------------------------------------------------

def test_titles_are_made_while_no_film_is_finished(db_session, case, monkeypatch):  # noqa: F811
    monkeypatch.setattr(J, "launch", lambda job_id: None)
    ports = FakePorts(approve=THREE)
    run = make_run(db_session, case, THREE)

    async def drive():
        await settle(runner(db_session, ports), db_session, run, passes=3)

    asyncio.run(drive())
    jobs = db_session.query(DocumentaryJob).filter(DocumentaryJob.case_id == case.id).all()
    assert len(jobs) == 3 and all(j.status == "queued" for j in jobs), "no film is finished"
    assert [c for c in ports.calls if c.startswith("titles:")] == ["titles:en,de,fa"], "the titles of every language were asked for in one call, from the final texts"
    assert S.step_status(run, "titles") == "done" and S.step_status(run, "films") != "done"


def test_titles_follow_each_language_as_its_text_becomes_final(db_session, case, monkeypatch):  # noqa: F811
    monkeypatch.setattr(J, "launch", lambda job_id: None)
    ports = Staged([["en"], ["de"], ["fa"]])
    run = make_run(db_session, case, THREE)

    async def drive():
        r = runner(db_session, ports)
        await settle(r, db_session, run)
        ports.go.set()
        await settle(r, db_session, run)
        ports.go.set()
        await settle(r, db_session, run)

    asyncio.run(drive())
    assert [c for c in ports.calls if c.startswith("titles:")] == ["titles:en", "titles:de", "titles:fa"], "each language's titles start when its own text is final (the attempt limit counts failures in a row)"


def test_one_languages_shorts_and_dry_run_queue_do_not_wait_for_another_languages_film(db_session, case, monkeypatch):  # noqa: F811
    from app.db.models import PublishRequest, ShortProductionJob, Video

    monkeypatch.setattr(J, "launch", lambda job_id: None)
    ports = FakePorts(approve=["en", "de"])
    run = make_run(db_session, case, ["en", "de"])

    async def drive():
        r = runner(db_session, ports)
        await settle(r, db_session, run, passes=2)
        german = db_session.get(DocumentaryJob, S.film_job_ids(run)["de"])
        german.status = "completed"                                                   # German is finished, English is still being made
        db_session.add(Video(case_id=case.id, job_id=german.id, language="de", state="rendered", file_path="de.mp4", title="Spur"))
        db_session.commit()
        await settle(r, db_session, run, passes=2)
        selection = db_session.query(ShortProductionJob).filter(ShortProductionJob.film_job_id == german.id).all()
        for row in selection:
            row.status = "done"                                                       # its Shorts are settled
        db_session.commit()
        await settle(r, db_session, run, passes=2)
        return german

    german = asyncio.run(drive())
    english = db_session.get(DocumentaryJob, S.film_job_ids(run)["en"])
    assert english.status == "queued", "English is not finished"
    queued = db_session.query(ShortProductionJob).filter(ShortProductionJob.film_job_id == german.id).count()
    assert queued >= 1 and db_session.query(ShortProductionJob).filter(ShortProductionJob.film_job_id == english.id).count() == 0, "German's Shorts were queued; English has none yet"
    assert S.recall(run, "publish", "signature")["languages"] == ["de"], "the dry-run queue is planned for German alone"
    assert "for de" in (S.step_detail(run, "publish") or "")
    assert all(row.status == "dry_run" for row in db_session.query(PublishRequest).filter(PublishRequest.run_id == run.id)), "nothing is sent: every request is a dry run"
    assert S.step_status(run, "publish") != "done"


def test_the_autopilot_never_publishes_anything(db_session, case, monkeypatch):  # noqa: F811
    """The publish phase only plans a dry-run queue (`sent: false`); publishing is the Publishing Studio's, behind the content and publication approvals."""
    from app.db.models import Publication

    monkeypatch.setattr(J, "launch", lambda job_id: None)
    ports = FakePorts(approve=THREE)
    run = make_run(db_session, case, THREE)

    async def drive():
        await settle(runner(db_session, ports), db_session, run, passes=3)

    asyncio.run(drive())
    assert db_session.query(Publication).filter(Publication.case_id == case.id).count() == 0, "the Autopilot creates no publication, so none can be approved or sent by it"


# --- approvals and gates survive a worker restart ------------------------------------------------------------------------------------------

def test_the_persian_voice_approval_survives_a_restart_and_a_remade_narration_needs_a_new_one(tmp_path, monkeypatch):
    from app.db.models import StoryVersion

    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    persian = StoryVersion(id=987003, case_id=987003, version=1, kind="final", language="fa", story_text="t", narrative_angle="a")
    folder = VRV.folder(persian)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "narration.mp3").write_bytes(b"the narration the owner heard")
    (folder / "manifest.json").write_text("{}")
    assert VRV.state(persian)["approved"] is False
    with pytest.raises(AwaitingVoiceReview):
        VRV.guard(persian, "fa")
    VRV.approve(persian, "sounds right")
    assert VRV.state(persian)["approved"] and VRV.kept(persian, "fa"), "approved: the run never makes this narration again"
    VRV.guard(persian, "fa")                                                     # read from disk again, as a restarted worker does: it passes
    (folder / "narration.mp3").write_bytes(b"a narration made again")           # a remade narration is another audio
    assert not VRV.state(persian)["approved"] and not VRV.kept(persian, "fa")
    with pytest.raises(AwaitingVoiceReview):
        VRV.guard(persian, "fa")


def test_the_picture_approval_survives_a_restart_because_it_is_stored_with_the_plan(db_session):
    case, plan = _film(db_session, "en", [100, 100])
    _candidate(db_session, case, plan, "B01", "A1", "approved")
    VG.approve(db_session, case.id)
    db_session.expire_all()                                                      # nothing is kept in memory: a restarted worker reads the database
    assert VG.status(db_session, case.id)["open"]
    VG.guard(db_session, case.id, "de")


def test_shorts_media_work_is_queued_only_for_a_finished_film_and_only_for_that_film(db_session, case):  # noqa: F811
    from app.db.models import ShortProductionJob
    from app.shortform.pipeline import enqueue_for_finished_job

    running = DocumentaryJob(case_id=case.id, status="running", languages_json=json.dumps(["de"]))
    finished = DocumentaryJob(case_id=case.id, status="completed", languages_json=json.dumps(["fa"]))
    db_session.add_all([running, finished])
    db_session.commit()
    assert enqueue_for_finished_job(db_session, running.id) is None, "the film is not finished: its Shorts need its cuts, voice and timing"
    queued = enqueue_for_finished_job(db_session, finished.id)
    assert queued is not None and queued.film_job_id == finished.id, "a finished film queues its own Shorts without waiting for any other language"
    assert db_session.query(ShortProductionJob).filter(ShortProductionJob.film_job_id == running.id).count() == 0
