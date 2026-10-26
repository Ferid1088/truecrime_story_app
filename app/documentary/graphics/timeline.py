"""Timeline: dated events drawn as a line that grows from node to node; the current node has a halo, nothing
that has not been revealed is drawn, and the line pans once more than a few events are on it."""

from __future__ import annotations

import json
from functools import lru_cache

from PIL import Image

from app.documentary.graphics import dates, parts
from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic, clamp01
from app.documentary.graphics.page import Page

LINE_Y = 600
SLOT_FIRST, SLOT_STEP = 0.12, 0.19      # fraction of the line width
NODE_W = 420


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
        return None

    def required_cues(self, data: dict) -> int:
        return len(data["events"])

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _frame(json.dumps(data, sort_keys=True, ensure_ascii=False), language, float(step))

    def draw_between(self, data, language, step, progress, before, after):
        return _frame(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step - 1 + progress)


def x_of(index: float, newest: float, width: int, margin: int) -> float:
    """Where node `index` sits when the newest revealed node is at `newest` (both counted from 0)."""
    low, high = T.config().timeline["pan_target_range"]
    offset = max(0.0, newest - (high - SLOT_FIRST) / SLOT_STEP)
    return margin + width * (SLOT_FIRST + SLOT_STEP * (index - offset))


@lru_cache(maxsize=64)
def _frame(data_json: str, language: str, shown: float) -> Image.Image:
    """The page when `shown` reveals have happened (fractions are a reveal in progress)."""
    data = json.loads(data_json)
    events = data["events"]
    page = Page(language)
    m = page.margin
    width = page.W - 2 * m
    parts.header(page, T.label(language, "timeline"), T.rgb("amber"),
                 data.get("title") or T.label(language, "timeline"), T.size("title_small"), lines_max=1)
    page.hline(m, LINE_Y, width, T.rgb("rule"), 4)
    done = int(shown)                     # nodes fully revealed
    partial = shown - done                # the next node is being drawn
    newest = done - 1 + (partial if done < len(events) else 0.0)
    amber, grey, ground = T.rgb("amber"), T.rgb("grey"), T.rgb("ground")
    # the filled part of the line runs to the newest node (growing while it is revealed)
    if newest >= 0:
        start = max(x_of(0, max(newest, 0), width, m), m)
        end = x_of(newest, newest, width, m)
        page.rect(start, LINE_Y - 2, max(end - start, 0), 5, fill=amber)
    for i, event in enumerate(events):
        if i >= shown + 1e-9 and i >= done + (1 if partial > 0 else 0):
            break
        k = 1.0 if i < done else clamp01((partial - 0.45) / 0.55)
        if k <= 0:
            continue
        cx = x_of(i, max(newest, i if i < done else newest), width, m)
        if cx < m + NODE_W / 2 - 60 or cx > page.W - m - NODE_W / 2 + 60:
            continue                      # panned out of the page
        current = i == done - 1 and partial == 0 or (i == done and partial > 0)
        color = mix(ground, amber if current or i >= done - 3 else grey, k)
        if current:
            page.disc(cx, LINE_Y, 15, amber, halo=34, halo_alpha=int(90 * k))
        else:
            page.disc(cx, LINE_Y, 11, mix(ground, amber, k))
        above = i % 2 == 0
        big, year = dates.display(event["date"], language)
        block = NODE_W
        left = cx - block / 2
        y = LINE_Y - 64 - 215 if above else LINE_Y + 64
        end_y = page.text(left, y, block, big, "title", T.size("date"), color, align="center", lines_max=1,
                          min_px=48, spacing=1.0, upper=True, alpha=int(255 * k))
        if year:
            end_y = page.text(left, end_y + 2, block, year, "mono", 26, mix(ground, grey, k), align="center",
                              lines_max=1, alpha=int(255 * k))
        page.text(left, end_y + 8, block, event["label"], "body", 34, mix(ground, T.rgb("ink"), k),
                  align="center", lines_max=3, min_px=26, spacing=1.22, alpha=int(255 * k))
    if data.get("source"):
        page.tracked(m, 960, f"{T.label(language, 'source')}: {data['source']}", "mono", 24, grey, 3)
    return page.done()
