"""Key facts: a few short facts, numbers and times as large text."""

from __future__ import annotations

import json
import re
from functools import lru_cache

from PIL import Image

from app.documentary.graphics import parts
from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic
from app.documentary.graphics.page import Page

VALUE = re.compile(r"""(
    \b\d{1,2}[:.]\d{2}\s?(?:a\.?m\.?|p\.?m\.?)?     # a time of day
  | \b(?:19|20)\d{2}\b                               # a year
  | \$\s?\d[\d,.]*(?:\s?(?:million|billion|thousand))?   # money
  | \b\d[\d,.]*\s?(?:%|percent|per cent)            # a share
  | \b\d[\d,.]*\b                                    # any number
)""", re.VERBOSE | re.IGNORECASE)
TOP = 470


def extract(sentence: str) -> dict | None:
    """The first number/time/date of a stated fact as the big value and the fact itself as the caption; None
    when the fact has no figure or is too long to be a caption."""
    text = " ".join(sentence.split())
    if text.endswith(".") and not re.search(r"\b[ap]\.m\.$", text, re.IGNORECASE):
        text = text[:-1]
    found = VALUE.search(text)
    if not found or len(text) > T.config().key_facts["caption_max_chars"]:
        return None
    return {"value": found.group(1).strip(), "caption": text}


class KeyFacts(Graphic):
    kind = "key_facts"

    def can_render(self, data: dict) -> str | None:
        cfg = T.config().key_facts
        facts = data.get("facts") or []
        if not 1 <= len(facts) <= cfg["max_per_card"]:
            return f"a key-facts card shows 1 to {cfg['max_per_card']} facts"
        for n, f in enumerate(facts, 1):
            if not (f.get("value") and f.get("caption") and f.get("fact_id")):
                return f"fact {n}: value, caption and fact id are required"
            if len(f["value"]) > 14 or len(f["caption"]) > cfg["caption_max_chars"]:
                return f"fact {n}: value up to 14 and caption up to {cfg['caption_max_chars']} characters"
        return None

    def required_cues(self, data: dict) -> int:
        return len(data["facts"])

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _facts(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step)


@lru_cache(maxsize=24)
def _facts(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    facts = data["facts"]
    page = Page(language)
    m = page.margin
    title = data.get("title")
    parts.header(page, T.label(language, "key_facts") if title else "", T.rgb("amber"),
                 title or T.label(language, "key_facts"), T.size("title_small"), lines_max=1)
    gap = 60
    width = (page.W - 2 * m - gap * (len(facts) - 1)) // len(facts)
    px = min(page.lines(f["value"], "title", T.size("big_number"), width, 1, 70)[1] for f in facts)
    for n, fact in enumerate(facts):
        if step < n + 1:
            continue
        left = m + n * (width + gap)
        page.rect(left, TOP, width, 6, fill=T.rgb("amber"))
        page.text(left, TOP + 40, width, fact["value"], "title", px, T.rgb("amber"), lines_max=1, spacing=1.0)
        page.text(left, TOP + 40 + int(px * 1.25), width, fact["caption"], "body", 44, T.rgb("ink"), lines_max=4,
                  min_px=30, spacing=1.25)
    return page.done()
