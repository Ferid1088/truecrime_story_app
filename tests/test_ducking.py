from app.shortform.ducking import (
    SpeechInterval,
    apply_envelope,
    build_gain_envelope,
    duck_headroom_db,
    generate_synthetic_testbed,
    measure_ebur128,
    plot_envelope,
)


def test_envelope_ducks_during_speech_and_rises_in_gaps():
    speech = [SpeechInterval(2.0, 4.0)]
    env = build_gain_envelope(speech, total_seconds=6.0, duck_db=-14.0,
                              visual_only_db=-8.0, attack_seconds=0.5, release_seconds=0.5)
    mid_speech = next(g for t, g in env if abs(t - 3.0) < 0.05)
    visual_only = next(g for t, g in env if abs(t - 0.2) < 0.05)
    assert mid_speech == -14.0
    assert visual_only == -8.0


def test_music_stays_at_least_config_db_below_speech_while_speech_plays():
    speech = [SpeechInterval(1.0, 5.0), SpeechInterval(9.0, 13.0)]
    env = build_gain_envelope(speech, total_seconds=14.0, duck_db=-14.0, visual_only_db=-8.0,
                              attack_seconds=0.35, release_seconds=0.6)
    headroom = duck_headroom_db(env, speech, visual_only_db=-8.0)
    assert headroom >= 5.9, f"only {headroom} dB of headroom during speech (want >= 6 dB)"


def test_full_pipeline_real_ffmpeg_synthetic_testbed(tmp_path):
    speech_path, music_path, speech = generate_synthetic_testbed(tmp_path)
    assert speech_path.exists() and speech_path.stat().st_size > 0
    assert music_path.exists() and music_path.stat().st_size > 0

    env = build_gain_envelope(speech, total_seconds=14.0)
    out = tmp_path / "ducked_music.wav"
    apply_envelope(music_path, env, out)
    assert out.exists() and out.stat().st_size > 0

    loud_before = measure_ebur128(music_path)
    loud_after = measure_ebur128(out)
    assert loud_after["integrated_lufs"] is not None
    assert loud_after["integrated_lufs"] < loud_before["integrated_lufs"], (
        "ducked music must measure quieter overall than the unducked bed")

    plot_path = tmp_path / "envelope.png"
    plot_envelope(env, speech, plot_path)
    assert plot_path.exists() and plot_path.stat().st_size > 0
