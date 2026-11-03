"""The pronunciation memory with real Case 15 words, on the database of the tests (each test has a language of its own, removed afterwards)."""

from __future__ import annotations

import json
import uuid

import pytest

from app.db.models import (
    PronunciationAuditLog,
    PronunciationEntry,
    PronunciationEntryVersion,
    PronunciationEvidence,
    PronunciationIntervention,
)
from app.documentary.pronunciation_memory import Contradiction, PronunciationMemory


@pytest.fixture
def memory(db_session):
    language = f"fa-t{uuid.uuid4().hex[:8]}"
    mem = PronunciationMemory(db_session)
    mem.language = language
    yield mem
    ids = [e.id for e in db_session.query(PronunciationEntry).filter_by(language=language)]
    for model in (PronunciationAuditLog, PronunciationEvidence, PronunciationIntervention, PronunciationEntryVersion):
        db_session.query(model).filter(model.entry_id.in_(ids)).delete(synchronize_session=False)
    db_session.query(PronunciationEntry).filter_by(language=language).delete(synchronize_session=False)
    db_session.commit()


def propose(mem, surface="بهم", expected="بِهَم", actor="agent:irab", **kw):
    return mem.propose(language=mem.language, surface=surface, expected_irab=expected, actor=actor, **kw)


def test_an_agent_proposal_is_a_candidate_with_its_first_version_and_an_audit_line(memory, db_session):
    p = propose(memory, sentence="نمی‌خواست زندگی دخترهاش بهم بریزه.", prev_word="دخترهاش", next_word="بریزه", confidence=0.7, grammar={"register": "colloquial"})
    assert p.created and p.entry.status == "candidate" and p.entry.source == "agent" and p.entry.surface_key == "بهم" and p.entry.version == 1
    assert [v.version for v in memory.history(p.entry.id)] == [1]
    assert db_session.query(PronunciationAuditLog).filter_by(entry_id=p.entry.id, action="propose").count() == 1


def test_a_second_proposal_for_the_same_word_and_context_updates_the_candidate_and_keeps_the_old_state(memory):
    first = propose(memory, expected="بَهَم", observed_wrong="")
    second = propose(memory, expected="بِهَم", observed_wrong="باهم")
    assert second.entry.id == first.entry.id and not second.created and second.entry.version == 2 and second.entry.expected_irab == "بِهَم"
    assert [json.loads(v.snapshot)["expected_irab"] for v in memory.history(first.entry.id)] == ["بَهَم", "بِهَم"]


def test_only_a_named_person_approves_and_the_approval_is_marked_human(memory):
    entry = propose(memory).entry
    with pytest.raises(PermissionError):
        memory.approve(entry.id, pronunciation="بِهَم", reviewer=" ")
    with pytest.raises(ValueError):
        memory.approve(entry.id, pronunciation="beham", reviewer="owner")
    approved = memory.approve(entry.id, pronunciation="بِهَم", reviewer="owner", scope="global", reason="heard it in the film")
    assert (approved.status, approved.source, approved.reviewer, approved.scope) == ("human_approved", "human", "owner", "global") and approved.approved_at
    assert memory.history(entry.id)[-1].actor == "human:owner"


def test_an_agent_never_overwrites_a_persons_approval_and_the_blocked_proposal_is_recorded(memory, db_session):
    entry = propose(memory).entry
    memory.approve(entry.id, pronunciation="بِهَم", reviewer="owner")
    blocked = propose(memory, expected="باهَم", observed_wrong="")
    assert blocked.blocked_by is not None and blocked.entry.approved_pronunciation == "بِهَم" and blocked.entry.version == 2
    assert db_session.query(PronunciationAuditLog).filter_by(entry_id=entry.id, action="proposal_blocked").count() == 1
    same = propose(memory, expected="بِهَم")
    assert same.blocked_by is None and not same.created, "the same expectation is not a conflict"


def test_a_different_approval_needs_an_explicit_supersede_and_the_history_keeps_both(memory):
    entry = propose(memory).entry
    memory.approve(entry.id, pronunciation="بِهَم", reviewer="owner")
    with pytest.raises(Contradiction) as caught:
        memory.approve(entry.id, pronunciation="بَهَم", reviewer="owner")
    assert caught.value.entry.id == entry.id
    memory.approve(entry.id, pronunciation="بَهَم", reviewer="owner", supersede=True, reason="the first was a slip")
    assert [json.loads(v.snapshot)["approved_pronunciation"] for v in memory.history(entry.id)] == [None, "بِهَم", "بَهَم"]


def test_a_person_rolls_an_entry_back_and_the_rollback_is_a_new_version(memory):
    entry = propose(memory).entry
    memory.approve(entry.id, pronunciation="بِهَم", reviewer="owner")
    memory.approve(entry.id, pronunciation="بَهَم", reviewer="owner", supersede=True)
    with pytest.raises(PermissionError):
        memory.rollback(entry.id, 2, reviewer="")
    back = memory.rollback(entry.id, 2, reviewer="owner")
    assert back.approved_pronunciation == "بِهَم" and back.version == 4 and len(memory.history(entry.id)) == 4


def test_a_word_is_found_only_in_its_exact_written_form_and_the_most_specific_approved_entry_comes_first(memory):
    general = propose(memory, surface="عهد", expected="اَهد").entry
    memory.approve(general.id, pronunciation="اَهد", reviewer="owner", scope="global")
    specific = propose(memory, surface="عهد", expected="عُهد", prev_word="شکستن", sense="vow").entry
    memory.approve(specific.id, pronunciation="عُهد", reviewer="owner")
    marked = propose(memory, surface="عَهْد", expected="عَهْد").entry
    found = memory.find(memory.language, "عهد", prev_word="شکستن", sense="vow")
    assert [m.entry.id for m in found] == [specific.id, general.id] and found[0].specificity == 2
    assert [m.entry.id for m in memory.find(memory.language, "عهد", prev_word="دیدن")] == [general.id]
    assert [m.entry.id for m in memory.find(memory.language, "عَهْد")] == [marked.id], "another written form of the word is another entry"
    assert memory.find(memory.language, "عهد", voice_id="x")[0].entry.id == general.id


def test_an_entry_for_one_voice_does_not_apply_to_another(memory):
    entry = propose(memory, surface="گورستون", expected="گورِستون", voice_id="V1", model_id="eleven_v4").entry
    assert [m.entry.id for m in memory.find(memory.language, "گورستون", voice_id="V1", model_id="eleven_v4")] == [entry.id]
    assert memory.find(memory.language, "گورستون", voice_id="V2", model_id="eleven_v4") == []


def test_an_intervention_is_validated_by_what_the_voice_did_and_not_by_how_the_rule_reads(memory):
    entry = propose(memory, surface="ببخشتش", expected="بِبَخشَدِش").entry
    memory.approve(entry.id, pronunciation="بِبَخشَدِش", reviewer="owner")
    item = memory.add_intervention(entry.id, kind="dictionary_alias", exact_form="ببخشتش", replacement="بِبَخشَدِش", voice_id="V1", model_id="eleven_v4",
                                   dictionary_id="D", dictionary_version_id="v1")
    assert item.status == "candidate" and memory.reusable(memory.language, "ببخشتش", voice_id="V1", model_id="eleven_v4") == []
    ok = memory.record_outcome(item.id, success=True, audio_ref="take.mp3")
    assert ok.status == "validated" and ok.validated_at and [i.id for i in memory.reusable(memory.language, "ببخشتش", voice_id="V1", model_id="eleven_v4")] == [item.id]
    assert memory.reusable(memory.language, "ببخشتش", voice_id="V2", model_id="eleven_v4") == [], "validated for one voice only"
    assert memory.reusable(memory.language, "بِبَخشتش", voice_id="V1", model_id="eleven_v4") == [], "never moved onto a form with other marks"
    assert memory.record_outcome(item.id, success=False).status == "candidate"


def test_an_intervention_that_keeps_failing_is_failed_and_a_candidate_entry_is_never_reused(memory):
    entry = propose(memory, surface="کنسول", expected="کُنسول").entry
    item = memory.add_intervention(entry.id, kind="dictionary_phoneme", exact_form="کنسول", replacement="konsol")
    memory.record_outcome(item.id, success=True)
    assert memory.reusable(memory.language, "کنسول") == [], "the target pronunciation is not approved by a person yet"
    bad = memory.add_intervention(entry.id, kind="irab", exact_form="کنسول", replacement="کُنسُل")
    memory.record_outcome(bad.id, success=False)
    assert memory.record_outcome(bad.id, success=False).status == "failed"
    assert memory.add_intervention(entry.id, kind="irab", exact_form="کنسول", replacement="کُنسُل").id == bad.id, "the same intervention is not stored twice"
    with pytest.raises(ValueError):
        memory.add_intervention(entry.id, kind="magic", exact_form="کنسول", replacement="x")


def test_evidence_keeps_the_measurement_apart_from_the_inference(memory, db_session):
    entry = propose(memory, surface="عهد", expected="اَهد").entry
    memory.add_evidence(surface="عهد", kind="acoustic", verdict="mismatch", entry_id=entry.id, confidence=0.8, expected="اَهد", estimated_observed="اُهد",
                        audio_ref="b.mp3", start_s=1.2, end_s=1.6)
    memory.add_evidence(surface="عهد", kind="inference", verdict="uncertain", entry_id=entry.id)
    kinds = [e.kind for e in db_session.query(PronunciationEvidence).filter_by(entry_id=entry.id).order_by(PronunciationEvidence.id)]
    assert kinds == ["acoustic", "inference"]
    with pytest.raises(ValueError):
        memory.add_evidence(surface="عهد", kind="guess", verdict="mismatch")


def test_two_approvals_for_the_same_word_in_two_written_forms_are_a_conflict(memory):
    a = propose(memory, surface="عهد", expected="اَهد").entry
    b = propose(memory, surface="عَهْد", expected="عُهد").entry
    memory.approve(a.id, pronunciation="اَهد", reviewer="owner")
    assert memory.conflicts(memory.language) == []
    memory.approve(b.id, pronunciation="عُهد", reviewer="owner")
    assert [(x.id, y.id) for x, y in memory.conflicts(memory.language)] == [(a.id, b.id)]


def test_two_different_contexts_with_different_pronunciations_are_not_a_conflict(memory):
    a = propose(memory, surface="در", expected="دَر", next_word="باز").entry
    b = propose(memory, surface="در", expected="دَرِ", next_word="گاراژ").entry
    memory.approve(a.id, pronunciation="دَر", reviewer="owner")
    memory.approve(b.id, pronunciation="دَرِ", reviewer="owner")
    assert memory.conflicts(memory.language) == []
    assert [m.entry.id for m in memory.find(memory.language, "در", next_word="گاراژ")] == [b.id]
