"""ffmpeg and the caption fonts come from the environment, config or PATH — never from a machine's fixed path —
and the fonts really carry the glyphs of their language."""

import re
import subprocess
from pathlib import Path

import pytest
from PIL import ImageFont

from app.core import media_tools
from app.core.ai_config import CaptionFont, ai_config
from app.shortform import export

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = {
    "en": "Quick brown fox jumps over the lazy dog 0123",
    "de": "Zwölf Boxkämpfer jagen Viktor quer über den großen Sylter Deich",
    "fa": "سلام دنیا، این یک آزمایش است؛ گچ و پژ و چای",
    "ar": "وصلت الإشارة الأخيرة، ثم انقطع الاتصال",
}
PRODUCTION = ai_config.short_form.production


def test_app_code_names_no_machine_specific_tool_or_font_path():
    offenders = [f"{p.relative_to(ROOT)}: {needle}" for p in (ROOT / "app").rglob("*.py")
                 for needle in ("/opt/homebrew", "/System/Library", "/Library/Fonts", "/usr/share/fonts")
                 if needle in p.read_text(encoding="utf-8")]
    assert offenders == []


# ---- ffmpeg / ffprobe -------------------------------------------------------


@pytest.fixture
def tool_setup(monkeypatch, tmp_path):
    monkeypatch.delenv("FFMPEG_BIN", raising=False)
    monkeypatch.delenv("FFPROBE_BIN", raising=False)
    on_path = tmp_path / "bin"
    on_path.mkdir()
    for tool in ("ffmpeg", "ffprobe"):
        (on_path / tool).write_text("#!/bin/sh\n")
        (on_path / tool).chmod(0o755)
    monkeypatch.setenv("PATH", str(on_path))
    return tmp_path, on_path


def test_the_environment_wins_over_config_and_path(monkeypatch, tool_setup):
    monkeypatch.setenv("FFMPEG_BIN", "/somewhere/ffmpeg")
    assert media_tools.ffmpeg_bin() == "/somewhere/ffmpeg"


def test_the_first_configured_build_that_exists_is_used(monkeypatch, tool_setup):
    tmp_path, _ = tool_setup
    build = tmp_path / "full" / "ffmpeg"
    build.parent.mkdir()
    build.write_text("#!/bin/sh\n")
    monkeypatch.setattr(PRODUCTION, "ffmpeg_candidates", [str(tmp_path / "missing" / "ffmpeg"), str(build)])
    assert media_tools.ffmpeg_bin() == str(build)


def test_without_a_configured_build_the_tool_on_path_is_used(monkeypatch, tool_setup):
    _, on_path = tool_setup
    monkeypatch.setattr(PRODUCTION, "ffmpeg_candidates", ["/does/not/exist/ffmpeg"])
    monkeypatch.setattr(PRODUCTION, "ffprobe_candidates", [])
    assert media_tools.ffmpeg_bin() == str(on_path / "ffmpeg")
    assert media_tools.ffprobe_bin() == str(on_path / "ffprobe")


def test_no_tool_anywhere_is_none(monkeypatch, tmp_path):
    monkeypatch.delenv("FFMPEG_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(PRODUCTION, "ffmpeg_candidates", [])
    assert media_tools.ffmpeg_bin() is None


# ---- caption fonts ----------------------------------------------------------


def test_the_first_listed_font_that_exists_is_used_and_relative_paths_start_at_the_repository(monkeypatch, tmp_path):
    first = tmp_path / "first.ttf"
    first.write_bytes(b"x")
    monkeypatch.setattr(PRODUCTION, "caption_fonts", {"en": [
        CaptionFont(file=str(tmp_path / "gone.ttf"), family="Gone"),
        CaptionFont(file=str(first), family="First"),
        CaptionFont(file="app/documentary/assets/fonts/Inter-400.ttf", family="Inter")]})
    assert export.caption_font("en") == {"file": str(first), "family": "First", "bundled": False}
    monkeypatch.setattr(PRODUCTION, "caption_fonts", {"en": [
        CaptionFont(file="app/documentary/assets/fonts/Inter-400.ttf", family="Inter")]})
    got = export.caption_font("en")
    assert got["file"] == str(ROOT / "app/documentary/assets/fonts/Inter-400.ttf") and got["bundled"]


def test_a_language_with_no_font_on_the_machine_fails_closed_naming_the_config_key(monkeypatch, tmp_path):
    monkeypatch.setattr(PRODUCTION, "caption_fonts", {"fa": [CaptionFont(file=str(tmp_path / "x.ttf"), family="X")]})
    with pytest.raises(export.FFmpegCapabilityError, match=r"caption_fonts\['fa'\]"):
        export.caption_font("fa")


def test_every_language_ends_its_list_with_a_font_that_ships_with_the_repository():
    for language in PRODUCTION.languages:
        last = PRODUCTION.caption_fonts[language][-1]
        path = ROOT / last.file
        assert path.is_file(), f"{language}: {last.file}"
        assert ImageFont.truetype(str(path), 30).getname()[0] == last.family


def has_glyph(font: ImageFont.FreeTypeFont, char: str) -> bool:
    """A character the font does not have is drawn as its .notdef box: the same picture for every missing one."""
    missing = font.getmask("￿")
    return font.getmask(char).getbbox() is not None and bytes(font.getmask(char)) != bytes(missing)


@pytest.mark.parametrize("language", sorted(SAMPLES))
def test_every_font_of_the_list_that_exists_carries_the_glyphs_of_its_language(language):
    existing = []
    for entry in PRODUCTION.caption_fonts[language]:
        path = Path(entry.file).expanduser()
        path = path if path.is_absolute() else ROOT / path
        if path.is_file():
            existing.append(path)
    assert existing, f"{language}: no font of the list exists"
    for path in existing:
        font = ImageFont.truetype(str(path), 40, index=0)
        lacking = sorted({c for c in SAMPLES[language] if c.strip() and c.isalpha() and not has_glyph(font, c)})
        assert lacking == [], f"{path.name} has no glyph for {lacking}"


def test_a_font_without_the_glyphs_is_recognised_as_such():
    latin = ImageFont.truetype(str(ROOT / "app/documentary/assets/fonts/Inter-400.ttf"), 40)
    assert not any(has_glyph(latin, c) for c in "سلام")


# ---- libass really picks those fonts ------------------------------------------


def libass_log(srt_text: str, language: str, tmp_path: Path) -> str:
    srt = tmp_path / "t.srt"
    srt.write_text(f"1\n00:00:00,000 --> 00:00:01,000\n{srt_text}\n", encoding="utf-8")
    font = export.caption_font(language)
    video_filter = export.subtitles_filter(srt, language, f"FontName={font['family']}")
    run = subprocess.run([media_tools.ffmpeg_bin(), "-hide_banner", "-v", "verbose", "-f", "lavfi", "-i",
                          "color=c=black:s=320x180:d=0.5", "-vf", video_filter, "-frames:v", "1", "-f", "null", "-"],
                         capture_output=True, text=True, check=True)
    return run.stderr


def test_this_machines_ffmpeg_can_burn_and_shape_captions():
    capability = export.ffmpeg_caption_capability()
    assert capability["available"], capability


@pytest.mark.parametrize("language", sorted(SAMPLES))
def test_libass_picks_the_repository_font_for_each_language_and_shapes_right_to_left(language, monkeypatch, tmp_path):
    bundled = PRODUCTION.caption_fonts[language][-1]
    monkeypatch.setattr(PRODUCTION, "caption_fonts", {**PRODUCTION.caption_fonts, language: [bundled]})
    log = libass_log(SAMPLES[language], language, tmp_path)
    chosen = re.search(r"fontselect: \(([^,]+), \d+, \d\) -> ([^,]+),", log)
    assert chosen, log[-400:]
    assert chosen.group(1) == bundled.family
    # the face libass found is the requested one (its PostScript name carries the family), not a fallback
    assert bundled.family.replace(" ", "").lower() in chosen.group(2).replace("-", "").lower()
    assert re.search(r"Shaper: FriBidi \S+ .*HarfBuzz-ng \S+", log)
