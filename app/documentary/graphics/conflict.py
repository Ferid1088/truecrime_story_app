"""Conflict page (one disputed point: who says what, the opposing point, the source of each, the status) and
conflict board (every conflict told so far, one card each)."""

from __future__ import annotations

import json
from functools import lru_cache

from PIL import Image

from app.documentary.graphics import parts
from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic
from app.documentary.graphics.page import Page

STATUSES = ("open", "decided", "refuted")
STATUS_COLOR = {"open": "amber", "decided": "blue", "refuted": "grey"}
CARD_W, CARD_H = 760, 460


class ConflictPage(Graphic):
    kind = "conflict"

    def can_render(self, data: dict) -> str | None:
        cfg = T.config().conflict
        conflict, sides = data.get("conflict") or {}, data.get("sides") or []
        title = " ".join(str(conflict.get("title", "")).split())
        if not title or len(title) > cfg["title_max_chars"]:
            return f"the point in dispute must be 1-{cfg['title_max_chars']} characters"
        if conflict.get("kind") not in cfg["kinds"]:
            return f"kind must be one of {cfg['kinds']}"
        if conflict.get("status") not in STATUSES:
            return f"status must be one of {list(STATUSES)}"
        if len(sides) != 2:
            return "a conflict page needs exactly two sides"
        for n, side in enumerate(sides, 1):
            claim = " ".join(str(side.get("claim", "")).split())
            if not claim or len(claim) > cfg["claim_max_chars"]:
                return f"side {n}: the claim must be 1-{cfg['claim_max_chars']} characters"
            if not (side.get("who") and side.get("source") and side.get("fact_id")):
                return f"side {n}: who, source and fact id are required (no claim without them)"
        if len(str(conflict.get("summary", ""))) > cfg["summary_max_chars"] or not conflict.get("summary"):
            return f"the board summary must be 1-{cfg['summary_max_chars']} characters"
        return None

    def required_cues(self, data: dict) -> int:
        return 3

    def minimum_cues(self, data: dict) -> int:
        return 2        # the planner may leave the status cue out while the point is open

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _conflict_page(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step)


@lru_cache(maxsize=24)
def _conflict_page(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    conflict, sides = data["conflict"], data["sides"]
    page = Page(language)
    m = page.margin
    number = f"{T.label(language, 'conflict')} {int(conflict.get('number', 0)):02d}:" if conflict.get("number") \
        else f"{T.label(language, 'conflict')}:"
    card_top = max(300, parts.header(page, number, T.rgb("red"), conflict["title"], 100) + 36)
    colors = (T.rgb("blue"), T.rgb("red"))
    left_edges = (m, page.W - m - CARD_W)
    for n, side in enumerate(sides):
        shown = step >= n + 1
        parts.card(page, left_edges[n], card_top, CARD_W, CARD_H, colors[n], alpha=255 if shown else 90)
        if not shown:
            continue
        x = left_edges[n] + 56
        inner = CARD_W - 112
        page.tracked(x, card_top + 52, side["who"].upper() if not page.rtl else side["who"], "mono", 26,
                     colors[n], 4)
        claim = f"“{' '.join(side['claim'].split())}”" if language in ("en",) else \
            f"{'«' if language in ('fa', 'ar') else '„'}{' '.join(side['claim'].split())}" \
            f"{'»' if language in ('fa', 'ar') else chr(0x201c)}"
        page.text(x, card_top + 118, inner, claim, "body", 46, T.rgb("ink"), lines_max=5, min_px=32, spacing=1.42)
        parts.source_line(page, x, card_top + CARD_H - 62, inner,
                          f"{T.label(language, 'source')}: {side['source']}".upper() if not page.rtl
                          else f"{T.label(language, 'source')}: {side['source']}", 22)
    if step >= 2:
        cx = page.W // 2
        page.vline(cx, card_top + 10, 130, T.rgb("rule"), 3)
        page.vline(cx, card_top + CARD_H - 140, 130, T.rgb("rule"), 3)
        page.text(cx - 70, card_top + CARD_H // 2 - 40, 140, T.label(language, "vs"), "title", 64, T.rgb("amber"),
                  align="center", lines_max=2, min_px=30, upper=True)
    if step >= 3:
        status = conflict["status"]
        color = T.rgb(STATUS_COLOR[status])
        y = card_top + CARD_H + 60
        width = parts.pill(page, m, y, T.label(language, f"status_{status}"), color, px=28)
        page.text(m + width + 36, y + 8, page.W - 2 * m - width - 36,
                  f"{T.label(language, 'board_summary')}: {conflict['summary']}", "body", 38, T.rgb("ink"),
                  lines_max=1, min_px=28)
    return page.done()


class ConflictBoard(Graphic):
    kind = "conflict_board"

    def can_render(self, data: dict) -> str | None:
        cfg = T.config().conflict_board
        cards = data.get("cards") or []
        if not 1 <= len(cards) <= 12:
            return "the board shows 1 to 12 conflicts"
        for n, c in enumerate(cards, 1):
            if c.get("status") not in STATUSES or not (c.get("title") and c.get("a") and c.get("b")):
                return f"card {n}: title, both sides and a status are required"
        if data.get("new_card") not in {c["id"] for c in cards}:
            return "new_card must name a card on the board"
        if cfg["show_total"] and not data.get("total"):
            return "show_total is on but the plan gave no total"
        return None

    def required_cues(self, data: dict) -> int:
        return 1

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _board(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step)


@lru_cache(maxsize=24)
def _board(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    cfg = T.config().conflict_board
    cards = data["cards"]
    shown = cards if step >= 1 else [c for c in cards if c["id"] != data["new_card"]]
    page = Page(language)
    m = page.margin
    parts.header(page, T.label(language, "conflict_board"), T.rgb("amber"),
                 T.label(language, "conflict_board_title"), 108, lines_max=1)
    total = data.get("total") if cfg["show_total"] else None
    if total:
        page.text(m, 150, page.W - 2 * m, T.label(language, "told_of").format(n=len(shown), total=total), "title", 50,
                  T.rgb("amber"), align="end", lines_max=1, upper=True)
    columns, gap, height, top = cfg["columns"], 60, 300, 320
    width = (page.W - 2 * m - gap * (columns - 1)) // columns
    slots = [*cards[:cfg["max_cards_per_view"]]]
    if total:
        slots += [None] * (min(total, cfg["max_cards_per_view"]) - len(slots))
    for n, c in enumerate(slots):
        col, row = n % columns, n // columns
        left, y = m + col * (width + gap), top + row * (height + gap)
        if c is None or c not in shown:
            if total:
                page.dashed_rect(left, y, width, height, T.rgb("rule"))
                page.text(left, y + height // 2 - 16, width,
                          f"{n + 1:02d} \u00b7 {T.label(language, 'not_yet_told')}", "mono", 28, T.rgb("grey"),
                          align="center", lines_max=1, min_px=18)
            continue
        color = T.rgb(STATUS_COLOR[c["status"]])
        parts.card(page, left, y, width, height, color, rule=6)
        page.tracked(left + 36, y + 42, f"{T.label(language, 'conflict')} {int(c['number']):02d}", "mono", 24,
                     T.rgb("grey"), 3)
        status = T.label(language, f"status_{c['status']}").split(":")[-1].strip()
        pill_w = page.measure(status, "mono", 22) + 36
        parts.pill(page, left + width - pill_w - 36, y + 28, status, color, px=22)
        below = page.text(left + 36, y + 96, width - 72, c["title"], "title", 44, T.rgb("ink"), lines_max=2, min_px=28,
                          spacing=1.0, upper=True) + 14
        below = page.text(left + 36, below, width - 72, c["a"], "body", 32, T.rgb("grey"), lines_max=1, min_px=20,
                          spacing=1.3)
        page.text(left + 36, below + 2, width - 72, c["b"], "body", 32, T.rgb("grey"), lines_max=1, min_px=20,
                  spacing=1.3)
    return page.done()
