"""Channel intros: made once per channel from its logo (same file in every
film; made again only when the logo, concept, size or code changes),
placed after the cold open (in its chapter break) before the film title
and chapter 1 — or before the first word when the film has no cold open —
and played by the renderer with its own sound (the film's sound ducked
under it)."""
import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.core.ai_config import ai_config
from app.documentary import intros as IN
from app.documentary.production.script import insert_chapter_cards

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")


def _logo(path: Path, color=(200, 40, 40)) -> Path:
    img = Image.new("RGB", (400, 400), (0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((100, 60, 300, 260), fill=color)
    d.rectangle((80, 300, 320, 340), fill=(220, 190, 120))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


@pytest.fixture
def channel(tmp_path, monkeypatch):
    logo = _logo(tmp_path / "logos" / "Logo_Test.png")
    state = {"concept": "flashlight", "logo": logo}
    monkeypatch.setattr(IN, "channel_intro", lambda lang: (state["concept"], state["logo"]))
    monkeypatch.setattr(ai_config.chapters, "intro_dir", str(tmp_path / "intros"))
    return state


def test_concepts_and_defaults():
    # the producer's choice: ClueVera B, Fallspur A, Persian moonrise, Arabic B
    assert IN.DEFAULT_CONCEPT == {"en": "flashlight", "de": "trail_stamp", "fa": "moonrise",
                                  "ar": "sand"}
    assert all(5.0 <= c.seconds <= 7.0 for c in IN.CONCEPTS.values())
    assert {ai_config.channels[lg].intro_concept for lg in ("en", "de", "fa", "ar")} == \
        {"flashlight", "trail_stamp", "moonrise", "sand"}


@needs_ffmpeg
def test_an_intro_is_made_once_and_reused(channel, tmp_path):
    a = IN.ensure_intro("xx", 160, 90, 10)
    assert a and Path(a["video"]).exists() and Path(a["audio"]).exists()
    assert a["seconds"] == IN.CONCEPTS["flashlight"].seconds
    meta = json.loads((tmp_path / "intros" / "xx" / "intro_160x90.json").read_text())
    assert meta["concept"] == "flashlight" and meta["fingerprint"] == a["fingerprint"]
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", a["video"]], capture_output=True, text=True)
    assert abs(float(out.stdout) - 6.0) < 0.3
    with wave.open(a["audio"]) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16)
        assert w.getnchannels() == 2 and np.abs(x).max() > 1000   # it has a sound
    mtime = Path(a["video"]).stat().st_mtime
    b = IN.ensure_intro("xx", 160, 90, 10)
    assert b["fingerprint"] == a["fingerprint"] and Path(b["video"]).stat().st_mtime == mtime
    # a new logo (or concept) makes a new intro
    _logo(channel["logo"], (40, 40, 200))
    c = IN.ensure_intro("xx", 160, 90, 10)
    assert c["fingerprint"] != a["fingerprint"]


@needs_ffmpeg
def test_every_chosen_concept_renders(channel):
    for concept in ("trail_stamp", "moonrise", "sand"):
        channel["concept"] = concept
        got = IN.ensure_intro(concept, 128, 72, 6)
        assert got["concept"] == concept and Path(got["video"]).exists()


CARDS = {"cold_open": True, "film_title": "The lake", "beat_order": ["B01", "B02", "B03"],
         "intro_seconds": 6.0,
         "chapters": [{"act_id": "act1", "number": 1, "first_beat": "B01", "last_beat": "B02",
                       "label": "Chapter 1", "title": "The village"},
                      {"act_id": "act2", "number": 2, "first_beat": "B03", "last_beat": "B03",
                       "label": "Chapter 2", "title": None}]}


def _shot(a, b, beat):
    return {"beat_id": beat, "start": a, "end": b, "kind": "image", "command": "NEW_IMAGE",
            "asset_id": f"V{a}", "path": "x.jpg", "motion": "SLOW_PUSH",
            "transition_in": "CROSSFADE"}


def test_the_intro_follows_the_cold_open():
    spans = [{"beat_id": "B01", "start": 0.0, "end": 6.0},
             {"beat_id": "B02", "start": 28.0, "end": 50.0},
             {"beat_id": "B03", "start": 66.0, "end": 90.0}]
    shots = [_shot(0, 28, "B01"), _shot(28, 66, "B02"), _shot(66, 90, "B03")]
    left, intro = insert_chapter_cards(shots, CARDS, spans, [])
    kinds = [(s["kind"], s["start"], s["end"]) for s in shots if s["kind"] != "image"]
    assert [k for k, _, _ in kinds][:3] == ["intro", "title", "chapter"]
    assert intro == {"mode": "gap", "start": kinds[0][1], "seconds": 6.0}
    assert kinds[0][2] - kinds[0][1] == pytest.approx(6.0)
    assert kinds[2][2] == round(28.0 - ai_config.chapters.lead_out_seconds, 3)
    assert next(s for s in shots if s["kind"] == "intro")["transition_in"] == "NONE"
    # a shorter break: the film title goes first, the intro stays
    spans[1]["start"] = 6.0 + 0.5 + 6.0 + ai_config.chapters.min_card_seconds + 0.6
    shots = [_shot(0, 20, "B01"), _shot(20, 66, "B02"), _shot(66, 90, "B03")]
    left, intro = insert_chapter_cards(shots, CARDS, spans, [])
    assert [s["kind"] for s in shots if s["kind"] != "image"][:2] == ["intro", "chapter"]
    assert intro["mode"] == "gap"
    # no cold open: the intro comes before the first word
    shots = [_shot(0, 28, "B01"), _shot(28, 66, "B02"), _shot(66, 90, "B03")]
    left, intro = insert_chapter_cards(shots, {**CARDS, "cold_open": False}, spans, [])
    assert intro == {"mode": "prepend", "seconds": 6.0}
    assert not any(s["kind"] == "intro" for s in shots)
    # without a channel intro nothing changes
    left, intro = insert_chapter_cards([_shot(0, 90, "B01")], {**CARDS, "intro_seconds": None},
                                       spans, [])
    assert intro is None


def _audio(path: Path, seconds: float) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"sine=frequency=300:duration={seconds}", "-ac", "2", "-ar", "48000",
                    str(path)], check=True)
    return path


@needs_ffmpeg
def test_the_renderer_plays_the_intro_with_its_sound(channel, tmp_path, monkeypatch):
    from app.documentary.render.engine import VideoRenderer

    monkeypatch.setattr(ai_config.render, "fps", 10)
    wav = _audio(tmp_path / "doc.wav", 10.0)
    base = {"language": "en", "duration": 10.0, "overlays": [],
            "subtitles": [{"start": 1.0, "end": 2.0, "text": "Hello"}],
            "audio": {"path": str(wav)}}
    # before the first word: the film starts after the intro
    script = {**base, "intro": {"mode": "prepend", "seconds": 6.0},
              "shots": [{"index": 0, "kind": "black", "start": 0.0, "end": 10.0,
                         "transition_in": "NONE", "motion": "NONE"}]}
    info = VideoRenderer(160, 90).render(script, tmp_path / "pre.mp4")
    assert info["duration"] == 16.0 and info["intro"]["mode"] == "prepend"
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration",
         "-of", "json", str(tmp_path / "pre.mp4")], capture_output=True, text=True).stdout)
    assert abs(float(probe["format"]["duration"]) - 16.0) < 0.3
    assert {s["codec_type"] for s in probe["streams"]} >= {"video", "audio", "subtitle"}
    srt = (tmp_path / "pre.srt").read_text()
    assert "00:00:07,000 --> 00:00:08,000" in srt                  # subtitles moved too
    # in the cold open's break: same length, the intro's frames in its window
    script = {**base, "intro": {"mode": "gap", "start": 2.0, "seconds": 6.0},
              "shots": [{"index": 0, "kind": "black", "start": 0.0, "end": 2.0,
                         "transition_in": "NONE", "motion": "NONE"},
                        {"index": 1, "kind": "intro", "start": 2.0, "end": 8.0,
                         "transition_in": "NONE", "motion": "NONE", "intro": {"seconds": 6.0}},
                        {"index": 2, "kind": "black", "start": 8.0, "end": 10.0,
                         "transition_in": "NONE", "motion": "NONE"}]}
    info = VideoRenderer(160, 90).render(script, tmp_path / "gap.mp4")
    assert info["duration"] == 10.0 and info["intro"]["mode"] == "gap"
    frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "6.5", "-i",
                            str(tmp_path / "gap.mp4"), "-frames:v", "1", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    assert np.frombuffer(frame, np.uint8).max() > 60               # the lit logo, not black
    # no logo for the channel: the film still renders, without an intro
    monkeypatch.setattr(IN, "channel_intro", lambda lang: None)
    info = VideoRenderer(160, 90).render({**script, "language": "de"}, tmp_path / "none.mp4")
    assert info["intro"] is None
