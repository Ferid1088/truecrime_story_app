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


def listening_plan(spec: dict, pause_ms: int) -> tuple[dict, dict[str, int], dict[str, str]]:
    """A listening test (prompts/pronunciation/listening_tests/*.json) as a voice plan: every item voiced exactly as written in each take, takes of one
    item side by side, nothing around a block (no neighbour text: each sentence is heard on its own). Returns (the plan, the seed bump of each block,
    the label of each block)."""
    blocks, bumps, labels = [], {}, {}
    for item in spec["items"]:
        for take in range(spec["takes"]):
            block_id = f"{item['id']}_t{take + 1}"
            words = len(item["text"].split())
            blocks.append({"block_id": block_id, "section_id": item["id"], "text": item["text"], "word_count": words, "est_seconds": words / 150 * 60,
                           "previous_text": "", "next_text": "", "oversize": False, "pause_after_ms": pause_ms, "pause_after_kind": "paragraph"})
            bumps[block_id] = take
            labels[block_id] = f"{item['id']} · {item['variant']} · take {take + 1} — listen for {item['listen_for']}"
    return {"language": spec["language"], "blocks": blocks}, bumps, labels
