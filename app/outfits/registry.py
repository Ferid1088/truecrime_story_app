"""Typed access to ``data/outfits/<slug>/<pose>.png``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "data" / "outfits"
_ROLES = {
    "front": "front",
    "full_body": "full_body",
    "standing_pockets": "standing_pockets",
    "standing_3q": "standing_3q",
    "standing_legs_crossed": "standing_legs_crossed",
    "sitting_crossed": "sitting_crossed",
    "sitting_hands": "sitting_hands",
}
_REQUIRED = {"front", "full_body"}

# Stable ids from the imported host manifest remain aliases, not a second
# image source. The registry owns the canonical slug and pose files.
_LEGACY = {
    "rust_overshirt_chinos": ("OUTFIT_01", "Rust Overshirt"),
    "navy_suit": ("OUTFIT_02", "Navy Suit"),
    "navy_cord_blazer_vest": ("OUTFIT_03", "Corduroy Three-Piece"),
    "grey_blazer_jeans": ("OUTFIT_04", "Grey Blazer & Jeans"),
    "navy_blazer_beige_chinos": ("OUTFIT_05", "Navy Blazer & Chinos"),
    "navy_quarterzip_tie": ("OUTFIT_06", "Navy Quarter-Zip"),
    "white_tee_beige_sweater": ("OUTFIT_07", "Beige Knit Shoulders"),
    "thobe_ghutra_sunglasses": ("OUTFIT_08", "Thobe & Ghutra (Sunglasses)"),
    "thobe_ghutra": ("OUTFIT_09", "Thobe & Ghutra"),
    "thobe_mandarin_sneakers": ("OUTFIT_10", "White Thobe"),
    "lightblue_polo_cream_sweater": ("OUTFIT_11", "Polo & Cream Knit"),
    "olive_sweater_beige_chinos": ("OUTFIT_12", "Olive Sweater & Chinos"),
    "navy_polo_beige_pleated": ("OUTFIT_13", "Navy Polo & Beige Trousers"),
    "navy_fieldjacket_turtleneck": ("OUTFIT_14", "Turtleneck & Navy Jacket"),
    "black_shirt_dark_trousers": ("OUTFIT_15", "Casual Navy"),
    "brown_sweater_navy_chinos": ("OUTFIT_16", "Brown Crewneck Sweater"),
    "lightblue_shirt_white_tee": ("OUTFIT_17", "Oxford Shirt & Chinos"),
}


@dataclass(frozen=True)
class OutfitPose:
    role: str
    path: Path


@dataclass(frozen=True)
class Outfit:
    outfit_id: str
    label: str
    poses: dict[str, OutfitPose]
    legacy_ids: tuple[str, ...] = ()
    heygen_look_ids: tuple[str, ...] = ()


class OutfitRegistry:
    """Read and validate the generated outfit directory layout."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(_ROOT if root is None else root)

    @staticmethod
    def _label(outfit_id: str) -> str:
        return outfit_id.replace("_", " ").title()

    @staticmethod
    def _png(path: Path) -> bool:
        return path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    def _read_outfit(self, directory: Path) -> Outfit:
        files = {path.name: path for path in directory.iterdir() if path.is_file()}
        unknown = sorted(set(files) - {f"{name}.png" for name in _ROLES})
        if unknown:
            raise ValueError(f"{directory.name}: unknown outfit files: {unknown}")
        missing = sorted(f"{name}.png" for name in _REQUIRED if f"{name}.png" not in files)
        if missing:
            raise ValueError(f"{directory.name}: missing required files: {missing}")
        non_png = sorted(name for name, path in files.items() if path.suffix.lower() != ".png")
        if non_png:
            raise ValueError(f"{directory.name}: non-PNG files: {non_png}")
        invalid = sorted(name for name, path in files.items() if not self._png(path))
        if invalid:
            raise ValueError(f"{directory.name}: invalid PNG files: {invalid}")
        poses = {
            name.removesuffix(".png"): OutfitPose(role=_ROLES[name.removesuffix(".png")], path=path)
            for name, path in sorted(files.items())
        }
        legacy_id, label = _LEGACY.get(directory.name, (None, self._label(directory.name)))
        return Outfit(outfit_id=directory.name, label=label, poses=poses,
                      legacy_ids=(legacy_id,) if legacy_id else ())

    def all(self) -> tuple[Outfit, ...]:
        if not self.root.is_dir():
            raise ValueError(f"outfit root does not exist: {self.root}")
        directories = sorted(path for path in self.root.iterdir() if path.is_dir())
        if not directories:
            raise ValueError(f"no outfit directories found: {self.root}")
        return tuple(self._read_outfit(directory) for directory in directories)

    def get(self, outfit_id: str) -> Outfit:
        for outfit in self.all():
            if outfit.outfit_id == outfit_id or outfit_id in outfit.legacy_ids:
                return outfit
        raise KeyError(outfit_id)

    def resolve(self, outfit_id: str) -> Outfit:
        """Resolve a current slug or a persisted pre-registry id."""
        return self.get(outfit_id)
