"""The video of a short clip: the timeline (hook, pictures, reaction and invitation, the music after the hook), the pictures' count, the caption chunks from the first word,
the vertical crop around the host's face and, with real ffmpeg on small inputs, the finished 9:16 video with its audio and its length."""

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin, ffprobe_bin
from app.documentary import audio as A
from app.documentary import opening_media as OM
from app.shortform import clip_video as CV

SECONDS = {"hook": 1.8, "context": 3.0, "detail": 12.0, "reaction": 5.0, "cta": 2.5}


def voices():
    out = {}
    for part, seconds in SECONDS.items():
        n = max(2, round(seconds * 2.5))
        out[part] = {"seconds": seconds, "words": [[f"{part}{i}", round(i * seconds / n, 3), round((i + 0.8) * seconds / n, 3)] for i in range(n)]}
    return out


def test_the_timeline_puts_the_hook_first_the_pictures_in_the_middle_and_the_avatar_again_at_the_end():
    cfg = ai_config.short_clips_settings().video
    plan = CV.plan_timeline(SECONDS, cfg)
    assert plan.starts["hook"] == 0 and plan.starts["context"] == round(1.8 + cfg.gap_seconds, 3)
    assert [s["kind"] for s in plan.segments] == ["avatar", "pictures", "avatar"] and plan.segments[0]["end"] == plan.starts["context"] == plan.segments[1]["start"]
    assert plan.segments[1]["end"] == plan.starts["reaction"] == plan.segments[2]["start"] and plan.segments[2]["end"] == plan.total
    assert plan.total == round(plan.starts["cta"] + 2.5 + cfg.tail_seconds, 3) and plan.music_start == 1.8              # the music begins when the hook ends


def test_the_pictures_fill_the_middle_each_for_a_few_seconds():
    cfg = ai_config.short_clips_settings().video
    low, high = cfg.picture_seconds
    for middle in (8.0, 15.0, 16.5, 2.0):
        n = CV.picture_count(middle, cfg)
        assert 1 <= n <= cfg.pictures_max and (n == 1 or low <= middle / n <= high + 0.8)


def test_the_captions_show_one_word_at_a_time_while_the_voice_says_it():
    cfg = ai_config.short_clips_settings().video
    plan = CV.plan_timeline(SECONDS, cfg)
    spoken, made = voices(), CV.chunks(voices(), plan, cfg)
    assert made[0]["start"] == 0.0 and made[0]["text"] == "hook0" and all(len(c["text"].split()) == 1 for c in made)
    assert len(made) == sum(len(v["words"]) for v in spoken.values()) and all(c["end"] > c["start"] for c in made) and made[-1]["end"] <= plan.total
    assert all(a["start"] <= b["start"] for a, b in zip(made, made[1:], strict=False))
    word = spoken["hook"]["words"][0]
    assert made[0]["end"] == round(min(word[2] + cfg.caption.hold_seconds, spoken["hook"]["words"][1][1]), 3)       # it goes away when it is said, the next one comes


def test_a_word_never_stays_over_the_next_one_and_goes_away_in_a_pause():
    cfg = ai_config.short_clips_settings().video
    plan = CV.plan_timeline(SECONDS, cfg)
    talk = voices()
    talk["hook"]["words"] = [["Eins", 0.0, 0.3], ["zwei", 0.35, 0.6], ["drei", 1.2, 1.5]]
    made = CV.chunks(talk, plan, cfg)[:3]
    assert made[0]["end"] == 0.35 and made[1]["end"] == round(0.6 + cfg.caption.hold_seconds, 3) and made[2]["start"] == 1.2
    assert made[1]["end"] < made[2]["start"]                                                                           # between «zwei» and «drei» nothing is shown


@pytest.mark.parametrize("language,spoken,shown", [
    ("de", ["Jemand", "ist", "eingebrochen,", "und", "hat", "meine", "Frau", "erschossen."], ["Jemand", "ist", "eingebrochen", "und", "hat", "meine", "Frau", "erschossen"]),
    ("en", ["\"Someone", "broke", "in,\"", "he", "said.", "Why?", "Well-known!"], ["Someone", "broke", "in", "he", "said", "Why", "Well-known"]),
    ("fa", ["ساعت", "دو،", "کسی", "وارد", "شده؟", "«می‌گوید»", "…"], ["ساعت", "دو", "کسی", "وارد", "شده", "می‌گوید"]),
    ("ar", ["اتصل", "بالطوارئ،", "فجرًا؛", "ماذا", "حدث؟"], ["اتصل", "بالطوارئ", "فجرًا", "ماذا", "حدث"]),
    ("es", ["¿Qué", "ocurrió", "realmente?", "¡Atención!"], ["Qué", "ocurrió", "realmente", "Atención"]),
    ("fr", ["Il", "appelle,", "puis", "l'", "attend…"], ["Il", "appelle", "puis", "l", "attend"]),
    ("it", ["Chiama", "la", "polizia,", "poi", "scompare."], ["Chiama", "la", "polizia", "poi", "scompare"]),
    ("pt", ["Ela", "ligou", "às", "2h,", "desapareceu."], ["Ela", "ligou", "às", "2h", "desapareceu"]),
    ("tr", ["Gece", "2'de", "polisi", "aradı,", "kayboldu."], ["Gece", "2'de", "polisi", "aradı", "kayboldu"])])
def test_the_captions_have_no_punctuation_in_any_language_and_keep_what_is_inside_a_word(language, spoken, shown):
    cfg = ai_config.short_clips_settings().video
    plan = CV.plan_timeline(SECONDS, cfg)
    talk = voices()
    talk["hook"]["words"] = [[w, round(i * 0.4, 3), round(i * 0.4 + 0.3, 3)] for i, w in enumerate(spoken)]
    assert [c["text"] for c in CV.chunks(talk, plan, cfg)[:len(shown)]] == shown
    assert not any(ch in c["text"] for c in CV.chunks(talk, plan, cfg) for ch in ",.;:!?…،؟؛«»\"¿¡")


def test_the_vertical_window_is_cut_around_the_face_inside_the_frame():
    cfg = ai_config.short_clips_settings().video
    width, left = CV.avatar_crop((1920, 1080), 760, cfg)
    assert width == 608 and abs(left + width / 2 - 760) <= 2
    assert CV.avatar_crop((1920, 1080), 20, cfg)[1] == 0 and CV.avatar_crop((1920, 1080), 1900, cfg)[1] == 1920 - 608


def _tools(tmp: Path):
    ff = ffmpeg_bin()
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-f", "lavfi", "-i", "sine=frequency=300", "-t", "12", "-pix_fmt", "yuv420p", str(tmp / "avatar.mp4")], check=True)
    for name, color in (("p1.jpg", "red"), ("p2.jpg", "green"), ("p3.jpg", "blue")):
        Image.new("RGB", (400, 300), color).save(tmp / name)
    for part, seconds in SECONDS.items():
        subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", str(tmp / f"{part}.mp3")], check=True)
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=110:duration=22", str(tmp / "music.wav")], check=True)


@pytest.mark.skipif(ffmpeg_bin() is None, reason="ffmpeg is not available")
def test_the_video_is_made_vertical_with_its_pictures_captions_voices_and_music(tmp_path):
    cfg = ai_config.short_clips_settings().video.model_copy(update={"width": 216, "height": 384, "fps": 15, "preset": "ultrafast"})
    _tools(tmp_path)
    plan = CV.plan_timeline(SECONDS, cfg)
    captions = []
    for k, chunk in enumerate(CV.chunks(voices(), plan, cfg)):
        image = tmp_path / f"c{k}.png"
        CV.caption_image(chunk["text"], "en", image, cfg)
        captions.append((image, chunk))
    out = tmp_path / "clip.mp4"
    command = CV.assemble_command(plan, voices={p: tmp_path / f"{p}.mp3" for p in CV.ORDER}, avatars={"hook": tmp_path / "avatar.mp4", "reaction": tmp_path / "avatar.mp4"},
                                  crops={"hook": CV.avatar_crop((640, 360), 320, cfg), "reaction": CV.avatar_crop((640, 360), 320, cfg)},
                                  pictures=[tmp_path / "p1.jpg", tmp_path / "p2.jpg", tmp_path / "p3.jpg"], captions=captions, music=tmp_path / "music.wav", out=out, cfg=cfg)
    CV.assemble(command)
    assert out.exists() and abs(A.probe_duration(out) - plan.total) < 0.5
    probe = subprocess.run([ffmpeg_bin(), "-i", str(out)], capture_output=True, text=True).stderr
    assert "216x384" in probe and "Audio: aac" in probe and "Video: h264" in probe
    first = tmp_path / "first.png"
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(out), "-frames:v", "1", str(first)], check=True)
    assert Image.open(first).size == (216, 384)


def test_a_failing_ffmpeg_command_is_reported(tmp_path):
    with pytest.raises(CV.ClipVideoError, match="could not make"):
        CV.assemble([ffmpeg_bin(), "-y", "-v", "error", "-i", str(tmp_path / "missing.mp4"), str(tmp_path / "x.mp4")])


def test_the_words_of_a_take_are_found_from_its_character_times():
    al = {"characters": list("Hi there, you"), "starts": [i * 0.1 for i in range(13)], "ends": [i * 0.1 + 0.1 for i in range(13)]}
    assert OM.word_times(al) == [["Hi", 0.0, 0.2], ["there,", 0.3, 0.9], ["you", 1.0, 1.3]]


def test_a_turkish_caption_keeps_the_dotted_capital(tmp_path):
    from app.covers.compose import display_title

    assert display_title("izler ışık", "tr") == "İZLER IŞIK"
    size = CV.caption_image("izler ışık", "tr", tmp_path / "c.png", ai_config.short_clips_settings().video)
    assert size[0] == ai_config.short_clips_settings().video.width and (tmp_path / "c.png").exists()


def test_a_caption_never_leaves_one_word_alone_on_its_last_line(tmp_path, monkeypatch):
    seen = []
    real = CV.TY.wrap
    monkeypatch.setattr(CV.TY, "wrap", lambda *a: seen.append(real(*a)) or seen[-1])
    CV.caption_image("the detective found the", "en", tmp_path / "c.png")
    assert len(seen[-1]) == 1 or len(seen[-1][-1].split()) > 1


def test_the_encode_follows_the_platforms_file_rules_a_closed_gop_a_fixed_rate_and_aac_stereo_at_48_khz():
    cfg = ai_config.short_clips_settings().video
    args = CV.encode_args(cfg)
    gop = round(cfg.delivery.gop_seconds * cfg.fps)
    assert 2.0 <= cfg.delivery.gop_seconds <= 5.0                                          # Meta's Reels guide: a closed GOP of 2 to 5 seconds
    assert args[args.index("-g") + 1] == str(gop) == args[args.index("-keyint_min") + 1] and args[args.index("-sc_threshold") + 1] == "0"
    assert "open-gop=0" in args and args[args.index("-fps_mode") + 1] == "cfr" and args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert args[args.index("-ar") + 1] == "48000" and args[args.index("-ac") + 1] == "2" and int(args[args.index("-b:a") + 1].rstrip("k")) >= 128
    assert f"I={cfg.delivery.lufs}" in CV.loudness_filter(cfg) and f"TP={cfg.delivery.true_peak}" in CV.loudness_filter(cfg)


@pytest.mark.skipif(ffmpeg_bin() is None, reason="ffmpeg is not available")
def test_a_delivery_copy_has_the_gop_and_the_sound_level_the_platforms_ask_for_and_the_original_is_not_touched(tmp_path):
    import hashlib

    cfg = ai_config.short_clips_settings().video.model_copy(update={"preset": "ultrafast"})
    source = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=216x384:rate=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-af", "volume=6.0,alimiter=limit=1.0",
                    "-t", "9", "-c:v", "libx264", "-g", "250", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], check=True)       # 9 seconds, one key frame, a full-scale sound
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    out = tmp_path / "delivery.mp4"
    CV.assemble(CV.delivery_command(source, out, cfg))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    keys = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey", "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(out)],
                          capture_output=True, text=True).stdout.split()
    times = [float(t.rstrip(",")) for t in keys]
    assert max(b - a for a, b in zip(times, times[1:], strict=False)) <= cfg.delivery.gop_seconds + 0.05
    meter = subprocess.run([ffmpeg_bin(), "-nostats", "-i", str(out), "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True).stderr
    peak = float(meter.rsplit("Peak:", 1)[1].split("dBFS")[0])
    assert peak <= cfg.delivery.peak_ceiling + 0.05


@pytest.mark.skipif(ffmpeg_bin() is None, reason="ffmpeg is not available")
def test_a_delivery_copy_made_with_a_cover_opens_with_it_for_the_configured_seconds_and_still_meets_the_platform_rules(tmp_path):
    from PIL import Image

    from app.publishing import platform_specs as PS

    cfg = ai_config.short_clips_settings().video.model_copy(update={"preset": "ultrafast"})
    lead = cfg.delivery.cover_lead_seconds
    source = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:size=540x960:rate=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(source)], check=True)
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (1080, 1920), (250, 10, 10)).save(cover, quality=95)        # a JPEG is full-range: the copy must still be yuv420p, which Meta asks for
    out = tmp_path / "delivery.mp4"
    CV.assemble(CV.delivery_command(source, out, cfg, cover=cover))
    found = PS.probe(out)
    assert abs(found["seconds"] - (4 + lead)) < 0.2 and PS.check_video(out, "facebook_reels", found) == []

    def pixel(at: float) -> tuple[int, int, int]:
        frame = tmp_path / f"f{at}.png"
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-ss", str(at), "-i", str(out), "-frames:v", "1", str(frame)], check=True)
        return Image.open(frame).convert("RGB").getpixel((540, 960))

    first, later = pixel(0.0), pixel(lead + 1.0)
    assert first[0] > 200 and first[2] < 60 and later[2] > 150 and later[0] < 80                      # the cover first, then the clip
