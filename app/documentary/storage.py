"""Case-local storage layout (Part 52). The database stays the source of
truth for metadata; these folders only hold the files.

data/cases/<case_id>/
    visuals/{photos,video,documents,maps,cards,broll,thumbs}/
    production/<lang>/
    renders/<lang>/
    audio/<lang>/v<version>/      (voice renderer, unchanged)
"""

from __future__ import annotations

from pathlib import Path

from app.core.ai_config import ai_config

ROOT = Path(__file__).resolve().parents[2]

VISUAL_DIRS = {
    "photo": "photos", "video": "video", "document": "documents", "map": "maps",
    "card": "cards", "graphic": "cards", "broll": "broll",
}


def cases_root() -> Path:
    d = Path(ai_config.documentary.storage_dir)
    return d if d.is_absolute() else ROOT / d


def case_dir(case_id: int) -> Path:
    return cases_root() / str(case_id)


def visuals_dir(case_id: int, asset_type: str = "photo") -> Path:
    d = case_dir(case_id) / "visuals" / VISUAL_DIRS.get(asset_type, "photos")
    d.mkdir(parents=True, exist_ok=True)
    return d


def thumbs_dir(case_id: int) -> Path:
    d = case_dir(case_id) / "visuals" / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def production_dir(case_id: int, language: str) -> Path:
    d = case_dir(case_id) / "production" / language
    d.mkdir(parents=True, exist_ok=True)
    return d


def renders_dir(case_id: int, language: str) -> Path:
    d = case_dir(case_id) / "renders" / language
    d.mkdir(parents=True, exist_ok=True)
    return d


def rel(path: str | Path | None) -> str | None:
    """Repository-relative path for storage in the DB (portable between
    machines); absolute paths outside the repo are kept as they are."""
    if path is None:
        return None
    p = Path(path).resolve()
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def resolve(path: str | None) -> Path | None:
    if not path:
        return None
    p = Path(path)
    return p if p.is_absolute() else ROOT / p
