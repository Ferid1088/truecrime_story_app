"""The phonetic benchmark: candidates, opinions, consensus versus ground truth, the recognizer comparison."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from app.db.models import PhoneticLabel, PhoneticSample
from app.documentary import phonetic_benchmark as PB


@pytest.fixture
def language(db_session):
    lang = f"fa-t{uuid.uuid4().hex[:8]}"
    yield lang
    rows = db_session.query(PhoneticSample).filter_by(language=lang).all()
    ids = [s.id for s in rows]
    for s in rows:
        if s.source == "recording" and s.audio_path.startswith("uploads/") and not s.audio_path.startswith(f"uploads/{lang}"):
            (PB.BASE / s.audio_path).unlink(missing_ok=True)
    db_session.query(PhoneticLabel).filter(PhoneticLabel.sample_id.in_(ids)).delete(synchronize_session=False)
    db_session.query(PhoneticSample).filter_by(language=lang).delete(synchronize_session=False)
    db_session.commit()


@pytest.fixture
def sample(db_session, language):
    s, _ = PB.add_sample(db_session, language=language, source="recording", surface="عهد", sentence="از عهد حرف زد", audio_path=f"uploads/{language}.wav", category="short_vowel",
                         candidates=json.dumps(PB.candidates_for("ahd", "short_vowel"), ensure_ascii=False), expected_key="ahd")
    return s


def test_candidates_hold_the_expected_reading_first_and_one_vowel_variants():
    keys = [c["key"] for c in PB.candidates_for("ahd", "short_vowel")]
    assert keys[0] == "ahd" and len(keys) == 3 and all(len(k) == 3 for k in keys)


def test_an_ezafe_clip_is_a_pair_with_and_without_the_ezafe():
    keys = [c["key"] for c in PB.candidates_for("dokhtar-e", "ezafe")]
    assert keys[0] == "dokhtar-e" and keys[1] == "dokhtar"


def test_a_name_is_a_name_before_anything_else():
    assert PB.category_of("تاد", "tād", {"تاد"}) == "name"


def test_the_same_clip_is_one_sample(db_session, sample, language):
    again, new = PB.add_sample(db_session, language=language, source="recording", surface="x", sentence="", audio_path=sample.audio_path, category="other", candidates="[]")
    assert not new and again.id == sample.id


def test_a_choice_that_is_not_a_candidate_is_refused(db_session, sample):
    with pytest.raises(ValueError):
        PB.record_label(db_session, sample, kind="agent", actor="a", choice="zzz")


def test_two_agreeing_opinions_are_consensus_not_ground_truth(db_session, sample):
    PB.record_label(db_session, sample, kind="agent", actor="listener-1", choice="ahd")
    PB.record_label(db_session, sample, kind="recognizer", actor="wav2vec2", choice="ahd")
    PB.settle(db_session, sample)
    assert sample.status == "agent_consensus" and sample.consensus_key == "ahd" and sample.human_key is None


def test_different_opinions_are_disputed_and_one_opinion_is_not_enough(db_session, sample):
    PB.record_label(db_session, sample, kind="agent", actor="listener-1", choice="ahd")
    assert PB.settle(db_session, sample).status == "unlabeled"
    PB.record_label(db_session, sample, kind="agent", actor="listener-2", choice="ohd")
    assert PB.settle(db_session, sample).status == "disputed"


def test_an_unclear_opinion_prevents_consensus(db_session, sample):
    PB.record_label(db_session, sample, kind="agent", actor="listener-1", choice="ahd")
    PB.record_label(db_session, sample, kind="recognizer", actor="allosaurus", choice="unclear")
    assert PB.settle(db_session, sample).status == "disputed"


def test_a_person_overrides_consensus_and_is_the_only_ground_truth(db_session, sample):
    PB.record_label(db_session, sample, kind="agent", actor="listener-1", choice="ahd")
    PB.record_label(db_session, sample, kind="agent", actor="listener-2", choice="ahd")
    PB.settle(db_session, sample)
    PB.human_label(db_session, sample.id, "ohd", "feri")
    assert sample.status == "human_verified" and sample.human_key == "ohd"


def test_a_label_needs_a_named_listener(db_session, sample):
    with pytest.raises(PermissionError):
        PB.human_label(db_session, sample.id, "ahd", " ")


def test_a_person_can_mark_a_clip_unclear(db_session, sample):
    PB.human_label(db_session, sample.id, "unclear", "feri")
    assert sample.status == "unclear" and sample.human_key is None


def test_the_recognizer_picks_the_nearer_candidate_and_ties_are_unclear(db_session, sample):
    label = PB.recognizer_opinion(db_session, sample, "fake", list("æhd"))
    assert label.choice == "ahd"
    tie = PB.recognizer_opinion(db_session, sample, "fake2", list("hd"))
    assert tie.choice == "unclear" and tie.confidence == 0.0


def test_the_agents_see_the_options_in_other_orders_and_their_picks_are_kept(db_session, sample, monkeypatch):
    monkeypatch.setattr(PB, "audio_data_url", lambda s: "data:audio/wav;base64,")
    seen = []

    async def runner(payload, audio):
        seen.append(payload["options"])
        return {"choice": next(k for k, v in payload["options"].items() if v == "ahd"), "sure": True}

    labels = asyncio.run(PB.agent_opinions(db_session, sample, runner))
    assert [x.choice for x in labels] == ["ahd", "ahd"] and len(seen) == 2


def test_the_report_scores_everyone_against_the_human_labels_and_checks_the_consensus(db_session, sample, language):
    PB.record_label(db_session, sample, kind="agent", actor="listener-1", choice="ahd")
    PB.record_label(db_session, sample, kind="recognizer", actor="wav2vec2", choice="ahd")
    PB.human_label(db_session, sample.id, "ohd", "feri")
    got = PB.report(db_session, language)
    assert got["human_verified"] == 1 and got["against_human_labels"]["wav2vec2"] == {"clips": 1, "right": 0, "unclear": 0, "accuracy": 0.0}
    assert got["consensus_checked_by_a_person"] == {"clips": 1, "consensus_right": 0}
    assert got["enough_for_a_verdict"] is False


def test_a_rerun_of_the_extraction_reaches_the_same_set_and_adds_nothing(db_session, language, tmp_path, monkeypatch):
    wav = tmp_path / "b1.wav"
    wav.write_bytes(b"")
    text = "یک عهد بزرگ دارد"
    wav.with_suffix(".json").write_text(json.dumps({"text": text, "voice_id": "v", "model_id": "m"}), encoding="utf-8")
    words = [{"block_id": "b1", "start": i * 1.0, "end": i * 1.0 + 0.5} for i in range(4)]
    (tmp_path / "manifest.json").write_text(json.dumps({"timeline": {"blocks": [{"block_id": "b1", "wav": str(wav), "start": 0.0}], "words": words}}), encoding="utf-8")
    cache = {"reader": {}, "blocks": {"b1": {"readings": [{"latin": x} for x in ("yek", "ahd", "bozorg", "dārad")]}}}
    monkeypatch.setattr(PB, "cut", lambda *a: None)
    monkeypatch.setattr(PB, "ROOT", tmp_path.parent)
    first = PB.extract(db_session, 0, 0, tmp_path, cache, language, quotas={"short_vowel": 2})
    second = PB.extract(db_session, 0, 0, tmp_path, cache, language, quotas={"short_vowel": 2})
    assert sum(first["created"].values()) == 2 and sum(second["created"].values()) == 0
    assert db_session.query(PhoneticSample).filter_by(language=language).count() == 2


@pytest.fixture
def tone(tmp_path):
    import subprocess

    path = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=0.6", str(path)], check=True)
    return path.read_bytes()


def test_an_uploaded_clip_is_converted_listed_and_streamed(client, db_session, language, tone):
    got = client.post("/api/documentary/phonetic/samples", files={"file": ("a.wav", tone, "audio/wav")},
                      data={"surface": "عهد", "candidates": "ahd, ohd", "category": "short_vowel", "speaker": "owner", "speed": "slow", "language": language})
    assert got.status_code == 200 and got.json()["status"] == "unlabeled" and [c["key"] for c in got.json()["candidates"]] == ["ahd", "ohd"]
    sid = got.json()["id"]
    assert client.get(f"/api/documentary/phonetic/samples/{sid}/audio").status_code == 200
    assert len(client.get(f"/api/documentary/phonetic/samples/{sid}/peaks").json()["peaks"]) == 160



def test_the_page_labels_a_clip_only_with_a_named_listener_and_a_real_candidate(client, sample):
    url = f"/api/documentary/phonetic/samples/{sample.id}/label"
    assert client.post(url, json={"choice": "ahd", "reviewer": ""}).status_code == 422
    assert client.post(url, json={"choice": "zzz", "reviewer": "feri"}).status_code == 422
    done = client.post(url, json={"choice": "ahd", "reviewer": "feri"}).json()
    assert done["status"] == "human_verified" and done["human_key"] == "ahd"


def test_an_upload_with_one_candidate_is_refused(client, tone):
    got = client.post("/api/documentary/phonetic/samples", files={"file": ("a.wav", tone, "audio/wav")}, data={"surface": "x", "candidates": "ahd", "category": "other"})
    assert got.status_code == 422


def test_the_listing_puts_disputed_clips_first(db_session, sample, language):
    other, _ = PB.add_sample(db_session, language=language, source="recording", surface="y", sentence="", audio_path=f"uploads/{language}-2.wav", category="other",
                             candidates=json.dumps(PB.candidates_for("ahd", "short_vowel")), expected_key="ahd")
    PB.record_label(db_session, other, kind="agent", actor="a", choice="ahd")
    PB.record_label(db_session, other, kind="agent", actor="b", choice="ohd")
    PB.settle(db_session, other)
    assert [s["status"] for s in PB.listing(db_session, language)["samples"]] == ["disputed", "unlabeled"]
