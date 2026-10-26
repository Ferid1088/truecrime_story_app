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
CARD_W, CARD_H, CARD_TOP = 760, 440, 420


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
    parts.header(page, number, T.rgb("red"), conflict["title"], T.size("title_small"))
    colors = (T.rgb("blue"), T.rgb("red"))
    left_edges = (m, page.W - m - CARD_W)
    for n, side in enumerate(sides):
        shown = step >= n + 1
        parts.card(page, left_edges[n], CARD_TOP, CARD_W, CARD_H, colors[n], alpha=255 if shown else 90)
        if not shown:
            continue
        x = left_edges[n] + 44
        inner = CARD_W - 88
        page.tracked(x, CARD_TOP + 40, side["who"].upper() if not page.rtl else side["who"], "mono", 27,
                     colors[n], 4)
        claim = f"“{' '.join(side['claim'].split())}”" if language in ("en",) else \
            f"{'«' if language in ('fa', 'ar') else '„'}{' '.join(side['claim'].split())}" \
            f"{'»' if language in ('fa', 'ar') else chr(0x201c)}"
        page.text(x, CARD_TOP + 96, inner, claim, "body", T.size("claim"), T.rgb("ink"), lines_max=5, min_px=34,
                  spacing=1.28)
        parts.source_line(page, x, CARD_TOP + CARD_H - 70, inner, f"{T.label(language, 'source')}: {side['source']}")
    if step >= 2:
        cx = page.W // 2
        page.vline(cx, CARD_TOP + 10, 150, T.rgb("rule"), 3)
        page.vline(cx, CARD_TOP + CARD_H - 160, 150, T.rgb("rule"), 3)
        page.text(cx - 70, CARD_TOP + CARD_H // 2 - 40, 140, T.label(language, "vs"), "title", 64, T.rgb("amber"),
                  align="center", lines_max=2, min_px=30, upper=True)
    if step >= 3:
        status = conflict["status"]
        color = T.rgb(STATUS_COLOR[status])
        y = CARD_TOP + CARD_H + 52
        width = parts.pill(page, m, y, T.label(language, f"status_{status}"), color)
        page.text(m + width + 36, y + 6, page.W - 2 * m - width - 36,
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
                 T.label(language, "conflict_board_title"), T.size("title_small"), lines_max=1)
    if cfg["show_total"] and data.get("total"):
        page.text(m, 140, page.W - 2 * m, f"{len(shown)} / {data['total']} {T.label(language, 'told')}", "title", 56,
                  T.rgb("amber"), align="end", lines_max=1)
    columns = cfg["columns"]
    gap = 40
    width = (page.W - 2 * m - gap * (columns - 1)) // columns
    height, top = 250, 400
    for n, c in enumerate(cards[:cfg["max_cards_per_view"]]):
        col, row = n % columns, n // columns
        left, y = m + col * (width + gap), top + row * (height + gap)
        if c not in shown:
            page.dashed_rect(left, y, width, height, T.rgb("rule"))
            continue
        color = T.rgb(STATUS_COLOR[c["status"]])
        parts.card(page, left, y, width, height, color, rule=6)
        page.tracked(left + 32, y + 26, f"{T.label(language, 'conflict')} {int(c['number']):02d}", "mono", 24,
                     T.rgb("grey"), 3)
        status = T.label(language, f"status_{c['status']}").split(":")[-1].strip()
        pill_w = page.measure(status, "mono", 20) + 28
        parts.pill(page, left + width - pill_w - 32, y + 18, status, color, px=20)
        page.text(left + 32, y + 66, width - 64, c["title"], "title", 50, T.rgb("ink"), lines_max=2, min_px=30,
                  spacing=1.0, upper=True)
        page.text(left + 32, y + 150, width - 64, f"{c['a']}\n{c['b']}".replace("\n", " · "), "body", 28,
                  T.rgb("grey"), lines_max=3, min_px=22, spacing=1.2)
    return page.done()
