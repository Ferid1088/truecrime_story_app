from pathlib import Path

import pytest

from app.outfits import OutfitRegistry


def test_all_generated_outfits_have_required_png_poses():
    outfits = OutfitRegistry().all()
    assert len(outfits) == 17
    for outfit in outfits:
        assert {"front", "full_body"} <= set(outfit.poses)
        assert all(p.path.suffix == ".png" for p in outfit.poses.values())
        assert all(p.path.is_file() for p in outfit.poses.values())


def test_legacy_manifest_outfit_ids_resolve_to_current_slugs():
    registry = OutfitRegistry()
    assert registry.resolve("OUTFIT_01").outfit_id == "rust_overshirt_chinos"
    assert registry.resolve("OUTFIT_17").outfit_id == "lightblue_shirt_white_tee"


def test_registry_rejects_unknown_file(tmp_path: Path):
    source = next(Path("data/outfits").iterdir())
    target = tmp_path / source.name
    target.mkdir()
    for path in source.iterdir():
        if path.is_file():
            (target / path.name).write_bytes(path.read_bytes())
    (target / "unexpected.txt").write_text("no", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown outfit files"):
        OutfitRegistry(tmp_path).all()
