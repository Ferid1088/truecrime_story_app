"""Dynamic EQ / de-esser: band isolation, adaptive detection, attack/
release envelopes, attenuation caps, file-level idempotency and the
voice-render integration.

Signal-level tests are pure numpy (deterministic, no ffmpeg); file and
pipeline tests need ffmpeg — a system requirement of the documentary
engine.
"""
import io
import json
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from app.core.ai_config import DynamicEQConfig, ai_config
from app.documentary import dynamic_eq as DEQ

SR = 44100
SETTINGS = DynamicEQConfig().resolved("en")
pytestmark_audio = pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="ffmpeg is required for audio tests"
)


def _sine(hz, seconds, amp=0.1, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    return amp * np.sin(2 * np.pi * hz * t).astype(np.float32), t


def _speechish(seconds=3.0, seed=1, sr=SR):
    """Voice-ish signal: low harmonics + light noise — no harshness."""
    rng = np.random.default_rng(seed)
    _, t = _sine(0, seconds, sr=sr)
    x = (0.05 * np.sin(2 * np.pi * 180 * t)
         + 0.03 * np.sin(2 * np.pi * 540 * t)
         + 0.02 * np.sin(2 * np.pi * 990 * t)
         + 0.008 * rng.standard_normal(len(t)))
    return x.astype(np.float32), t


def _settings(**kw):
    s = dict(SETTINGS)
    s.update(kw)
    return s


# ---------------------------------------------------------------------------
# filter and envelope units
# ---------------------------------------------------------------------------


def test_bandpass_isolates_its_frequency():
    h = DEQ.bandpass(3100, 1500, SR)
    inside, _ = _sine(3100, 1.0)
    outside, _ = _sine(1000, 1.0)
    assert np.sqrt((DEQ.band_filter(inside, h) ** 2).mean()) > \
        10 * np.sqrt((DEQ.band_filter(outside, h) ** 2).mean())


def test_band_filter_is_sample_aligned():
    """Zero effective phase shift — a tap lands where it was."""
    x = np.zeros(SR, dtype=np.float32)
    x[10000] = 1.0
    y = DEQ.band_filter(x, DEQ.bandpass(3100, 1500, SR))
    assert np.argmax(np.abs(y)) == 10000


def test_one_pole_attack_is_faster_than_release():
    target = np.zeros(200)
    target[20:60] = 6.0
    gr = DEQ._one_pole(target, 0.005, attack_ms=10, release_ms=120)
    rise = gr[40] - gr[20]           # 100 ms into the burst
    assert 0 < gr.max() <= 6.0
    assert gr[21] > 0                # it moved at once…
    assert rise > 0.5 * 6.0          # …and reached most of it quickly
    assert gr[80] < gr[60]           # decaying after the burst ends
    assert gr[65] > 0.5 * gr[60]     # but the release is not instant
    assert gr[199] < 0.1 * gr[60]    # and it returns to zero


# ---------------------------------------------------------------------------
# detection behaviour
# ---------------------------------------------------------------------------


def test_clean_speech_is_untouched():
    x, _ = _speechish()
    y, rep = DEQ.process(x, SR, SETTINGS)
    np.testing.assert_array_equal(x, y)
    assert rep["active_seconds_reduced"] == 0.0
    assert all(b["active_seconds"] == 0 for b in rep["bands"])


def test_silence_is_untouched():
    x = np.zeros(SR, dtype=np.float32)
    y, rep = DEQ.process(x, SR, SETTINGS)
    np.testing.assert_array_equal(x, y)
    assert rep["active_seconds_reduced"] == 0.0


def test_harsh_burst_is_reduced_only_during_the_burst():
    x, t = _speechish(seconds=3.0)
    burst = (t >= 1.0) & (t < 1.3)
    x[burst] += (0.3 * np.sin(2 * np.pi * 3100 * t[burst])).astype(np.float32)

    y, rep = DEQ.process(x, SR, SETTINGS)
    band = rep["bands"][0]
    assert band["name"] == "harsh_3k"
    assert 0 < band["max_gr_db"] <= 6.0
    assert band["events"], "the burst must be documented as an event"
    ev = band["events"][0]
    assert ev["start"] <= 1.05 and ev["end"] >= 1.3  # caught the burst
    # outside the event (plus a release margin) the signal is effectively
    # unchanged — sub-threshold moments may still get a tiny cut
    assert np.abs(x[: int(0.8 * SR)] - y[: int(0.8 * SR)]).max() < 0.02
    assert np.abs(x[int(1.8 * SR):] - y[int(1.8 * SR):]).max() < 0.02
    # inside the burst the band really was cut
    seg = slice(int(1.1 * SR), int(1.25 * SR))
    bi = DEQ.band_filter(x[seg], DEQ.bandpass(3100, 1500, SR))
    bo = DEQ.band_filter(y[seg], DEQ.bandpass(3100, 1500, SR))
    cut_db = 10 * np.log10((bi**2).mean() / max((bo**2).mean(), 1e-12))
    assert cut_db > 2.0


def test_max_attenuation_is_capped():
    x, t = _speechish(seconds=2.0)
    x += (0.5 * np.sin(2 * np.pi * 3100 * t)).astype(np.float32)  # brutally harsh
    _, rep = DEQ.process(x, SR, SETTINGS)
    assert rep["bands"][0]["max_gr_db"] <= 6.0


def test_constant_harsh_resonance_is_still_detected():
    """A pathological resonance loud enough to BECOME the voice's own
    baseline must not be accepted as normal — the threshold ceiling
    keeps it in treatment."""
    x, t = _speechish(seconds=3.0)
    x += (0.5 * np.sin(2 * np.pi * 3100 * t)).astype(np.float32)  # constant
    _, rep = DEQ.process(x, SR, SETTINGS)
    band = rep["bands"][0]
    assert band["max_gr_db"] > 0, "constant severe resonance slipped through"
    assert band["events"], "must be documented as a harshness event"
    # …while a mild consistent brightness IS accepted as the voice
    x2, t2 = _speechish(seconds=3.0)
    x2 += (0.05 * np.sin(2 * np.pi * 3100 * t2)).astype(np.float32)
    _, rep2 = DEQ.process(x2, SR, SETTINGS)
    assert rep2["bands"][0]["max_gr_db"] == 0.0


def test_zero_strength_disables_reduction():
    x, t = _speechish(seconds=2.0)
    x += (0.3 * np.sin(2 * np.pi * 3100 * t)).astype(np.float32)
    y, rep = DEQ.process(x, SR, _settings(strength=0.0))
    np.testing.assert_array_equal(x, y)


def test_output_never_exceeds_input_peak_and_keeps_length():
    x, t = _speechish(seconds=2.0)
    x += (0.3 * np.sin(2 * np.pi * 3100 * t)).astype(np.float32)
    y, _ = DEQ.process(x, SR, SETTINGS)
    assert len(y) == len(x)                          # pitch/speed preserved
    assert np.abs(y).max() <= np.abs(x).max() + 1e-4  # attenuation-only


def test_output_has_no_sample_discontinuities():
    """Envelopes are smooth: no step larger than a plausible audio slope."""
    x, t = _speechish(seconds=2.0)
    x += (0.3 * np.sin(2 * np.pi * 3100 * t)).astype(np.float32)
    y, _ = DEQ.process(x, SR, SETTINGS)
    assert np.abs(np.diff(y)).max() < 0.2


def test_deesser_reduces_excessive_sibilance():
    rng = np.random.default_rng(3)
    x, t = _speechish(seconds=2.0)
    sib = (t >= 0.8) & (t < 1.0)
    hiss = DEQ.band_filter(rng.standard_normal(len(x)).astype(np.float32),
                           DEQ.bandpass(6500, 3000, SR))
    x[sib] += 0.4 * hiss[sib]
    y, rep = DEQ.process(x, SR, SETTINGS)
    de = rep["deesser"]
    assert de and de["max_gr_db"] > 0
    ev = de["events"][0]
    assert ev["start"] <= 0.85   # fast attack caught the /s/ near its start
    seg = slice(int(0.85 * SR), int(0.95 * SR))
    si = DEQ.band_filter(x[seg], DEQ.bandpass(6500, 3000, SR))
    so = DEQ.band_filter(y[seg], DEQ.bandpass(6500, 3000, SR))
    assert (si**2).mean() > 4 * (so**2).mean()  # ≥6 dB actually removed


def test_deesser_can_be_disabled_independently():
    rng = np.random.default_rng(3)
    x, t = _speechish(seconds=2.0)
    sib = (t >= 0.8) & (t < 1.0)
    hiss = DEQ.band_filter(rng.standard_normal(len(x)).astype(np.float32),
                           DEQ.bandpass(6500, 3000, SR))
    x[sib] += 0.4 * hiss[sib]
    s = _settings(deesser={**SETTINGS["deesser"], "enabled": False})
    _, rep = DEQ.process(x, SR, s)
    assert rep["deesser"] is None


@pytest.mark.parametrize("lang,f0", [("en", 150), ("de", 130),
                                     ("ar", 170), ("fa", 140)])
def test_per_language_profile(lang, f0):
    """Every shipped language profile: harshness is caught, duration and
    pitch are preserved and processing never adds level (no clipping)."""
    s = DynamicEQConfig().resolved(lang)
    t = np.arange(int(3.0 * SR)) / SR
    x = (0.05 * np.sin(2 * np.pi * f0 * t)
         + 0.03 * np.sin(2 * np.pi * 3 * f0 * t)
         + 0.008 * np.random.default_rng(1)
         .standard_normal(len(t))).astype(np.float32)
    burst = (t >= 1.0) & (t < 1.3)
    x[burst] += (0.3 * np.sin(2 * np.pi * 3100 * t[burst])).astype(np.float32)

    y, rep = DEQ.process(x, SR, s)
    assert len(y) == len(x)                              # duration/timing
    assert np.abs(y).max() <= np.abs(x).max() + 1e-4     # attenuation only
    band = next(b for b in rep["bands"] if b["name"] == "harsh_3k")
    assert 0 < band["max_gr_db"] <= 6.0
    # pitch preserved: the fundamental band leaves the engine untouched
    h = DEQ.bandpass(f0, 0.4 * f0, SR)
    np.testing.assert_allclose(DEQ.band_filter(x, h), DEQ.band_filter(y, h),
                               atol=1e-4)


def test_adaptive_baseline_follows_the_voice_not_a_preset():
    """A consistently bright voice is normal FOR THAT VOICE: the same
    absolute band level that is ordinary in a bright recording is
    treated as excessive in a dark one."""
    bright, t = _speechish(seconds=3.0)
    bright += (0.08 * np.sin(2 * np.pi * 3100 * t)).astype(np.float32)
    dark, _ = _speechish(seconds=3.0)
    b = (t >= 1.0) & (t < 1.3)
    dark[b] += (0.08 * np.sin(2 * np.pi * 3100 * t[b])).astype(np.float32)
    _, rb = DEQ.process(bright, SR, SETTINGS)
    _, rd = DEQ.process(dark, SR, SETTINGS)
    assert rb["bands"][0]["baseline_db"] > rd["bands"][0]["baseline_db"]
    # …and only the dark recording's flare-up is actually reduced
    assert rd["bands"][0]["max_gr_db"] > rb["bands"][0]["max_gr_db"]


# ---------------------------------------------------------------------------
# config: language profiles and persistence
# ---------------------------------------------------------------------------


def test_language_override_shifts_thresholds():
    cfg = DynamicEQConfig(
        languages={"de": DynamicEQConfig().languages.get("de") or
                   {"threshold_offset_delta_db": -2.0}}
    )
    base = cfg.resolved("en")["bands"][0]["threshold_offset_db"]
    de = cfg.resolved("de")["bands"][0]["threshold_offset_db"]
    assert de == base - 2.0


def test_unknown_language_gets_the_base_profile():
    cfg = DynamicEQConfig()
    assert cfg.resolved("xx")["bands"][0]["threshold_offset_db"] == \
        cfg.resolved("en")["bands"][0]["threshold_offset_db"]


def test_save_dynamic_eq_persists_and_updates(tmp_path, monkeypatch):
    import app.core.ai_config as mod

    src = Path(mod.CONFIG_PATH).read_text()
    fake = tmp_path / "ai_config.json"
    fake.write_text(src)
    monkeypatch.setattr(mod, "CONFIG_PATH", fake)
    old = ai_config.dynamic_eq.strength
    try:
        cfg = ai_config.dynamic_eq.model_copy(update={"strength": 0.5})
        mod.save_dynamic_eq(cfg)
        assert ai_config.dynamic_eq.strength == 0.5
        assert json.loads(fake.read_text())["dynamic_eq"]["strength"] == 0.5
    finally:
        mod.save_dynamic_eq(ai_config.dynamic_eq.model_copy(
            update={"strength": old}))


# ---------------------------------------------------------------------------
# file-level driver (ffmpeg)
# ---------------------------------------------------------------------------


def _wav(path: Path, samples: np.ndarray, sr=SR):
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1, 1)
    pcm = (pcm * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


@pytestmark_audio
def test_process_into_writes_enhanced_and_report(tmp_path):
    x, t = _speechish(seconds=2.0)
    b = (t >= 0.7) & (t < 1.1)
    x[b] += (0.3 * np.sin(2 * np.pi * 3100 * t[b])).astype(np.float32)
    src = _wav(tmp_path / "narration.wav", x)
    rep = DEQ.process_into(src, tmp_path, "en", SETTINGS, SR)
    assert rep["applied"] and rep["active"] == "enhanced"
    assert (tmp_path / "narration_enhanced.wav").exists()
    side = json.loads((tmp_path / "dynamics.json").read_text())
    assert side["bands"][0]["curve"] and side["bands"][0]["events"]
    # duration preserved exactly (sample-aligned processing)
    from app.documentary.audio import probe_duration
    assert abs(probe_duration(tmp_path / "narration_enhanced.wav")
               - probe_duration(src)) < 0.01


@pytestmark_audio
def test_process_into_is_idempotent(tmp_path):
    x, _ = _speechish(seconds=1.5)
    src = _wav(tmp_path / "narration.wav", x)
    first = DEQ.process_into(src, tmp_path, "en", SETTINGS, SR)
    second = DEQ.process_into(src, tmp_path, "en", SETTINGS, SR)
    assert first["applied"] and second["cached"]
    # changed settings invalidate the cache → processed again
    third = DEQ.process_into(src, tmp_path, "en",
                             _settings(strength=0.5), SR)
    assert not third.get("cached")


@pytestmark_audio
def test_process_into_disabled_cleans_stale_output(tmp_path):
    x, _ = _speechish(seconds=1.0)
    src = _wav(tmp_path / "narration.wav", x)
    DEQ.process_into(src, tmp_path, "en", SETTINGS, SR)
    rep = DEQ.process_into(src, tmp_path, "en",
                           _settings(enabled=False), SR)
    assert rep == {"enabled": False, "applied": False, "active": "original"}
    assert not (tmp_path / "narration_enhanced.wav").exists()
    assert not (tmp_path / "dynamics.json").exists()


# ---------------------------------------------------------------------------
# end-to-end through the voice renderer (fake TTS, real ffmpeg + DSP)
# ---------------------------------------------------------------------------


@pytestmark_audio
def test_voice_render_applies_dynamic_eq(tmp_path, monkeypatch):
    """The pipeline: TTS blocks → assembly → normalize → dynamic EQ on the
    ORIGINAL narration → enhanced file + manifest report."""
    import asyncio
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from test_voice_render import FakeASR, SPEECH_PER_CHAR, LEAD, _block
    from app.documentary.voice_render import VoiceRenderer
    from app.providers.voice import VoiceRenderResult

    class HarshTTS:
        name = "harsh_tts"

        def is_configured(self):
            return True

        async def synthesize(self, req):
            n_speech = int(len(req.text) * SPEECH_PER_CHAR * SR)
            pad = int(LEAD * SR)
            t = np.arange(n_speech) / SR
            pulse = ((t % 0.5) < 0.15).astype(np.float32)  # harsh bursts,
            sig = (0.08 * np.sin(2 * np.pi * 220 * t)      # not constant —
                   + 0.05 * np.sin(2 * np.pi * 660 * t)    # constant bright
                   + 0.4 * pulse * np.sin(2 * np.pi * 3100 * t))  # = baseline
            audio = np.concatenate(
                [np.zeros(pad, dtype=np.float32),
                 sig.astype(np.float32),
                 np.zeros(int(0.6 * SR), dtype=np.float32)])
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes((np.clip(audio, -1, 1) * 32767)
                              .astype("<i2").tobytes())
            chars = list(req.text)
            starts = [LEAD + i * SPEECH_PER_CHAR for i in range(len(chars))]
            ends = [s + SPEECH_PER_CHAR for s in starts]
            return VoiceRenderResult(
                audio=buf.getvalue(), audio_format="wav",
                characters=chars, char_starts=starts, char_ends=ends,
                provider=self.name, model_id=req.model_id,
                voice_id=req.voice_id, request_id="r1",
                character_cost=len(chars))

    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    monkeypatch.setattr(ai_config.dynamic_eq, "enabled", True)
    text = "The house stood silent on the hill above the cold grey water."
    plan = {"language": "en", "blocks": [_block("B1", "a", text)]}
    m = asyncio.run(VoiceRenderer(provider=HarshTTS(), asr=FakeASR())
                    .render(plan, case_id=7, story_version_id=3))

    eq = m["dynamic_eq"]
    assert eq["applied"], "a harsh narration must be processed"
    out = tmp_path / "7" / "audio" / "en" / "v3"
    assert (out / "narration.wav").exists()            # original kept
    assert (out / "narration_enhanced.wav").exists()   # enhanced written
    assert (out / "narration_original.mp3").exists()   # A/B side
    assert (out / "dynamics.json").exists()            # GR documentation
    assert m["files"]["narration_enhanced_wav"]
    band = next(b for b in eq["bands"] if b["name"] == "harsh_3k")
    assert band["max_gr_db"] > 0
