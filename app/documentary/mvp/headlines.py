"""Newspaper headline cards from the case's own researched sources: a source that is a listed newspaper, with its
headline, name and date as published. The earliest beat a headline may appear in (so it tells the viewer nothing
early) is decided by an agent and checked against the story's order."""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date, datetime

from app.core.ai_config import ai_config
from app.documentary.graphics.headline import HeadlineCard, known_source

SUFFIX = re.compile(r"\s+[|–—-]\s+[^|–—-]{2,45}$")


def clean_title(title: str, publisher: str, newspaper: str) -> str:
    """The headline as printed: HTML entities decoded and the trailing site/newspaper name removed."""
    text = " ".join(html.unescape(title or "").split())
    for _ in range(2):
        m = SUFFIX.search(text)
        if m and (any(part in m.group(0).lower() for part in (publisher.lower(), newspaper.lower().replace("the ", "")))
                  or len(m.group(0)) < 28):
            text = text[:m.start()].strip()
    return text.strip(" \"'")


def parse_date(value) -> dict | None:
    if isinstance(value, (datetime, date)):
        return {"year": value.year, "month": value.month, "day": value.day}
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return {"year": int(m.group(1)), "month": int(m.group(2)), "day": int(m.group(3))} if m else None


def candidates(sources: dict[int, dict]) -> list[dict]:
    """Every source that is a listed newspaper with a headline, a full date and an English-language page."""
    out = []
    cfg = ai_config.graphics_settings().headline
    for sid, s in sorted(sources.items()):
        newspaper = known_source(s.get("url") or "")
        published = parse_date(s.get("published_at"))
        if not newspaper or not published or published["year"] < 1990 or (s.get("language") or "en") != "en":
            continue
        headline = clean_title(s.get("title") or "", s.get("publisher") or "", newspaper)
        if not 12 <= len(headline) <= cfg["headline_max_chars"]:
            continue
        data = {"headline": headline, "source": newspaper, "url": s["url"], "published": published,
                "fact_id": f"S{sid}", "language": "en"}
        if HeadlineCard().can_render(data) is None:
            out.append(data)
    return out


def placement_key(data: dict) -> str:
    return f"headline|{data['fact_id']}|{hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:10]}"
