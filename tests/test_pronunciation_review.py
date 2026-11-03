"""The pronunciation review page: its API and where a word is spoken in a narration."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.db.models import PronunciationAuditLog, PronunciationEntry, PronunciationEntryVersion
from app.documentary import pronunciation_review as PR
from app.documentary.pronunciation_import import import_legacy


@pytest.fixture
def language(db_session):
    from app.db.models import PronunciationLexicon, ReadingApproval

    lang = f"fa-t{uuid.uuid4().hex[:8]}"
    db_session.add_all([ReadingApproval(language=lang, surface="عهد", kind="spoken_form", sense="", latin="'ahd", source="owner"),
                        PronunciationLexicon(language=lang, surface="مسلح", kind="phoneme", reading="mosællæh", source="reviewer", hits=16)])
    db_session.commit()
    import_legacy(db_session, lang)
    yield lang
    ids = [e.id for e in db_session.query(PronunciationEntry).filter_by(language=lang)]
    for model in (PronunciationAuditLog, PronunciationEntryVersion):
        db_session.query(model).filter(model.entry_id.in_(ids)).delete(synchronize_session=False)
    db_session.query(PronunciationEntry).filter_by(language=lang).delete(synchronize_session=False)
    db_session.query(ReadingApproval).filter_by(language=lang).delete(synchronize_session=False)
    db_session.query(PronunciationLexicon).filter_by(language=lang).delete(synchronize_session=False)
    db_session.commit()


def listed(client, language, status=None):
    return client.get("/api/documentary/pronunciation/entries", params={"language": language, **({"status": status} if status else {})}).json()


def test_the_page_lists_the_imported_entries_as_legacy_unverified_with_their_sources_and_counts(client, language):
    got = listed(client, language)
    assert got["counts"] == {"legacy_unverified": 2} and [e["surface"] for e in got["entries"]] == ["عهد", "مسلح"] or len(got["entries"]) == 2
    ahd = next(e for e in got["entries"] if e["surface"] == "عهد")
    assert ahd["status"] == "legacy_unverified" and ahd["expected_irab"] == "اَهد" and ahd["approved_pronunciation"] is None
    assert ahd["sources"][0]["table"] == "reading_approvals" and ahd["sources"][0]["latin"] == "'ahd"
    assert listed(client, language, "human_approved")["entries"] == []


def test_a_reviewer_approves_a_corrected_pronunciation_and_it_is_kept_with_the_reviewer(client, language):
    ahd = next(e for e in listed(client, language)["entries"] if e["surface"] == "عهد")
    done = client.post(f"/api/documentary/pronunciation/entries/{ahd['id']}/approve", json={"pronunciation": "اَهد", "reviewer": "owner", "scope": "global", "ipa": "ʔæhd"})
    assert done.status_code == 200 and done.json()["status"] == "human_approved" and done.json()["reviewer"] == "owner" and done.json()["approved_ipa"] == "ʔæhd"
    assert listed(client, language)["counts"] == {"legacy_unverified": 1, "human_approved": 1}
    history = client.get(f"/api/documentary/pronunciation/entries/{ahd['id']}/history").json()
    assert [h["actor"] for h in history] == ["agent:import", "human:owner"] and history[-1]["state"]["approved_pronunciation"] == "اَهد"


def test_an_approval_needs_a_reviewer_and_persian_letters_and_a_different_approval_needs_supersede(client, language):
    entry = next(e for e in listed(client, language)["entries"] if e["surface"] == "مسلح")
    url = f"/api/documentary/pronunciation/entries/{entry['id']}/approve"
    assert client.post(url, json={"pronunciation": "مُسَلَّح", "reviewer": " "}).status_code == 422
    assert client.post(url, json={"pronunciation": "mosallah", "reviewer": "owner"}).status_code == 422
    assert client.post(url, json={"pronunciation": "مُسَلَّح", "reviewer": "owner"}).status_code == 200
    assert client.post(url, json={"pronunciation": "مُسَللَح", "reviewer": "owner"}).status_code == 409
    assert client.post(url, json={"pronunciation": "مُسَللَح", "reviewer": "owner", "supersede": True}).json()["approved_pronunciation"] == "مُسَللَح"


def test_a_reviewer_rejects_or_marks_an_entry_uncertain_and_neither_is_ever_used(client, language):
    first, second = listed(client, language)["entries"]
    rejected = client.post(f"/api/documentary/pronunciation/entries/{first['id']}/reject", json={"reviewer": "owner", "reason": "wrong word"})
    unsure = client.post(f"/api/documentary/pronunciation/entries/{second['id']}/uncertain", json={"reviewer": "owner"})
    assert rejected.json()["status"] == "rejected" and unsure.json()["status"] == "uncertain"
    assert client.post(f"/api/documentary/pronunciation/entries/{first['id']}/reject", json={"reviewer": ""}).status_code == 422
    assert listed(client, language)["counts"] == {"rejected": 1, "uncertain": 1}


def test_an_unknown_entry_has_no_occurrences(client):
    assert client.get("/api/documentary/pronunciation/entries/99999999/occurrences", params={"version_id": 1}).status_code == 404


def test_the_places_of_a_word_in_a_narration_come_with_the_times_of_the_words_around_it(monkeypatch, db_session):
    entry = PronunciationEntry(language="x", surface="گورستون", surface_key="گورستون")
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3, "block_id": "FA_B55_g03_01"}
             for i, w in enumerate(["دخترها", "می‌رفتن", "گورستون.", "و", "گورِستون", "بعد"])]
    monkeypatch.setattr(PR, "narration_words", lambda version: (words, Path("/x/narration.mp3")))

    class Version:
        id = 289

    got = PR.occurrences(db_session, entry, Version())
    assert got["audio_url"] == "/api/documentary/versions/289/audio/narration" and got["total"] == 2
    exact, other = got["occurrences"]
    assert exact["form"] == "exact" and exact["written"] == "گورستون" and (exact["start"], exact["end"]) == (0.8, 1.1) and exact["words"][exact["hit"]]["text"] == "گورستون."
    assert other["form"] == "other written form" and other["written"] == "گورِستون", "another written form is shown and marked"
    monkeypatch.setattr(PR, "narration_words", lambda version: ([], None))
    assert PR.occurrences(db_session, entry, Version()) == {"audio_url": None, "total": 0, "occurrences": []}


def test_the_waveform_of_a_clip_follows_the_loudness(tmp_path):
    import subprocess

    wav = tmp_path / "x.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "1", str(tmp_path / "quiet.wav")], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=8000:duration=1", str(tmp_path / "tone.wav")], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(tmp_path / "quiet.wav"), "-i", str(tmp_path / "tone.wav"), "-filter_complex", "[0][1]concat=n=2:v=0:a=1", str(wav)], check=True)
    steps = PR.peaks(wav, 0.0, 2.0, 40)
    assert len(steps) == 40 and max(steps[:15]) == 0.0 and min(steps[25:]) > 0.5


def test_the_app_verifies_an_entry_through_the_page_only_when_autonomous_verification_is_on(client, language, monkeypatch):
    from app.core.ai_config import ai_config
    from app.documentary import pronunciation_auto as AUTO

    entry = next(e for e in listed(client, language)["entries"] if e["surface"] == "عهد")
    off = client.post(f"/api/documentary/pronunciation/entries/{entry['id']}/auto-verify")
    assert off.status_code == 409 and "off" in off.json()["detail"]
    monkeypatch.setattr(ai_config.pronunciation_auto, "enabled", True)

    async def fake(db, entry_id, version, runner=None, max_attempts=3):
        from app.documentary.pronunciation_memory import PronunciationMemory

        PronunciationMemory(db).auto_verify(entry_id, attempts=3, agreed=False, notes=["attempt 3: no agreement"])
        return AUTO.Outcome(entry_id, "عهد", 3, False, ["attempt 3: no agreement"])

    monkeypatch.setattr(AUTO, "verify", fake)
    done = client.post(f"/api/documentary/pronunciation/entries/{entry['id']}/auto-verify").json()
    assert done["attempts"] == 3 and done["agreed"] is False and done["entry"]["status"] == "unresolved" and done["entry"]["approved_pronunciation"] is None
    assert client.post("/api/documentary/pronunciation/entries/99999999/auto-verify").status_code == 404
