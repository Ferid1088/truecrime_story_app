from pathlib import Path

import pytest

from app.outfits import OutfitRegistry


def test_outfits_are_read_per_language_with_stances():
    outfits = OutfitRegistry().all()
    assert {o.language for o in outfits} == {"en", "de", "fa", "ar"}
    for outfit in outfits:
        assert outfit.outfit_id == f"{outfit.language}/{outfit.name}"
        assert {p.stance for p in outfit.poses.values()} == {"SEATED", "STANDING"}
        assert all(p.path.suffix == ".png" and p.path.is_file() for p in outfit.poses.values())


def test_label_splits_the_camel_case_name():
    assert OutfitRegistry().resolve("en/navySuit").label == "navy Suit"


def test_reference_pose_fits_the_stance():
    outfit = OutfitRegistry().resolve("de/brownSweater")
    assert outfit.reference_pose("SEATED").role.startswith("sit")
    assert outfit.reference_pose("STANDING").role.startswith("stand")


def test_unknown_outfit_is_a_key_error():
    with pytest.raises(KeyError):
        OutfitRegistry().resolve("navy_suit")


def test_variants_change_the_outfit_before_the_pose():
    variants = OutfitRegistry().variants("en")
    assert len(variants) == 15 and len(set(variants)) == 15
    assert all(a[0] != b[0] for a, b in zip(variants, variants[1:], strict=False))


def test_registry_skips_a_file_not_named_after_its_outfit(tmp_path: Path, outfit_root: Path):
    """Unexpected files are skipped (the library scan reports them) — the outfit
    and every other outfit still resolve."""
    source = outfit_root / "en" / "navySuit"
    target = tmp_path / "en" / "navySuit"
    target.mkdir(parents=True)
    for path in source.iterdir():
        (target / path.name).write_bytes(path.read_bytes())
    (target / "unexpected.txt").write_text("no", encoding="utf-8")
    outfit = OutfitRegistry(tmp_path).resolve("en/navySuit")
    assert len(outfit.poses) == len(list(source.iterdir()))


def test_registry_skips_a_non_png(tmp_path: Path):
    """An invalid PNG is skipped; an outfit left with no poses is dropped from
    all() without taking the rest down."""
    folder = tmp_path / "en" / "navySuit"
    folder.mkdir(parents=True)
    (folder / "en_navySuit_stand.png").write_bytes(b"not a png")
    (folder / "en_navySuit_sitHands.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    outfit = OutfitRegistry(tmp_path).resolve("en/navySuit")
    assert list(outfit.poses) == ["sitHands"]

    empty = tmp_path / "de" / "onlyBad"
    empty.mkdir(parents=True)
    (empty / "de_onlyBad_stand.png").write_bytes(b"also not a png")
    assert all(o.outfit_id != "de/onlyBad" for o in OutfitRegistry(tmp_path).all())


def test_the_real_outfit_folder_is_readable():
    from app.outfits import registry

    real = OutfitRegistry(Path(registry.__file__).resolve().parents[2] / "data" / "outfits")
    if not real.root.is_dir():
        pytest.skip("data/outfits is private and not in this checkout")
    assert {o.language for o in real.all()} <= {"en", "de", "fa", "ar"}
