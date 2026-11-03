"""Narration previews: a narration voiced for listening, kept in data/previews/<name>/ apart from the film's own narration folders (which a preview never
touches). A preview is the voice alone, in story order, with a `preview.json` that says what it is, what it cost and where each block is."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data" / "previews"
INFO = "preview.json"


def folder(name: str) -> Path:
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"not a preview name: {name!r}")
    return BASE / name


def info(name: str) -> dict:
    return json.loads((folder(name) / INFO).read_text(encoding="utf-8"))


def listing() -> list[dict]:
    """The previews that exist, newest first."""
    out = []
    for path in sorted(BASE.glob(f"*/{INFO}"), key=lambda p: p.stat().st_mtime, reverse=True) if BASE.exists() else []:
        data = json.loads(path.read_text(encoding="utf-8"))
        out.append({"name": path.parent.name, **{k: data[k] for k in ("title", "case_id", "language", "created_at", "duration_seconds", "cost")}})
    return out


def audio_path(name: str) -> Path:
    return folder(name) / info(name)["audio"]


def write_info(name: str, data: dict) -> Path:
    path = folder(name) / INFO
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
