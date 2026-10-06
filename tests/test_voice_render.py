"""Voice rendering: provider request/response, word timing, trimming,
cache, speech-to-text check with re-takes, loudness, assembly, API.

No network: the TTS provider and the ASR are fakes. Real FFmpeg runs
(it is a system requirement of the documentary engine)."""
import asyncio
import base64
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.ai_config import ai_config
from app.documentary.asr import compare_transcript, normalize_tokens
from app.documentary.voice_render import (
    VoiceRenderer, select_blocks, words_from_alignment,
)
from app.providers.voice import VoiceProviderError, VoiceRenderResult, VoiceRequest

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="ffmpeg is required for audio tests"
)

LEAD, SPEECH_PER_CHAR, TAIL = 0.4, 0.02, 0.6


def _mp3(seconds_speech: float) -> bytes:
    """Silence, a tone standing in for speech, silence — as MP3."""
    out = Path(f"/tmp/fake_tts_{uuid.uuid4().hex}.mp3")
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", f"sine=frequency=220:duration={seconds_speech:.3f}:sample_rate=44100",
        "-af", f"adelay={int(LEAD * 1000)},apad=pad_dur={TAIL}",
        "-ac", "1", "-c:a", "libmp3lame", "-b:a", "128k", str(out),
    ], check=True)
    data = out.read_bytes()
    out.unlink()
    return data


class FakeTTS:
    name = "fake_tts"

    def __init__(self):
        self.requests: list[VoiceRequest] = []

    def is_configured(self):
        return True

    async def synthesize(self, req: VoiceRequest) -> VoiceRenderResult:
        self.requests.append(req)
        chars = list(req.text)
        speech = len(chars) * SPEECH_PER_CHAR
        starts = [LEAD + i * SPEECH_PER_CHAR for i in range(len(chars))]
        ends = [s + SPEECH_PER_CHAR for s in starts]
        return VoiceRenderResult(
            audio=_mp3(speech), audio_format="mp3_44100_128",
            characters=chars, char_starts=starts, char_ends=ends,
            provider=self.name, model_id=req.model_id, voice_id=req.voice_id,
            request_id=f"req-{len(self.requests)}", character_cost=len(chars),
        )


class FakeASR:
    """Hears the script exactly — unless told to drop words for a seed."""

    name = "fake_asr"

    def __init__(self, drop_for_seeds=()):
        self.drop_for_seeds = set(drop_for_seeds)
        self.calls = 0

    def transcribe(self, audio_path: str, language: str) -> dict:
        self.calls += 1
        wav = Path(audio_path)
        key = wav.stem.split("__")[-1]  # takes may be reused across block ids
        meta = json.loads(next(wav.parent.glob(f"*__{key}.json")).read_text())
        # audio tags ("[whispers]") are performed, never spoken
        words = re.sub(r"\[[^\[\]]*\]", " ", meta["text"]).split()
        if meta["seed"] in self.drop_for_seeds:
            words = words[:3] + words[8:]  # five words skipped
        return {"text": " ".join(words), "words": []}


def _plan(blocks):
    return {"language": "en", "blocks": blocks}


def _block(bid, section, text):
    words = len(text.split())
    return {
        "block_id": bid, "section_id": section, "text": text,
        "word_count": words, "est_seconds": words / 150 * 60,
        "previous_text": "", "next_text": "", "oversize": False,
    }


TEXT_A = "The house was empty and every room had been scrubbed clean before they left."
TEXT_B = "Four people had vanished from the property without telling anyone where."
TEXT_C = "Months earlier the household had begun to talk about leaving the country."


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    return tmp_path


def _render(renderer, blocks, **kw):
    return asyncio.run(renderer.render(
        _plan(blocks), case_id=7, story_version_id=3, **kw
    ))


# ---------------------------------------------------------------------------
# units
# ---------------------------------------------------------------------------


def test_words_from_character_alignment():
    chars = list("Hi there")
    starts = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
    ends = [s + 0.1 for s in starts]
    assert words_from_alignment(chars, starts, ends) == [
        {"word": "Hi", "start": 0.0, "end": 0.2},
        {"word": "there", "start": 0.3, "end": 0.8},
    ]


def test_select_blocks_takes_whole_leading_blocks():
    blocks = [_block(f"B{i}", "a", "word " * 150) for i in range(4)]  # 60 s each
    assert [b["block_id"] for b in select_blocks(blocks, 100)] == ["B0", "B1"]
    assert len(select_blocks(blocks, None)) == 4


def test_missing_voice_for_language_is_a_clear_error():
    with pytest.raises(ValueError, match="No narrator voice"):
        ai_config.voice.for_language("xx")


# ---------------------------------------------------------------------------
# transcript comparison
# ---------------------------------------------------------------------------


def test_compare_ignores_spelling_dates_possessives_and_spacing():
    script = ("On 16 July 2007, Kadwill’s house in Australia’s South West "
              "stood empty. Chantelle McDougall left a note.")
    heard = ("On the 16th of July 2007, Cadwill's house in Australia's "
             "southwest stood empty. Chantel McDougal left a note.")
    r = compare_transcript(script, heard, "en")
    assert r["passed"] and r["word_error_rate"] == 0.0
    assert {v["script"] for v in r["spelling_variants"]} >= {"chantelle mcdougall"}


def test_compare_flags_a_skipped_phrase_even_with_low_error_rate():
    script = " ".join(f"w{i}" for i in range(100))
    heard = " ".join(f"w{i}" for i in range(100) if not 40 <= i < 44)
    r = compare_transcript(script, heard, "en")
    assert r["word_error_rate"] == 0.04  # below the 8 % limit …
    assert not r["passed"] and "missing_words" in r["failures"]  # … still fails
    assert r["diffs"][0]["type"] == "missing"


def test_compare_flags_invented_words():
    r = compare_transcript("the car was found", "the red car was found near the old river", "en")
    assert not r["passed"]


def test_normalization_keeps_numbers_apart_across_commas():
    assert normalize_tokens("in 2007, forty-five people", "en") == [
        "in", "2007", "45", "people"]


def test_persian_normalization_unifies_letters_and_digits():
    assert normalize_tokens("سال ۲۰۰۷ كتاب‌ها", "fa") == normalize_tokens(
        "سال 2007 کتاب ها", "fa")


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def test_render_trims_times_checks_and_normalizes(workdir):
    tts, asr = FakeTTS(), FakeASR()
    r = VoiceRenderer(provider=tts, asr=asr)
    blocks = [_block("EN_a_01", "act1", TEXT_A), _block("EN_a_02", "act1", TEXT_B),
              _block("EN_b_01", "act2", TEXT_C)]
    m = _render(r, blocks)

    # trimmed to speech + pads (the TTS lead/tail silence is gone)
    cfg = ai_config.voice
    for b, text in zip(m["blocks"], (TEXT_A, TEXT_B, TEXT_C)):
        speech = len(text) * SPEECH_PER_CHAR
        expected = speech + (cfg.lead_pad_ms + cfg.tail_pad_ms) / 1000
        assert abs(b["actual_seconds"] - expected) < 0.08, b
        assert b["asr"]["passed"] and b["attempts"] == 1

    # app-level pauses: short inside an act, longer between acts
    tl = m["timeline"]["blocks"]
    gap_in_act = tl[1]["start"] - tl[0]["end"]
    gap_between_acts = tl[2]["start"] - tl[1]["end"]
    assert abs(gap_in_act - cfg.between_blocks_ms / 1000) < 0.02
    assert abs(gap_between_acts - cfg.between_sections_ms / 1000) < 0.02

    # global word timeline: ordered, offset by block start
    words = m["timeline"]["words"]
    assert [w["start"] for w in words] == sorted(w["start"] for w in words)
    first_b = next(w for w in words if w["block_id"] == "EN_a_02")
    assert first_b["start"] >= tl[1]["start"]
    assert len(words) == sum(len(t.split()) for t in (TEXT_A, TEXT_B, TEXT_C))

    # one loudness target for the whole narration
    assert abs(m["loudness"]["after_lufs"] - ai_config.loudness.narration_target_lufs) < 1.0
    out = Path(ai_config.voice.work_dir) / "7" / "audio" / "en" / "v3"
    assert (out / "narration.mp3").exists() and (out / "manifest.json").exists()
    assert m["characters_paid"] == sum(len(t) for t in (TEXT_A, TEXT_B, TEXT_C))
    assert m["flags"] == []


def test_neighbour_text_and_style_reach_the_provider(workdir):
    tts = FakeTTS()
    b = _block("EN_a_01", "act1", TEXT_A)
    b["previous_text"], b["next_text"] = "Before.", "After."
    _render(VoiceRenderer(provider=tts, asr=FakeASR()), [b], style="controlled_tension")
    req = tts.requests[0]
    assert (req.previous_text, req.next_text) == ("Before.", "After.")
    assert req.settings["speed"] == ai_config.voice.styles["controlled_tension"].speed
    assert req.voice_id == ai_config.voice.for_language("en").voice_id


def test_unchanged_blocks_are_never_paid_twice(workdir):
    tts = FakeTTS()
    blocks = [_block("EN_a_01", "act1", TEXT_A), _block("EN_a_02", "act1", TEXT_B)]
    _render(VoiceRenderer(provider=tts, asr=FakeASR()), blocks)
    assert len(tts.requests) == 2
    blocks[1] = _block("EN_a_02", "act1", TEXT_B.replace("anyone", "a soul"))
    m = _render(VoiceRenderer(provider=tts, asr=FakeASR()), blocks)
    assert len(tts.requests) == 3  # only the edited block
    assert [b["cache_hit"] for b in m["blocks"]] == [True, False]
    assert m["characters_paid"] == len(blocks[1]["text"])


def test_failed_speech_check_triggers_a_new_take(workdir):
    tts = FakeTTS()
    seed = ai_config.voice.seed
    m = _render(VoiceRenderer(provider=tts, asr=FakeASR(drop_for_seeds={seed})),
                [_block("EN_a_01", "act1", TEXT_A)])
    b = m["blocks"][0]
    assert [r.seed for r in tts.requests] == [seed, seed + 1]
    assert b["attempts"] == 2 and b["seed"] == seed + 1
    assert b["asr"]["passed"] and m["flags"] == []


def test_block_failing_every_take_is_flagged(workdir):
    seed = ai_config.voice.seed
    asr = FakeASR(drop_for_seeds={seed + i for i in range(5)})
    m = _render(VoiceRenderer(provider=FakeTTS(), asr=asr),
                [_block("EN_a_01", "act1", TEXT_A)])
    assert m["flags"] == ["EN_a_01:asr_check_failed"]
    assert m["blocks"][0]["asr"]["diffs"][0]["type"] == "missing"


def test_forced_block_gets_a_new_take(workdir):
    tts = FakeTTS()
    blocks = [_block("EN_a_01", "act1", TEXT_A), _block("EN_a_02", "act1", TEXT_B)]
    _render(VoiceRenderer(provider=tts, asr=FakeASR()), blocks)
    _render(VoiceRenderer(provider=tts, asr=FakeASR()), blocks,
            force_block_ids=["EN_a_02"])
    assert len(tts.requests) == 3
    assert tts.requests[-1].text == TEXT_B
    assert tts.requests[-1].seed != tts.requests[1].seed


def test_render_without_asr_is_reported(workdir):
    m = _render(VoiceRenderer(provider=FakeTTS(), use_asr=False),
                [_block("EN_a_01", "act1", TEXT_A)])
    assert m["asr"] is None and m["blocks"][0]["asr"] is None


# ---------------------------------------------------------------------------
# ElevenLabs provider (network replaced)
# ---------------------------------------------------------------------------


def _el(monkeypatch, responses):
    from app.providers.voice.elevenlabs import ElevenLabsVoiceProvider

    monkeypatch.setenv(ai_config.voice.elevenlabs.secret_env, "test-key")
    p = ElevenLabsVoiceProvider()
    sent = []

    async def fake_post(url, body):
        sent.append((url, body))
        return responses.pop(0)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(p, "_post", fake_post)
    monkeypatch.setattr("app.providers.voice.elevenlabs.asyncio.sleep", no_sleep)
    return p, sent


OK = (200, {
    "audio_base64": base64.b64encode(b"ID3fake").decode(),
    "alignment": {"characters": list("Hi"),
                  "character_start_times_seconds": [0.0, 0.1],
                  "character_end_times_seconds": [0.1, 0.2]},
}, {"request-id": "abc", "character-cost": "2"})

REQ = VoiceRequest(text="Hi", voice_id="V1", model_id="eleven_multilingual_v2",
                   settings={"stability": 0.5, "speed": 0.94},
                   previous_text="Before.", next_text="After.", seed=7)


def test_elevenlabs_request_and_parse(monkeypatch):
    p, sent = _el(monkeypatch, [OK])
    res = asyncio.run(p.synthesize(REQ))
    url, body = sent[0]
    assert url.endswith("/v1/text-to-speech/V1/with-timestamps")
    assert body == {
        "text": "Hi", "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "speed": 0.94},
        "previous_text": "Before.", "next_text": "After.", "seed": 7,
    }
    assert res.audio == b"ID3fake" and res.characters == ["H", "i"]
    assert res.request_id == "abc" and res.character_cost == 2


def test_elevenlabs_retries_rate_limit_then_succeeds(monkeypatch):
    p, sent = _el(monkeypatch, [(429, {"detail": "busy"}, {}), OK])
    assert asyncio.run(p.synthesize(REQ)).request_id == "abc"
    assert len(sent) == 2


def test_elevenlabs_permission_error_is_not_retried(monkeypatch):
    p, sent = _el(monkeypatch, [(401, {"detail": {"message": "missing_permissions"}}, {})])
    with pytest.raises(VoiceProviderError) as e:
        asyncio.run(p.synthesize(REQ))
    assert e.value.kind == "unauthorized" and len(sent) == 1


def test_elevenlabs_without_key(monkeypatch):
    from app.providers.voice.elevenlabs import ElevenLabsVoiceProvider

    monkeypatch.delenv(ai_config.voice.elevenlabs.secret_env, raising=False)
    with pytest.raises(VoiceProviderError) as e:
        asyncio.run(ElevenLabsVoiceProvider().synthesize(REQ))
    assert e.value.kind == "missing_credentials"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_voice_render_api(client, db_session, workdir, monkeypatch):
    from app.db.models import Case, StoryVersion
    from app.utils import slugify

    title = f"Voice API {uuid.uuid4().hex[:6]}"
    case = Case(canonical_title=title, slug=slugify(title), language="en")
    db_session.add(case)
    db_session.commit()
    story = StoryVersion(
        case_id=case.id, version=1, kind="master", language="en",
        narrative_angle="{}", narrative_structure=json.dumps({"sections": [
            {"id": "act1", "text": TEXT_A}, {"id": "act2", "text": TEXT_C}]}),
        story_text=TEXT_A + "\n\n" + TEXT_C, text_hash="h",
        engagement_score=80.0, status="ready",
    )
    db_session.add(story)
    db_session.commit()

    import app.main as main_mod
    monkeypatch.setattr(
        main_mod, "VoiceRenderer",
        type("R", (VoiceRenderer,), {
            "__init__": lambda self: VoiceRenderer.__init__(
                self, provider=FakeTTS(), asr=FakeASR())
        }),
    )
    base = f"/api/cases/{case.id}/stories/{story.id}/voice"
    assert client.get(base).status_code == 404
    r = client.post(base + "/render", json={"max_seconds": 60})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["blocks_rendered"] == 2 and body["timeline_word_count"] > 0
    assert "timeline" not in body  # long word list only in the manifest
    assert client.get(base).json()["timeline"]["words"]
    audio = client.get(base + "/narration.mp3")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg"
