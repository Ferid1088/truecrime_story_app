"""The end of the film: deep music from the avatar's last words that never covers the voice, 30 s after it, the logo fading in slowly."""

from __future__ import annotations

import subprocess

import numpy as np
import pytest

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin
from app.db.models import MusicTrack
from app.documentary.production import outro as OUTRO
from app.documentary.render.engine import FrameMaker

RATE = 44100
REAL_APPLY = OUTRO.apply          # taken when the file loads: another test file replaces `apply` for its pipeline runs


def tone(path, seconds, freq, level=0.5, channels=2):
    t = np.arange(int(seconds * RATE)) / RATE
    wave = (np.sin(2 * np.pi * freq * t) * level * 32767).astype("<i2")
    data = np.repeat(wave[:, None], channels, axis=1)
    with open(path, "wb") as f:
        import wave as _w

        w = _w.open(f, "wb")
        w.setnchannels(channels); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(data.tobytes()); w.close()


def samples(path):
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32767


def rms_db(x):
    return 20 * np.log10(max(float(np.sqrt(np.mean(x ** 2))), 1e-9))


def test_the_music_starts_quietly_stays_below_the_voice_at_its_end_and_then_rises_and_fades_out_at_the_end():
    cfg = ai_config.outro_settings()
    lead, total = cfg.lead_seconds, cfg.lead_seconds + cfg.after_seconds
    assert OUTRO.gain_db(0.0, lead, total) == pytest.approx(cfg.start_db)
    assert OUTRO.gain_db(lead, lead, total) == pytest.approx(cfg.speech_db) and cfg.speech_db < -20, "under the voice it is far below it"
    assert OUTRO.gain_db(lead + cfg.rise_seconds, lead, total) == pytest.approx(cfg.full_db)
    assert OUTRO.gain_db(total, lead, total) <= -59
    levels = [OUTRO.gain_db(t, lead, total) for t in np.linspace(0, total - cfg.fade_out_seconds, 40)]
    assert all(b >= a - 1e-9 for a, b in zip(levels, levels[1:], strict=False)), "it only gets louder until the end fades it"


def test_the_ffmpeg_envelope_is_the_same_line(tmp_path):
    cfg = ai_config.outro_settings()
    lead, total = cfg.lead_seconds, cfg.lead_seconds + cfg.after_seconds
    music, out = tmp_path / "m.wav", tmp_path / "o.wav"
    tone(music, 10, 330, 0.5)
    run = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-stream_loop", "-1", "-i", str(music), "-filter_complex",
                          f"[0:a]atrim=0:{total},asetpts=PTS-STARTPTS,volume='{OUTRO.expression(lead, total)}':eval=frame[a]", "-map", "[a]", str(out)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    x = samples(out)
    for second in (1.0, 4.5, 8.0, 12.0, 20.0, 33.0):
        window = x[int(second * RATE):int((second + 0.5) * RATE)]
        assert abs(rms_db(window) - (rms_db(np.full(10, 0.5 / np.sqrt(2))) + OUTRO.gain_db(second + 0.25, lead, total))) < 2.5, second


def test_the_film_is_thirty_seconds_longer_than_the_avatars_last_word_and_the_music_never_covers_the_voice(tmp_path, db_session):
    cfg = ai_config.outro_settings()
    base, track = tmp_path / "film.wav", tmp_path / "bed.wav"
    tone(base, 40, 220, 0.5)                                 # 40 s of "speech": the avatar speaks to the very end
    tone(track, 20, 440, 0.5)                                # the library's loopable track
    out = tmp_path / "out.wav"
    run = subprocess.run(OUTRO.mix_command(base, track, out, 40.0, 70.0), capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    x = samples(out)
    assert abs(len(x) / RATE - 70.0) < 0.1, "the film is 30 s longer than the voice"
    spoken = x[int(30 * RATE):int(40 * RATE)]                # the last 10 s of speech, 5 of them with music
    voice_only = samples(base)[int(30 * RATE):int(40 * RATE)]
    quiet = x[int(35 * RATE):int(40 * RATE)]
    assert rms_db(spoken) - rms_db(voice_only) < 0.6, "the music adds less than half a dB to the voice"
    after = x[int(55 * RATE):int(60 * RATE)]
    assert rms_db(after) > rms_db(quiet) - 40 and rms_db(after) > -30, "after the voice the music is heard"
    assert rms_db(x[int(69.5 * RATE):]) < rms_db(after) - 20, "and it fades out at the very end"
    silent_before = x[int(20 * RATE):int(34.5 * RATE)]
    assert abs(rms_db(silent_before) - rms_db(samples(base)[int(20 * RATE):int(34.5 * RATE)])) < 0.2, "nothing before the lead"


def test_the_script_gets_an_outro_shot_the_new_length_and_the_new_audio(tmp_path, db_session, monkeypatch):
    base, track = tmp_path / "film.wav", tmp_path / "bed.wav"
    tone(base, 12, 220); tone(track, 20, 440)
    db_session.add(MusicTrack(kind="bridge", mood="uncertainty", variant=1, file_path=str(tmp_path / "never_made.wav"), track_code="a_test",
                              situation="its file was never made", tags_json='["bridge"]'))
    db_session.add(MusicTrack(kind="bridge", mood="uncertainty", variant=2, file_path=str(track), track_code="b_test",
                              situation="unresolved, reflective interlude", tags_json='["bridge", "reflective"]'))
    db_session.commit()
    monkeypatch.setattr("app.documentary.storage.production_dir", lambda case, language: tmp_path)
    script = {"duration": 12.0, "audio": {"path": str(base)}, "shots": [{"kind": "video", "start": 6.0, "end": 12.0}],
              "host": [{"position": "final", "start": 6.0, "end": 12.0}], "music": []}
    done = REAL_APPLY(db_session, script, 1, "en", 7)
    assert done["duration"] == 42.0 and done["shots"][-1]["kind"] == "outro" and done["shots"][-1]["start"] == 12.0 and done["shots"][-1]["end"] == 42.0
    assert done["audio"]["outro"] is True and done["music"][-1]["start"] == 7.0 and done["music"][-1]["role"] == "outro"
    assert done["outro"] == {"start": 12.0, "seconds": 30.0, "music_start": 7.0}


def test_a_library_without_the_track_stops_the_run_with_a_clear_message(db_session):
    db_session.query(MusicTrack).delete()
    db_session.commit()
    with pytest.raises(OUTRO.OutroError, match="no bridge track of the mood 'uncertainty'"):
        OUTRO.track_path(db_session)


def test_the_logo_fades_in_slowly_on_the_dark_ground_and_stays():
    maker = FrameMaker({"language": "en", "shots": [], "width": 640, "height": 360}, 640, 360)
    shot = {"kind": "outro", "start": 100.0, "end": 130.0}
    cfg = ai_config.outro_settings()
    early, mid, late = (maker.shot_frame(shot, 100.0 + s).astype(int) for s in (0.5, cfg.logo.delay_seconds + cfg.logo.fade_seconds / 2,
                                                                                 cfg.logo.delay_seconds + cfg.logo.fade_seconds + 5))
    centre = (slice(150, 210), slice(250, 390))
    assert abs(early[centre].mean() - early[:20, :20].mean()) < 30, "no logo at first: only the dark ground"
    assert early.mean() < 60
    assert mid[centre].mean() > early[centre].mean() + 3 and late[centre].mean() > mid[centre].mean(), "it comes in slowly"
    assert np.array_equal(late, maker.shot_frame(shot, 129.0).astype(int)), "and stays"


def test_a_mono_film_stays_mono_and_the_voice_is_not_changed_by_a_single_sample_before_the_music_begins(tmp_path):
    base, track, out = tmp_path / "film.wav", tmp_path / "bed.wav", tmp_path / "out.wav"
    tone(base, 40, 220, 0.5, channels=1)
    tone(track, 20, 440, 0.5)
    assert subprocess.run(OUTRO.mix_command(base, track, out, 40.0, 70.0), capture_output=True).returncode == 0
    assert OUTRO.audio_format(out) == (RATE, "mono")
    before, original = samples(out)[:int(34 * RATE)], samples(base)[:int(34 * RATE)]
    assert np.allclose(before, original, atol=1e-4), "the voice is exactly the voice"
