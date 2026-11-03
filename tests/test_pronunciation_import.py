"""The earlier systems imported into the pronunciation memory as legacy_unverified, with real Case 15 rows copied by shape."""

from __future__ import annotations

import json
import uuid

import pytest

from app.db.models import (
    PronunciationAuditLog,
    PronunciationEntry,
    PronunciationEntryVersion,
    PronunciationLexicon,
    ReadingApproval,
)
from app.documentary.pronunciation_import import import_legacy
from app.documentary.pronunciation_memory import PronunciationMemory


@pytest.fixture
def seeded(db_session):
    language = f"fa-t{uuid.uuid4().hex[:8]}"
    db_session.add_all([
        ReadingApproval(language=language, surface="عهد", kind="spoken_form", sense="", latin="'ahd", source="owner"),
        ReadingApproval(language=language, surface="بهم", kind="spoken_form", sense="", latin="beham", source="owner"),
        PronunciationLexicon(language=language, surface="بهم", kind="alias", reading="بَهَم", source="reviewer", hits=3),
        PronunciationLexicon(language=language, surface="کالب", kind="alias", reading="کالِبْ", source="reviewer", hits=1075),
        PronunciationLexicon(language=language, surface="مسلح", kind="phoneme", reading="mosællæh", source="reviewer", hits=16),
        PronunciationLexicon(language=language, surface="غریب", kind="phoneme", reading="ɡæɾibæ§", source="reviewer", hits=1),
    ])
    db_session.commit()
    yield language
    ids = [e.id for e in db_session.query(PronunciationEntry).filter_by(language=language)]
    for model in (PronunciationAuditLog, PronunciationEntryVersion):
        db_session.query(model).filter(model.entry_id.in_(ids)).delete(synchronize_session=False)
    db_session.query(PronunciationEntry).filter_by(language=language).delete(synchronize_session=False)
    db_session.query(ReadingApproval).filter_by(language=language).delete(synchronize_session=False)
    db_session.query(PronunciationLexicon).filter_by(language=language).delete(synchronize_session=False)
    db_session.commit()


def entry_of(db, language, surface):
    return db.query(PronunciationEntry).filter_by(language=language, surface=surface).one()


def test_every_old_row_becomes_a_legacy_unverified_entry_and_none_is_called_approved(seeded, db_session):
    assert import_legacy(db_session, seeded) == {"entries_created": 5, "sources_added": 1, "skipped": 0}
    entries = db_session.query(PronunciationEntry).filter_by(language=seeded).all()
    assert {e.status for e in entries} == {"legacy_unverified"} and {e.source for e in entries} == {"legacy"} and all(e.approved_pronunciation is None for e in entries)
    ahd = entry_of(db_session, seeded, "عهد")
    assert ahd.expected_irab == "اَهد" and ahd.origin == "reading_approvals"
    source = json.loads(ahd.origin_data)["sources"][0]
    assert (source["source"], source["kind"], source["latin"]) == ("owner", "spoken_form", "'ahd") and source["created_at"]


def test_a_word_in_both_systems_keeps_both_sources_and_notes_a_different_reading(seeded, db_session):
    import_legacy(db_session, seeded)
    beham = entry_of(db_session, seeded, "بهم")
    data = json.loads(beham.origin_data)
    assert [s["table"] for s in data["sources"]] == ["reading_approvals", "pronunciation_lexicon"] and beham.expected_irab == "بِهَم"
    assert data["different_readings"][0]["reading"] == "بَهَم" and data["sources"][1]["hits"] == 3


def test_an_ipa_row_is_written_in_persian_letters_for_review_and_a_row_that_cannot_be_written_is_kept_without_a_reading(seeded, db_session):
    import_legacy(db_session, seeded)
    mosallah = entry_of(db_session, seeded, "مسلح")
    assert mosallah.expected_irab == "مُسَللَح" and json.loads(mosallah.origin_data)["sources"][0]["reading"] == "mosællæh"
    odd = entry_of(db_session, seeded, "غریب")
    assert odd.expected_irab == "" and json.loads(odd.origin_data)["sources"][0]["reading"] == "ɡæɾibæ§"
    assert entry_of(db_session, seeded, "کالب").expected_irab == "کالِبْ"


def test_importing_again_changes_nothing_and_the_old_tables_are_untouched(seeded, db_session):
    import_legacy(db_session, seeded)
    versions = db_session.query(PronunciationEntryVersion).count()
    assert import_legacy(db_session, seeded) == {"entries_created": 0, "sources_added": 0, "skipped": 6}
    assert db_session.query(PronunciationEntryVersion).count() == versions
    assert db_session.query(ReadingApproval).filter_by(language=seeded).count() == 2 and db_session.query(PronunciationLexicon).filter_by(language=seeded).count() == 4


def test_an_agent_cannot_change_a_legacy_entry_and_a_legacy_entry_is_never_reused_until_a_person_approves_it(seeded, db_session):
    import_legacy(db_session, seeded)
    memory = PronunciationMemory(db_session)
    ahd = entry_of(db_session, seeded, "عهد")
    blocked = memory.propose(language=seeded, surface="عهد", expected_irab="عُهد", actor="agent:irab")
    assert blocked.blocked_by is not None and entry_of(db_session, seeded, "عهد").expected_irab == "اَهد"
    item = memory.add_intervention(ahd.id, kind="dictionary_alias", exact_form="عهد", replacement="اَهد")
    memory.record_outcome(item.id, success=True)
    assert memory.reusable(seeded, "عهد") == []
    memory.approve(ahd.id, pronunciation="اَهد", reviewer="owner")
    assert [i.id for i in memory.reusable(seeded, "عهد")] == [item.id] and entry_of(db_session, seeded, "عهد").status == "human_approved"
    db_session.query(PronunciationAuditLog).filter_by(entry_id=ahd.id, action="proposal_blocked").one()
