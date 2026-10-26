"""Recap board ("what we know so far"): what is confirmed, what is disputed, what is still unknown, and where the
film stands. Only sections that have items are drawn; each is revealed on its own cue."""

from __future__ import annotations

import json
from functools import lru_cache

from PIL import Image

from app.documentary.graphics import parts
from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic
from app.documentary.graphics.page import Page

SECTIONS = ("confirmed", "disputed", "unknown")
RULE_COLOR = {"confirmed": "amber", "disputed": "blue", "unknown": "red"}
TOP, HEIGHT, WIDTH, GAP = 312, 540, 520, 60
BAR_LEFT, BAR_RIGHT, BAR_Y = 244, 1678, 951


def sections_with_items(data: dict) -> list[str]:
    return [s for s in SECTIONS if data.get(s)]


def clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


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
        progress = data.get("progress") or {}
        if not 0 <= progress.get("elapsed_s", -1) <= progress.get("total_s", 0):
            return "progress needs elapsed_s within total_s"
        return None

    def required_cues(self, data: dict) -> int:
        return len(sections_with_items(data))

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _recap(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step)


@lru_cache(maxsize=24)
def _recap(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    page = Page(language)
    m = page.margin
    parts.header(page, T.label(language, "recap"), T.rgb("amber"), T.label(language, "recap_title"), 112, lines_max=1)
    for n, name in enumerate(sections_with_items(data)):
        left = m + n * (WIDTH + GAP)
        shown = step >= n + 1
        color = T.rgb(RULE_COLOR[name])
        parts.card(page, left, TOP, WIDTH, HEIGHT, color, alpha=255 if shown else 70)
        if not shown:
            continue
        page.text(left + 52, TOP + 62, WIDTH - 104, T.label(language, name), "title", 60, color, lines_max=1,
                  min_px=40, spacing=1.0, upper=True)
        y = TOP + 150
        for item in data[name]:
            y = page.text(left + 52, y, WIDTH - 104, item["text"], "body", 40, T.rgb("ink"), lines_max=3,
                          min_px=28, spacing=1.35) + 26
    progress = data["progress"]
    page.text(m, BAR_Y - 20, 120, clock(progress["elapsed_s"]), "mono", 28, T.rgb("grey"), lines_max=1)
    page.text(page.W - m - 120, BAR_Y - 20, 120, clock(progress["total_s"]), "mono", 28, T.rgb("grey"), align="end",
              lines_max=1)
    page.rect(BAR_LEFT, BAR_Y, BAR_RIGHT - BAR_LEFT, 6, fill=T.rgb("rule"))
    reach = int((BAR_RIGHT - BAR_LEFT) * progress["elapsed_s"] / max(progress["total_s"], 1))
    page.rect(BAR_LEFT, BAR_Y, reach, 6, fill=T.rgb("amber"))
    page.disc(BAR_LEFT + reach, BAR_Y + 3, 14, T.rgb("amber"))
    return page.done()
