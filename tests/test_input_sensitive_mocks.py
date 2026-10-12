"""Phase 2 contract tests for the mocked four-language provider boundaries."""

import asyncio
import json
import shutil
from dataclasses import replace

import pytest
from mock_payload import request_hash
from test_spoken_audio import FakeSound
from test_visual_production import PipelineGen, _story
from test_voice_render import FakeASR, FakeTTS

from app.core.ai_config import ai_config
from app.providers.voice.base import VoiceRequest

pytest_plugins = ["test_visual_production"]


def test_generation_text_changes_for_one_request_field_and_repeats_identically():
    async def run(system):
        return await PipelineGen().generate_text(
            "spoken_writer",
            system,
            json.dumps({"script": "[[ACT:B01]]\n\nalpha beta"}),
            images=["data:image/jpeg;base64,AA=="],
        )

    first = asyncio.run(run("system-a"))
    repeat = asyncio.run(run("system-a"))
    changed = asyncio.run(run("system-b"))
    assert first.text == repeat.text
    assert first.text != changed.text


def test_generation_structured_changes_for_one_request_field_and_repeats_identically():
    async def run(system):
        return await PipelineGen().generate_structured("narrative_director", system, "{}")

    first, _ = asyncio.run(run("system-a"))
    repeat, _ = asyncio.run(run("system-a"))
    changed, _ = asyncio.run(run("system-b"))
    assert first == repeat
    assert first["_mock_request_sha256"] != changed["_mock_request_sha256"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required for media fakes")
def test_tts_and_asr_fakes_hash_complete_requests(tmp_path):
    req = VoiceRequest("hello world", "voice-a", "model-a", {"speed": 1}, language="en")
    tts = FakeTTS()
    first = asyncio.run(tts.synthesize(req))
    repeat = asyncio.run(tts.synthesize(req))
    changed = asyncio.run(tts.synthesize(replace(req, voice_id="voice-b")))
    assert (first.audio, first.request_id) == (repeat.audio, repeat.request_id)
    assert (first.audio, first.request_id) != (changed.audio, changed.request_id)

    audio = tmp_path / "take__7.mp3"
    audio.write_bytes(first.audio)
    (tmp_path / "block__7.json").write_text(
        json.dumps({"text": req.text, "seed": 1}), encoding="utf-8"
    )
    asr = FakeASR()
    heard = asr.transcribe(str(audio), "en")
    heard_repeat = asr.transcribe(str(audio), "en")
    changed_language = asr.transcribe(str(audio), "de")
    assert heard == heard_repeat
    assert heard["request_hash"] != changed_language["request_hash"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required for media fakes")
def test_music_fake_hashes_complete_request():
    async def run(prompt):
        return await FakeSound().generate(prompt, 0.1, False, 0.5)

    first = asyncio.run(run("mystery"))
    repeat = asyncio.run(run("mystery"))
    changed = asyncio.run(run("calm"))
    assert first == repeat
    assert first != changed


def test_generation_and_host_ignore_config_not_sent_to_provider(monkeypatch):
    async def generation():
        return await PipelineGen().generate_text(
            "spoken_writer", "system",
            json.dumps({"language": "en", "script": "[[ACT:B01]]\n\nhello"}),
        )

    first = asyncio.run(generation())
    monkeypatch.setattr(ai_config.production_policies.incremental, "mode", "verify")
    monkeypatch.setattr(ai_config.production_policies.incremental, "enforced_types", ["intro"])
    unchanged = asyncio.run(generation())
    assert first.text == unchanged.text

    from test_host import HostGen

    user = json.dumps({"language": "en", "segments": [
        {"id": "H1", "target_words": 5},
    ]})
    host_a, _ = asyncio.run(HostGen().generate_structured("host_writer", "system", user))
    monkeypatch.setattr(ai_config.channels["en"], "name", "unrelated-test-channel")
    host_b, _ = asyncio.run(HostGen().generate_structured("host_writer", "system", user))
    assert host_a == host_b


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required for media fakes")
def test_tts_asr_and_music_ignore_local_only_config(monkeypatch, tmp_path):
    req = VoiceRequest("hello world", "voice-a", "model-a", {"speed": 1}, language="en")
    tts = FakeTTS()
    before_tts = asyncio.run(tts.synthesize(req))
    monkeypatch.setattr(ai_config.voice, "lead_pad_ms", ai_config.voice.lead_pad_ms + 1)
    after_tts = asyncio.run(tts.synthesize(req))
    assert (before_tts.audio, before_tts.request_id) == (after_tts.audio, after_tts.request_id)

    audio = tmp_path / "take__7.mp3"
    audio.write_bytes(before_tts.audio)
    (tmp_path / "block__7.json").write_text(
        json.dumps({"text": req.text, "seed": 1}), encoding="utf-8"
    )
    asr = FakeASR()
    before_asr = asr.transcribe(str(audio), "en")
    monkeypatch.setattr(ai_config.asr_check, "max_word_error_rate", 0.2)
    after_asr = asr.transcribe(str(audio), "en")
    assert before_asr == after_asr

    async def music():
        return await FakeSound().generate("mystery", 0.1, False, 0.5)

    before_music = asyncio.run(music())
    monkeypatch.setattr(ai_config.music_library, "reference_lufs", -12.0)
    after_music = asyncio.run(music())
    assert before_music == after_music


def test_visual_search_geocode_and_map_fakes_hash_complete_requests(
    db_session, documentary_env, tmp_path
):
    from app.agents.visual_research import VisualResearchAgent
    from app.documentary.visuals import generated as generated_mod

    case, _ = _story(db_session)
    agent = VisualResearchAgent()
    query = [{"entity": "the_house", "query": "farmhouse"}]
    first = asyncio.run(agent.run(db_session, case, query, found_during="research"))
    repeat = asyncio.run(agent.run(db_session, case, query, found_during="research"))
    changed = asyncio.run(agent.run(db_session, case, query, found_during="production"))
    assert first == repeat
    assert first["request_hash"] != changed["request_hash"]

    geo = generated_mod.MAPS.geocode
    g1 = asyncio.run(geo("Nannup", near=None))
    g2 = asyncio.run(geo("Nannup", near=None))
    g3 = asyncio.run(geo("Nannup", near=(1, 2)))
    assert g1 == g2 and g1["request_hash"] != g3["request_hash"]

    out = tmp_path / "map.jpg"
    m1 = asyncio.run(generated_mod.MAPS.render_map(1, 2, 3, out))
    m2 = asyncio.run(generated_mod.MAPS.render_map(1, 2, 3, out))
    m3 = asyncio.run(generated_mod.MAPS.render_map(9, 2, 3, out))
    assert m1 == m2 and m1["request_hash"] != m3["request_hash"]


def test_request_hash_changes_for_each_payload_field():
    base = {
        "prompt": "a",
        "system": "b",
        "model": "c",
        "params": {"x": 1},
        "attachments": ["a"],
        "language": "en",
    }
    for field, value in (
        ("prompt", "z"),
        ("system", "z"),
        ("model", "z"),
        ("params", {"x": 2}),
        ("attachments", ["b"]),
        ("language", "de"),
    ):
        changed = dict(base, **{field: value})
        assert request_hash("contract", **base) != request_hash("contract", **changed)
