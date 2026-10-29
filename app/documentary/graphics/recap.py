"""Recap board ("what we know so far"): what is confirmed, what is disputed and what is still unknown, each as a few
key words. Only sections that have items are drawn; each is revealed on its own cue."""

from __future__ import annotations

import json

from PIL import Image

from app.documentary.graphics import parts
from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic
from app.documentary.graphics.page import DoesNotFit, Page, mode_cache

SECTIONS = ("confirmed", "disputed", "unknown")
RULE_COLOR = {"confirmed": "amber", "disputed": "blue", "unknown": "red"}
TOP, WIDTH, GAP = 260, 520, 60
PAD = 52


def sections_with_items(data: dict) -> list[str]:
    return [s for s in SECTIONS if data.get(s)]


class RecapBoard(Graphic):
    kind = "recap"

    def can_render(self, data: dict) -> str | None:
        cfg = T.config().recap
        if not sections_with_items(data):
            return "nothing to recap"
        for section in SECTIONS:
            items = data.get(section) or []
            if len(items) > cfg["max_items"][section]:
                return f"{section}: at most {cfg['max_items'][section]} items"
            for n, item in enumerate(items, 1):
                text = " ".join(str(item.get("text", "")).split())
                if not text or len(text) > cfg["item_max_chars"]:
                    return f"{section} item {n}: the text must be 1-{cfg['item_max_chars']} characters"
                if not item.get("fact_id"):
                    return f"{section} item {n}: no fact id (an item without one is not shown)"
        return None

    def required_cues(self, data: dict) -> int:
        return len(sections_with_items(data))

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _recap(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step)


@mode_cache(maxsize=24)
def _recap(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    page = Page(language)
    m = page.margin
    parts.header(page, "", T.rgb("amber"), T.label(language, "recap_title"), T.config().recap["title_px"], lines_max=1)
    item_h = lambda item: len(page.lines(item["text"], "body", 40, WIDTH - 2 * PAD, 2, 30)[0]) * 56 + 26  # noqa: E731
    height = 150 + max(sum(item_h(i) for i in data[s]) for s in sections_with_items(data)) + PAD // 2
    top = TOP + max(0, (860 - TOP - height) // 2)
    for n, name in enumerate(sections_with_items(data)):
        left = m + n * (WIDTH + GAP)
        shown = step >= n + 1
        color = T.rgb(RULE_COLOR[name])
        parts.card(page, left, top, WIDTH, height, color, alpha=255 if shown else 70)
        if not shown:
            continue
        page.text(left + 52, top + 62, WIDTH - 104, T.label(language, name), "title", 60, color, lines_max=1,
                  min_px=40, spacing=1.0, upper=True)
        y = top + 150
        for item in data[name]:
            y = page.text(left + PAD, y, WIDTH - 2 * PAD, item["text"], "body", 40, T.rgb("ink"), lines_max=2,
                          min_px=30, spacing=1.35) + 26
        if y - 26 > top + height - PAD // 2:
            raise DoesNotFit(f"the {name} items do not fit their card")
    return page.done()
