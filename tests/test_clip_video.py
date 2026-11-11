"""The video of a short clip: the timeline (hook, pictures, reaction and invitation, the music after the hook), the pictures' count, the caption chunks from the first word,
the vertical crop around the host's face and, with real ffmpeg on small inputs, the finished 9:16 video with its audio and its length."""

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin
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
        assert 1 <= n <= cfg.scenes.count and (n == 1 or low <= middle / n <= high + 0.8)


def test_the_captions_start_with_the_first_word_in_groups_and_follow_the_voice():
    cfg = ai_config.short_clips_settings().video
    plan = CV.plan_timeline(SECONDS, cfg)
    made = CV.chunks(voices(), plan, cfg)
    assert made[0]["start"] == 0.0 and made[0]["text"].startswith("hook0") and len(made[0]["text"].split()) == cfg.caption.words_per_chunk
    assert all(a["start"] <= b["start"] for a, b in zip(made, made[1:], strict=False)) and made[-1]["end"] <= plan.total
    assert all(c["end"] > c["start"] for c in made)


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
