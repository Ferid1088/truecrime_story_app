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
TOP, HEIGHT, GAP = 360, 520, 40


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


def _glyph(page: Page, kind: str, cx: float, cy: float, color) -> None:
    """A small symbol so the section is recognisable without its colour."""
    cx = page.W - cx if page.rtl else cx
    d = page.d
    if kind == "confirmed":
        d.line([(cx - 16, cy), (cx - 5, cy + 12), (cx + 18, cy - 14)], fill=color, width=7, joint="curve")
    elif kind == "disputed":
        d.line([(cx - 18, cy - 14), (cx + 18, cy + 14)], fill=color, width=7)
        d.line([(cx - 18, cy + 14), (cx + 18, cy - 14)], fill=color, width=7)
    else:
        d.arc((cx - 17, cy - 20, cx + 17, cy + 6), 200, 360 + 70, fill=color, width=7)
        d.line([(cx, cy + 6), (cx, cy + 12)], fill=color, width=7)
        d.ellipse((cx - 4, cy + 20, cx + 4, cy + 28), fill=color)


@lru_cache(maxsize=24)
def _recap(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    page = Page(language)
    m = page.margin
    parts.header(page, T.label(language, "recap"), T.rgb("amber"), T.label(language, "recap_title"),
                 T.size("title"), lines_max=1)
    names = sections_with_items(data)
    width = (page.W - 2 * m - GAP * (len(names) - 1)) // len(names) if len(names) > 1 else 760
    for n, name in enumerate(names):
        left = m + n * (width + GAP) if len(names) > 1 else m
        shown = step >= n + 1
        color = T.rgb(RULE_COLOR[name])
        parts.card(page, left, TOP, width, HEIGHT, color, alpha=255 if shown else 70)
        if not shown:
            continue
        _glyph(page, name, left + 64, TOP + 78, color)
        page.text(left + 112, TOP + 40, width - 150, T.label(language, name), "title", 62, color, lines_max=1,
                  min_px=40, spacing=1.0, upper=True)
        y = TOP + 140
        for item in data[name]:
            page.rect(left + 40, y + 20, 12, 12, fill=color)
            y = page.text(left + 76, y, width - 116, item["text"], "body", 38, T.rgb("ink"), lines_max=3,
                          min_px=28, spacing=1.22) + 22
    progress = data["progress"]
    bar_left, bar_w, bar_y = m + 150, page.W - 2 * m - 300, 960
    page.text(m, bar_y - 16, 140, clock(progress["elapsed_s"]), "mono", 30, T.rgb("grey"), lines_max=1)
    page.text(page.W - m - 140, bar_y - 16, 140, clock(progress["total_s"]), "mono", 30, T.rgb("grey"),
              align="end", lines_max=1)
    page.rect(bar_left, bar_y, bar_w, 6, fill=T.rgb("rule"))
    reach = int(bar_w * progress["elapsed_s"] / max(progress["total_s"], 1))
    page.rect(bar_left, bar_y, reach, 6, fill=T.rgb("amber"))
    page.disc(bar_left + reach, bar_y + 3, 13, T.rgb("amber"))
    return page.done()
