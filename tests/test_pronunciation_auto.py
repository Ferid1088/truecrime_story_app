"""The app verifies pronunciations itself (switched on only here): up to three attempts with an independent linguist and the quality agent; no agreement leaves an entry unresolved."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.db.models import (
    PronunciationAuditLog,
    PronunciationEntry,
    PronunciationEntryVersion,
    PronunciationEvidence,
    PronunciationIntervention,
)
from app.documentary import pronunciation_agents as PA
from app.documentary import pronunciation_auto as AUTO
from app.documentary.pronunciation_memory import PronunciationMemory


@pytest.fixture(autouse=True)
def switched_on(monkeypatch):
    from app.core.ai_config import ai_config

    monkeypatch.setattr(ai_config.pronunciation_auto, "enabled", True)


@pytest.fixture
def world(db_session):
    language = f"fa-t{uuid.uuid4().hex[:8]}"
    memory = PronunciationMemory(db_session)
    yield language, memory
    ids = [e.id for e in db_session.query(PronunciationEntry).filter_by(language=language)]
    for model in (PronunciationAuditLog, PronunciationEvidence, PronunciationIntervention, PronunciationEntryVersion):
        db_session.query(model).filter(model.entry_id.in_(ids)).delete(synchronize_session=False)
    db_session.query(PronunciationEntry).filter_by(language=language).delete(synchronize_session=False)
    db_session.commit()


def legacy(world, surface="بهم", reading="بِهَم"):
    language, memory = world
    entry, _ = memory.add_legacy(language=language, surface=surface, expected_irab=reading, source={"table": "reading_approvals", "id": uuid.uuid4().int % 10**9, "source": "owner", "kind": "spoken_form"})
    return entry


class Runner:
    """Scripted agents: the linguist answers `reading` for the word, the quality agent answers `verdict`."""

    def __init__(self, reading, verdict="reject", fail=False):
        self.reading, self.verdict, self.fail, self.calls = reading, verdict, fail, []

    async def __call__(self, agent, payload):
        self.calls.append((agent, payload))
        if self.fail:
            raise RuntimeError("provider hiccup")
        if agent == "linguist":
            words = [dict(n=w["n"], irab=self.reading if w["text"] == "بهم" else w["text"], speech_register="neutral", pos="x", ezafe=False, status="resolved", confidence=0.9, note="") for w in payload["words"]]
            return PA.check_answer("linguist", dict(words=words, unresolved=[]))
        checks = {k: dict(ok=self.verdict == "approve", note="n") for k in ("linguistic", "audio_evidence", "dictionary_compatibility", "intervention_choice", "fluency", "memory_integrity", "reuse_safety", "cost")}
        return PA.check_answer("qa", dict(verdict=self.verdict, problem="none" if self.verdict == "approve" else "unsupported_confidence", reason="r", **checks))


def verify(db, entry, runner, sentences=None, monkeypatch=None):
    if monkeypatch:
        monkeypatch.setattr(AUTO, "sentences_for", lambda db, e, v: sentences or ["زندگی‌اش بهم ریخت"])
    return asyncio.run(AUTO.verify(db, entry.id, None, runner))


def test_an_independent_linguist_that_agrees_verifies_the_entry_on_the_first_attempt(world, db_session, monkeypatch):
    entry = legacy(world)
    runner = Runner("بِهَم")
    out = verify(db_session, entry, runner, monkeypatch=monkeypatch)
    db_session.refresh(entry)
    assert (out.attempts, out.agreed) == (1, True) and entry.status == "auto_verified" and entry.approved_pronunciation == "بِهَم" and entry.reviewer == "auto (verified in 1 attempt)"
    assert [c[0] for c in runner.calls] == ["linguist"] and entry.confidence == 0.8


def test_the_linguist_is_not_told_what_the_entry_says_so_its_answer_is_independent(world, db_session, monkeypatch):
    entry = legacy(world)
    runner = Runner("بِهَم")
    verify(db_session, entry, runner, monkeypatch=monkeypatch)
    payload = runner.calls[0][1]
    assert payload["memory"] == [] and "بِهَم" not in str(payload)


def test_when_the_linguist_differs_the_quality_agent_judges_and_an_approval_ends_the_attempts(world, db_session, monkeypatch):
    entry = legacy(world)
    runner = Runner("بَهَم", verdict="approve")
    out = verify(db_session, entry, runner, monkeypatch=monkeypatch)
    assert (out.attempts, out.agreed) == (1, True) and [c[0] for c in runner.calls] == ["linguist", "qa"]
    assert "quality agent: approve" in out.notes[0]


def test_after_three_attempts_without_agreement_the_entry_is_unresolved_and_never_approved(world, db_session, monkeypatch):
    entry = legacy(world)
    runner = Runner("بَهَم", verdict="reject")
    out = verify(db_session, entry, runner, sentences=["زندگی‌اش بهم ریخت", "او بهم ریخت", "بهم"], monkeypatch=monkeypatch)
    db_session.refresh(entry)
    assert (out.attempts, out.agreed) == (3, False) and len(runner.calls) == 6
    assert entry.status == "unresolved" and entry.reviewer == "auto (no agreement after 3 attempts)" and entry.confidence == 0.0 and entry.approved_pronunciation is None
    sentences = [c[1]["sentence"] for c in runner.calls if c[0] == "linguist"]
    assert sentences == ["زندگی‌اش بهم ریخت", "او بهم ریخت", "بهم"], "a different sentence each attempt"
    assert db_session.query(PronunciationEvidence).filter_by(entry_id=entry.id, kind="model", verdict="uncertain").count() == 1
    assert db_session.query(PronunciationAuditLog).filter_by(entry_id=entry.id, action="auto_unresolved").one().note.startswith("attempts=3 agreed=False")


def test_a_model_that_keeps_failing_still_ends_after_three_attempts(world, db_session, monkeypatch):
    entry = legacy(world)
    out = verify(db_session, entry, Runner("x", fail=True), monkeypatch=monkeypatch)
    db_session.refresh(entry)
    assert out.attempts == 3 and not out.agreed and entry.status == "unresolved" and all("failed" in n for n in out.notes)


def test_what_a_person_decided_is_never_touched(world, db_session, monkeypatch):
    approved, rejected = legacy(world, "بهم", "بِهَم"), legacy(world, "عهد", "اَهد")
    memory = world[1]
    memory.approve(approved.id, pronunciation="بِهَم", reviewer="owner")
    memory.reject(rejected.id, reviewer="owner")
    runner = Runner("بِهَم")
    for entry in (approved, rejected):
        out = verify(db_session, entry, runner, monkeypatch=monkeypatch)
        assert out.attempts == 0 and "left as it is" in out.notes[0]
    db_session.refresh(approved)
    db_session.refresh(rejected)
    assert (approved.status, rejected.status) == ("human_approved", "rejected") and runner.calls == []


def test_an_auto_verified_entry_is_a_reference_below_a_person_who_can_replace_it_without_a_supersede(world, db_session, monkeypatch):
    language, memory = world
    entry = legacy(world)
    verify(db_session, entry, Runner("بِهَم"), monkeypatch=monkeypatch)
    item = memory.add_intervention(entry.id, kind="dictionary_alias", exact_form="بهم", replacement="بِهَم")
    memory.record_outcome(item.id, success=True)
    assert [i.id for i in memory.reusable(language, "بهم")] == [item.id], "the app's approval makes the validated intervention reusable"
    assert memory.find(language, "بهم")[0].entry.status == "auto_verified"
    person = memory.approve(entry.id, pronunciation="بَهَم", reviewer="owner")
    assert person.status == "human_approved" and person.approved_pronunciation == "بَهَم"
    assert memory.propose(language=language, surface="بهم", expected_irab="بِهَم", actor="agent:x").blocked_by is not None


def test_an_agent_proposal_does_not_change_an_auto_verification(world, db_session, monkeypatch):
    language, memory = world
    entry = legacy(world)
    verify(db_session, entry, Runner("بِهَم"), monkeypatch=monkeypatch)
    blocked = memory.propose(language=language, surface="بهم", expected_irab="باهَم", actor="agent:linguist")
    assert blocked.blocked_by is not None and blocked.entry.approved_pronunciation == "بِهَم"


def test_verify_all_goes_through_every_legacy_entry_of_the_language(world, db_session, monkeypatch):
    language, _ = world
    legacy(world, "بهم", "بِهَم")
    legacy(world, "عهد", "اَهد")
    monkeypatch.setattr(AUTO, "sentences_for", lambda db, e, v: [f"او {e.surface} را دید"])

    async def runner(agent, payload):
        words = [dict(n=w["n"], irab="اَهد" if w["text"] == "عهد" else "بِهَم" if w["text"] == "بهم" else w["text"], speech_register="neutral", pos="x", ezafe=False, status="resolved", confidence=0.9, note="") for w in payload["words"]]
        return PA.check_answer("linguist", dict(words=words, unresolved=[]))

    results = asyncio.run(AUTO.verify_all(db_session, language, None, runner))
    assert sorted(r.surface for r in results) == ["بهم", "عهد"] and all(r.agreed for r in results)
    assert db_session.query(PronunciationEntry).filter_by(language=language, status="auto_verified").count() == 2


def test_autonomous_verification_is_off_by_default_and_changes_nothing_while_it_is_off(world, db_session, monkeypatch):
    from app.core.ai_config import ai_config

    monkeypatch.setattr(ai_config.pronunciation_auto, "enabled", False)
    entry = legacy(world)
    with pytest.raises(AUTO.AutoDisabled):
        asyncio.run(AUTO.verify(db_session, entry.id, None, Runner("بِهَم")))
    db_session.refresh(entry)
    assert entry.status == "legacy_unverified" and entry.approved_pronunciation is None


def test_the_shipped_configuration_keeps_it_off():
    import json
    from pathlib import Path

    from app.core.ai_config import PronunciationAutoConfig

    shipped = json.loads((Path(__file__).resolve().parents[1] / "config/parameters/documentary.json").read_text(encoding="utf-8"))
    assert PronunciationAutoConfig().enabled is False and shipped["pronunciation_auto"]["enabled"] is False


def test_a_system_made_verification_is_reverted_to_legacy_and_a_persons_approval_is_not(world, db_session, monkeypatch):
    _, memory = world
    checked, approved = legacy(world, "بهم", "بِهَم"), legacy(world, "عهد", "اَهد")
    verify(db_session, checked, Runner("بِهَم"), monkeypatch=monkeypatch)
    memory.approve(approved.id, pronunciation="اَهد", reviewer="owner")
    for entry in (checked, approved):
        memory.revert_to_legacy(entry.id, reason="autonomous verification is not allowed yet")
        db_session.refresh(entry)
    assert checked.status == "legacy_unverified" and checked.approved_pronunciation is None and checked.reviewer is None
    assert approved.status == "human_approved"
    assert db_session.query(PronunciationAuditLog).filter_by(entry_id=checked.id, action="revert_to_legacy").count() == 1
