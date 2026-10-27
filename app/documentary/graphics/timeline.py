"""Timeline: dated events drawn as a line that grows from node to node; the current node has a halo, nothing
that has not been revealed is drawn, and the line pans once the current node would pass the middle of the page. The
points that have panned away stay on the line as small marks with their dates (the road of the whole film). A card
may continue an earlier one: `shown` events are already on the line when it opens and only the others are revealed."""

from __future__ import annotations

import json
import math
from functools import lru_cache

from PIL import Image

from app.documentary.graphics import dates, parts
from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic, clamp01
from app.documentary.graphics.page import Page

LINE_Y = 601
LINE_LEFT, LINE_WIDTH = 144, 1632
SLOT_FIRST, SLOT_STEP = 0.048, 0.2696   # node positions as a share of the line (four nodes fill it, as designed)
NODE_W = 420
HISTORY_STEP, HISTORY_MAX = 64, 560        # the marks of the points that have panned away: spacing and the strip's width
HISTORY_LEFT, HISTORY_DATES_MAX = 12, 14    # their start inside the line; more marks than this are dots without dates
HISTORY_DATE_PX = 22
DATE_PX, YEAR_PX, LABEL_PX = 90, 26, 36


def mix(a, b, k: float):
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b, strict=True))


class Timeline(Graphic):
    kind = "timeline"

    def can_render(self, data: dict) -> str | None:
        cfg = T.config().timeline
        events = data.get("events") or []
        if not events:
            return "no events"
        for n, e in enumerate(events, 1):
            if not dates.valid(e.get("date")):
                return f"event {n}: the date needs a year (and a month before a day)"
            label = " ".join(str(e.get("label", "")).split())
            if not label or len(label) > cfg["node_text_max_chars"]:
                return f"event {n}: the text must be 1-{cfg['node_text_max_chars']} characters"
            if not e.get("fact_id"):
                return f"event {n}: no fact id"
        if not 0 <= int(data.get("shown") or 0) < len(events):
            return "shown: the card needs at least one event that is not on the line yet"
        return None

    def required_cues(self, data: dict) -> int:
        return len(data["events"]) - int(data.get("shown") or 0)

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _frame(json.dumps(data, sort_keys=True, ensure_ascii=False), language,
                      float(int(data.get("shown") or 0) + step))

    def draw_between(self, data, language, step, progress, before, after):
        return _frame(json.dumps(data, sort_keys=True, ensure_ascii=False), language,
                      int(data.get("shown") or 0) + step - 1 + progress)


def layout(newest: float) -> tuple[float, float]:
    """The line's pan (in nodes) and the width of the strip at its left that holds the marks of the points that have
    panned away, when the newest revealed node is at `newest`: the line pans so the newest node stays at or before the
    page's middle, and the strip grows with the panning."""
    high = T.config().timeline["pan_target_range"][1]
    offset = max(0.0, newest - (high - SLOT_FIRST) / SLOT_STEP)
    return offset, min(HISTORY_MAX, HISTORY_STEP * max(0.0, offset - SLOT_FIRST / SLOT_STEP))


def x_of(index: float, newest: float) -> float:
    """Where node `index` sits on the page when the newest revealed node is at `newest` (both counted from 0):
    evenly spaced to the right of the history strip."""
    offset, history = layout(newest)
    return LINE_LEFT + history + (LINE_WIDTH - history) * (SLOT_FIRST + SLOT_STEP * (index - offset))


def block_height(page: Page, event: dict, language: str) -> int:
    big, year = dates.display(event["date"], language)
    lines, px = page.lines(event["label"], "body", LABEL_PX, NODE_W, 3, 26)
    return int(DATE_PX * 1.0) + (int(YEAR_PX * 1.4) if year else 0) + 10 + int(len(lines) * px * 1.3)


@lru_cache(maxsize=64)
def _frame(data_json: str, language: str, shown: float) -> Image.Image:
    """The page when `shown` reveals have happened (a fraction is a reveal in progress)."""
    data = json.loads(data_json)
    events = data["events"]
    page = Page(language)
    parts.header(page, T.label(language, "timeline"), T.rgb("amber"),
                 data.get("title") or T.label(language, "timeline"), 108, lines_max=1)
    page.hline(LINE_LEFT, LINE_Y, LINE_WIDTH, T.rgb("rule"), 4)
    done = int(shown)                     # nodes fully revealed
    partial = shown - done                # the next node is being drawn
    newest = done - 1 + (partial if done < len(events) else 0.0)
    amber, grey, ground, ink = T.rgb("amber"), T.rgb("grey"), T.rgb("ground"), T.rgb("ink")
    if newest >= 0:                       # the filled part of the line, growing while a node is revealed
        start = max(x_of(0, newest), LINE_LEFT)
        page.rect(start, LINE_Y - 2, max(x_of(newest, newest) - start, 0), 5, fill=amber)
    offset, history = layout(newest)
    away = max(0, math.ceil(offset - SLOT_FIRST / SLOT_STEP - 1e-9))     # the points that have panned away
    _history(page, events[:away], language, history, grey, amber)
    for i, event in enumerate(events):
        revealing = i == done and partial > 0 and done < len(events)
        if i >= done and not revealing:
            break
        if i < away:
            continue                      # drawn as a mark in the history strip
        k = 1.0 if i < done else clamp01((partial - 0.45) / 0.55)
        cx = x_of(i, newest)
        if cx > LINE_LEFT + LINE_WIDTH + 30:
            continue
        current = (i == done - 1 and partial == 0) or revealing
        if revealing and partial < 0.45:  # the node arrives as an empty ring, then fills
            page.ring(cx, LINE_Y, 20, grey, 4)
            continue
        page.disc(cx, LINE_Y, 15 if current else 12, mix(ground, amber, k), halo=42 if current else 33,
                  halo_alpha=int((92 if current else 52) * k))
        _node(page, event, language, cx, i % 2 == 0, mix(ground, amber if (current or i >= done - 3) else grey, k),
              mix(ground, grey, k), mix(ground, ink, k), int(255 * k))
    if data.get("source"):
        source = f"{T.label(language, 'source')}: {data['source']}"
        page.text(LINE_LEFT, 990, LINE_WIDTH, source if page.rtl else source.upper(), "mono", 24, grey, align="end",
                  lines_max=1)
    return page.done()


def _history(page: Page, events: list[dict], language: str, width: float, color, mark) -> None:
    """The points that have panned away stay on the line: a small mark each, evenly spread over the strip, with a small
    date above and below in turn (dots only when there are too many to read)."""
    if not events:
        return
    gap = width / len(events)
    for j, event in enumerate(events):
        cx = LINE_LEFT + HISTORY_LEFT + gap * j
        page.disc(cx, LINE_Y, 7, mark)
        if len(events) > HISTORY_DATES_MAX or gap < 40:
            continue
        big, _year = dates.display(event["date"], language)
        y = LINE_Y - 44 - HISTORY_DATE_PX if j % 2 == 0 else LINE_Y + 40
        page.text(cx - 18, y, 150, big, "mono", HISTORY_DATE_PX, color, lines_max=1, spacing=1.2)


def _node(page: Page, event: dict, language: str, cx: float, above: bool, date_color, year_color, label_color,
          alpha: int) -> None:
    """One event's date, year and text, left-aligned from just before its node; above or below the line."""
    big, year = dates.display(event["date"], language)
    top = LINE_Y - 70 - block_height(page, event, language) if above else LINE_Y + 82
    left = cx - 22
    y = page.text(left, top, NODE_W, big, "title", DATE_PX, date_color, lines_max=1, min_px=48, spacing=1.0, upper=True,
                  alpha=alpha)
    if year:
        y = page.text(left, y, NODE_W, year, "mono", YEAR_PX, year_color, lines_max=1, spacing=1.4, alpha=alpha)
    page.text(left, y + 8, NODE_W, event["label"], "body", LABEL_PX, label_color, lines_max=3, min_px=26, spacing=1.3,
              alpha=alpha)
