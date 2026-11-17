"""The writer's few-shot examples: the titles and captions of the most-viewed true-crime Shorts of one language, collected from public YouTube search (data/publishing_examples/<language>.json,
ordered by views). The collection is third-party text and stays out of the repository; without it the writer simply gets no examples."""

from __future__ import annotations

import json
from pathlib import Path

from app.publishing.config import GenerationSettings

ROOT = Path(__file__).resolve().parents[3]


def examples_for(language: str, settings: GenerationSettings) -> list[dict]:
    """At most `examples_per_call` of the language's examples, each as {title, caption, views}; the caption is cut to `example_caption_chars`. [] when there is no collection."""
    path = ROOT / settings.examples_dir / f"{language}.json"
    if settings.examples_per_call == 0 or not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("examples", [])
    except (OSError, ValueError):
        return []
    return [{"title": r["title"], "caption": (r.get("caption") or "")[: settings.example_caption_chars], "views": r.get("views")} for r in rows[: settings.examples_per_call]]
