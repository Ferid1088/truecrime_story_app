"""The orchestrator: one more film starts only while the own machine and the providers have room, a film that is held stays queued with
the reason, a provider that says "too many requests" gets a smaller limit that recovers, and the monitor shows all of it."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_worker import STAGES, _case

from app import worker as W
from app.core import concurrency
from app.core.ai_config import ai_config
from app.db.models import DocumentaryJob, OrchestratorState
from app.orchestration.governor import Governor
from app.orchestration.host import HostSample


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def host(free_gb=32.0, load=0.2):
    return lambda: HostSample(free_gb=free_gb, total_gb=64.0, load_per_core=load, cores=8)


def governor(sampler=None, clock=None) -> Governor:
    return Governor(ai_config.orchestrator_settings().model_copy(update={"stagger_seconds": 20.0}), sampler or host(), clock or Clock())


# --- the own machine ---------------------------------------------------------------------------------------------------------------------

def test_a_film_is_admitted_while_memory_and_load_have_room():
    d = governor().decide(running=2, cap=9)
    assert d.allowed and d.code == "admit"


def test_memory_that_would_fall_below_the_reserve_holds_the_next_film_and_says_how_much():
    limits = ai_config.orchestrator_settings().host
    just_short = limits.min_free_memory_gb + limits.job_memory_gb - 0.1
    d = governor(host(free_gb=just_short)).decide(running=1, cap=9)
    assert not d.allowed and d.code == "memory" and "GB memory is free" in d.reason
    assert governor(host(free_gb=just_short + 0.2)).decide(running=1, cap=9).allowed


def test_a_machine_under_load_holds_the_next_film():
    d = governor(host(load=ai_config.orchestrator_settings().host.max_load_per_core + 0.5)).decide(running=1, cap=9)
    assert not d.allowed and d.code == "cpu" and "per core" in d.reason


def test_two_films_never_start_closer_than_the_stagger():
    clock = Clock()
    g = governor(clock=clock)
    assert g.decide(0, 9).allowed
    g.admitted()
    clock.now += 5
    d = g.decide(1, 9)
    assert not d.allowed and d.code == "stagger"
    clock.now += 16
    assert g.decide(1, 9).allowed


def test_the_first_film_always_starts_even_on_a_machine_with_little_room():
    d = governor(host(free_gb=0.5, load=9.0)).decide(running=0, cap=9)
    assert d.allowed and "first one always starts" in d.reason


def test_the_cap_holds_even_on_an_empty_machine():
    d = governor().decide(running=9, cap=9)
    assert not d.allowed and d.code == "cap"


def test_the_changes_of_the_decision_are_logged_not_every_repeat():
    g = governor(host(free_gb=1.0))
    for _ in range(5):
        g.decide(1, 9)
    assert [e["code"] for e in g.events] == ["memory"]
    g.sampler = host()
    g.decide(1, 9)
    assert [e["code"] for e in g.events] == ["memory", "admit"]


# --- the providers -----------------------------------------------------------------------------------------------------------------------

def test_a_provider_that_says_too_many_gets_half_its_limit_and_it_grows_back_one_slot_at_a_time():
    start = 5000.0
    configured = concurrency.limit_size("llm")
    policy = ai_config.orchestrator_settings().throttle
    assert concurrency.report_throttle("llm", now=start) == max(policy.min_limit, int(configured * policy.shrink_factor))
    shrunk = concurrency.effective_limit("llm", now=start)
    assert shrunk < configured and concurrency.throttled("llm", now=start)
    assert concurrency.effective_limit("llm", now=start + policy.cooldown_seconds - 1) == shrunk      # still cooling down
    later = start + policy.cooldown_seconds + policy.recovery_seconds * 2
    assert shrunk < concurrency.effective_limit("llm", now=later) <= configured
    assert concurrency.effective_limit("llm", now=start + 10_000) == configured


def test_repeated_reports_never_push_a_limit_below_its_minimum():
    now = 7000.0
    for _ in range(10):
        concurrency.report_throttle("elevenlabs_tts", now=now)
    assert concurrency.effective_limit("elevenlabs_tts", now=now) == ai_config.orchestrator_settings().throttle_overrides["elevenlabs_tts"].min_limit


def test_a_throttled_provider_really_lets_fewer_calls_run_at_once():
    concurrency.report_throttle("elevenlabs_tts")
    concurrency.report_throttle("elevenlabs_tts")
    limit = concurrency.effective_limit("elevenlabs_tts")
    peak = {"now": 0, "max": 0}

    async def call():
        async with concurrency.slot("elevenlabs_tts"):
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
            await asyncio.sleep(0.02)
            peak["now"] -= 1

    async def go():
        await asyncio.gather(*(call() for _ in range(8)))

    asyncio.run(go())
    assert peak["max"] <= limit < concurrency.limit_size("elevenlabs_tts")


def test_while_a_provider_is_throttled_no_new_film_starts_but_the_first_one_may():
    concurrency.report_throttle("llm")
    g = governor()
    held = g.decide(running=1, cap=9)
    assert not held.allowed and held.code == "provider" and "llm limit" in held.reason
    assert not g.decide(running=0, cap=9).code == "provider"       # nothing runs yet: the first film may start


def test_a_rate_limit_answer_of_the_language_model_provider_shrinks_its_limit():
    from unittest.mock import patch

    from test_provider_split import _provider

    from app.providers.generation.base import GenerationError

    provider = _provider()
    calls = []

    async def refuse(model, system, user, gen):
        calls.append(model)
        raise GenerationError("rate_limited", "rate limit exceeded (429)")

    assert not concurrency.throttled("llm")
    with patch.object(provider, "_chat", side_effect=refuse), pytest.raises(GenerationError):
        asyncio.run(provider.generate_structured("fact_extractor", "sys", "user"))
    assert calls and concurrency.throttled("llm") and not concurrency.throttled("vision")


def test_a_rate_limit_answer_of_the_voice_provider_shrinks_its_limit(monkeypatch):
    from app.providers.voice import elevenlabs as EL
    from app.providers.voice.base import VoiceRequest

    provider = EL.ElevenLabsVoiceProvider.__new__(EL.ElevenLabsVoiceProvider)
    provider.cfg, provider.api_key = ai_config.voice.elevenlabs, "key"
    answers = []

    async def post(url, body):
        answers.append(url)
        return 429, {"detail": {"message": "too many requests"}}, {}

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(provider, "_post", post)
    monkeypatch.setattr(EL.asyncio, "sleep", no_wait)
    assert not concurrency.throttled("elevenlabs_tts")
    with pytest.raises(EL.VoiceProviderError):
        asyncio.run(provider.synthesize(VoiceRequest("hello", "voice", "model", {"speed": 1}, language="en")))
    assert len(answers) > 1 and concurrency.throttled("elevenlabs_tts")


# --- the worker and the queue ------------------------------------------------------------------------------------------------------------

def make_jobs(db, n):
    jobs = [DocumentaryJob(case_id=_case(db).id, status="queued", stages_json=json.dumps(STAGES)) for _ in range(n)]
    db.add_all(jobs)
    db.commit()
    return jobs


@pytest.fixture(autouse=True)
def clean_queue(db_session):
    for j in db_session.query(DocumentaryJob).filter(DocumentaryJob.status.in_(("queued", "running", "cancelling"))).all():
        j.status = "cancelled"
    db_session.commit()


def test_a_held_film_stays_queued_and_starts_when_the_machine_has_room(db_session):
    jobs = make_jobs(db_session, 3)
    ran = []

    async def fake_run(job_id, session_factory=None):
        ran.append(job_id)
        db = session_factory()
        db.get(DocumentaryJob, job_id).status = "running"
        db.commit()
        db.close()
        await asyncio.sleep(0.2)

    free = {"gb": 1.0}
    g = Governor(ai_config.orchestrator_settings().model_copy(update={"stagger_seconds": 0.0}),
                 lambda: HostSample(free_gb=free["gb"], total_gb=64.0, load_per_core=0.1, cores=8))

    async def go():
        w = W.Worker(run_job=fake_run, worker_id="t", poll_seconds=0.02, lease_seconds=3, max_parallel=9, governor=g)
        await w.tick()
        assert len(w.tasks) == 1                            # memory is short: the first film starts, the others are not claimed
        assert db_session.query(DocumentaryJob).filter_by(status="queued", worker_id=None).count() == 2
        free["gb"] = 32.0
        await w.tick()
        await asyncio.sleep(0.05)
        assert len(w.tasks) == 3
        await asyncio.gather(*w.tasks.values())

    asyncio.run(go())
    db_session.expire_all()
    assert sorted(ran) == sorted(j.id for j in jobs)


def test_the_worker_writes_what_the_orchestrator_saw_every_tick(db_session):
    make_jobs(db_session, 1)
    g = Governor(ai_config.orchestrator_settings().model_copy(update={"stagger_seconds": 0.0}), host(free_gb=1.0))

    async def go():
        w = W.Worker(run_job=lambda *a, **k: asyncio.sleep(0), worker_id="t", poll_seconds=0.02, lease_seconds=3, max_parallel=9, governor=g)
        await w.tick()

    asyncio.run(go())
    db_session.expire_all()
    snapshot = json.loads(db_session.get(OrchestratorState, 1).snapshot_json)
    assert snapshot["decision"]["code"] == "memory" and snapshot["host"]["free_gb"] == 1.0 and snapshot["cap"] == 9
    assert {"llm", "elevenlabs_tts"} <= set(snapshot["providers"])


def test_nine_queued_films_are_admitted_one_after_the_other_as_memory_allows(db_session):
    """A simulated machine: every started film uses 2 GB; the orchestrator admits one film per stagger until the reserve is reached and
    holds the rest, queued."""
    make_jobs(db_session, 9)
    started = []
    state = {"free": 12.0}
    clock = Clock()

    async def fake_run(job_id, session_factory=None):
        started.append(job_id)
        state["free"] -= 2.0
        db = session_factory()
        db.get(DocumentaryJob, job_id).status = "running"
        db.commit()
        db.close()
        await asyncio.sleep(0.5)

    g = governor(lambda: HostSample(free_gb=state["free"], total_gb=64.0, load_per_core=0.1, cores=8), clock)

    async def go():
        w = W.Worker(run_job=fake_run, worker_id="t", poll_seconds=0.02, lease_seconds=3, max_parallel=9, governor=g)
        for _ in range(12):
            await w.tick()
            await asyncio.sleep(0.02)          # the started film's memory shows
            clock.now += 25.0
        assert len(w.tasks) == len(started)
        await asyncio.gather(*w.tasks.values())

    asyncio.run(go())
    limits = ai_config.orchestrator_settings().host
    assert len(started) == 5                   # 12 -> 10 -> 8 -> 6 -> 4 -> 2 GB: a sixth would need 1.5 GB and leave less than the 2 GB reserve
    assert state["free"] - limits.job_memory_gb < limits.min_free_memory_gb
    db_session.expire_all()
    assert db_session.query(DocumentaryJob).filter_by(status="queued").count() == 4


# --- the monitor -------------------------------------------------------------------------------------------------------------------------

def test_the_monitor_shows_the_machine_the_providers_and_why_a_film_waits(client, db_session):
    make_jobs(db_session, 2)
    snapshot = {"host": {"free_gb": 1.0, "total_gb": 16.0, "load_per_core": 0.3, "cores": 8}, "limits": {"min_free_memory_gb": 3.0},
                "running": 1, "cap": 9, "decision": {"allowed": False, "code": "memory", "reason": "1.0 GB memory is free"},
                "providers": {"llm": {"active": 2, "configured": 10, "limit": 5, "throttled": True}},
                "events": [{"at": 1.0, "code": "memory", "reason": "1.0 GB memory is free"}]}
    row = db_session.get(OrchestratorState, 1) or OrchestratorState(id=1)
    row.snapshot_json = json.dumps(snapshot)
    db_session.add(row)
    db_session.commit()
    body = client.get("/api/orchestrator").json()
    assert body["worker_alive"] is True and body["host"]["free_gb"] == 1.0 and body["providers"]["llm"]["throttled"] is True
    queued = [j for j in body["jobs"] if j["status"] == "queued"]
    assert len(queued) == 2 and all("1.0 GB memory is free" in j["waiting_because"] for j in queued)
    assert body["events"][0]["code"] == "memory"


def test_without_a_worker_the_monitor_says_so(client, db_session):
    db_session.query(OrchestratorState).delete()
    db_session.commit()
    body = client.get("/api/orchestrator").json()
    assert body["worker_alive"] is False and body["host"] is None


# --- Shorts ------------------------------------------------------------------------------------------------------------------------------

def test_a_shorts_job_is_held_while_a_provider_asks_for_less_and_admitted_again_when_it_recovers():
    concurrency.reset_throttles()
    try:
        concurrency.report_throttle("llm")
        d = governor().decide_short(running=1, cap=9)
        assert not d.allowed and d.code == "provider" and "llm" in d.reason
        assert governor().decide_short(running=0, cap=9).allowed        # the first one always starts
    finally:
        concurrency.reset_throttles()
    assert governor().decide_short(running=1, cap=9).allowed


def test_a_machine_under_load_holds_the_next_shorts_job():
    load = ai_config.orchestrator_settings().host.max_load_per_core + 0.5
    d = governor(host(load=load)).decide_short(running=1, cap=9)
    assert not d.allowed and d.code == "cpu"
    assert not governor().decide_short(running=9, cap=9).allowed
