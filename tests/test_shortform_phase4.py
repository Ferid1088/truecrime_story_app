import json
import subprocess

from app.shortform.export import HEIGHT, WIDTH, build_export_package, subtitle_layout, SubtitleCue


def test_export_package_is_watchable_and_disclosed(tmp_path):
    manifest = build_export_package(tmp_path)
    assert (tmp_path / manifest["video"]).stat().st_size > 10_000
    assert (tmp_path / manifest["cover"]).exists()
    assert manifest["width"] == WIDTH == 1080
    assert manifest["height"] == HEIGHT == 1920
    assert manifest["disclosure"]["required"] is True
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=width,height", "-of", "json",
                            str(tmp_path / manifest["video"])], capture_output=True,
                           text=True, check=True)
    assert '"width": 1080' in probe.stdout
    assert '"height": 1920' in probe.stdout


def test_rtl_subtitle_layout_records_direction_font_and_safe_area():
    layout = subtitle_layout("ar", [SubtitleCue(0, 1, "وصلت الإشارة الأخيرة")])
    assert layout["direction"] == "rtl"
    assert layout["font"].endswith("GeezaPro.ttc")
    assert layout["safe_area"]["bottom"] == 1590


def test_long_subtitle_is_rejected_before_render():
    try:
        subtitle_layout("fa", [SubtitleCue(0, 1, "x" * 43)])
    except ValueError as exc:
        assert "42" in str(exc)
    else:
        raise AssertionError("long subtitle was accepted")


def test_export_manifest_preserves_review_required_rights(tmp_path):
    manifest = build_export_package(
        tmp_path,
        title="Case detail",
        narration=["Case detail", "Review rights first", "No publishing"],
        duration_seconds=20,
        rights_status="editorial_review_required",
        rights_source="visual_asset:VIS_000002",
        rights_human_signoff=False,
    )
    assert manifest["rights"] == {
        "status": "editorial_review_required",
        "source": "visual_asset:VIS_000002",
        "human_signoff": False,
    }
    assert manifest["duration_seconds"] == 20
