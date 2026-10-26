"""Floor plan: a building room by room with numbered markers, drawn only from a real plan in the case sources
(rooms and markers come from the stored document) - never from an invented layout."""

from __future__ import annotations

import json
from functools import lru_cache

from PIL import Image

from app.documentary.graphics import parts
from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic
from app.documentary.graphics.page import Page

GRID_LEFT, GRID_TOP, GRID_W, GRID_H = 120, 330, 1100, 600
KIND_COLOR = {"found": "amber", "entry": "red", "witness": "blue"}


class FloorPlan(Graphic):
    kind = "floor_plan"

    def can_render(self, data: dict) -> str | None:
        cfg = T.config().floor_plan
        rooms, markers = data.get("rooms") or [], data.get("markers") or []
        source = data.get("source") or {}
        if not (source.get("document_id") and source.get("page")):
            return "no real plan: the case sources have no plan document (a layout is never invented)"
        if not rooms:
            return "no rooms"
        ids = set()
        for r in rooms:
            if not (r.get("id") and r.get("label")) or not all(isinstance(r.get(k), int) for k in ("x", "y", "w", "h")):
                return "every room needs an id, a label and a rectangle on the grid"
            ids.add(r["id"])
        if not 1 <= len(markers) <= cfg["max_markers"]:
            return f"1 to {cfg['max_markers']} markers"
        for n, mk in enumerate(markers, 1):
            if mk.get("kind") not in cfg["legend_kinds"] or mk.get("room") not in ids:
                return f"marker {n}: kind or room unknown"
            if not (mk.get("text") and mk.get("fact_id")):
                return f"marker {n}: text and fact id are required"
        return None

    def required_cues(self, data: dict) -> int:
        return len(data["markers"])

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _plan(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step)


@lru_cache(maxsize=24)
def _plan(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    page = Page(language)
    parts.header(page, T.label(language, "floor_plan"), T.rgb("amber"), data.get("title") or T.label(language, "floor_plan"),
                 T.size("title_small"), lines_max=1)
    rooms = {r["id"]: r for r in data["rooms"]}
    cols = max(r["x"] + r["w"] for r in rooms.values())
    rows = max(r["y"] + r["h"] for r in rooms.values())
    cell = min(GRID_W // cols, GRID_H // rows)
    for r in rooms.values():
        left, top = GRID_LEFT + r["x"] * cell, GRID_TOP + r["y"] * cell
        page.rect(left, top, r["w"] * cell, r["h"] * cell, fill=T.rgb("card"), outline=T.rgb("ink"), line=4)
        page.text(left + 14, top + 10, r["w"] * cell - 28, r["label"], "mono", 24, T.rgb("grey"), lines_max=1,
                  min_px=16, upper=True)
    centers = {}
    for n, mk in enumerate(data["markers"], 1):
        r = rooms[mk["room"]]
        cx = GRID_LEFT + r["x"] * cell + r["w"] * cell // 2 + (n % 2) * 30
        cy = GRID_TOP + r["y"] * cell + r["h"] * cell // 2 + 14
        centers[n] = (cx, cy)
        if step >= n:
            color = T.rgb(KIND_COLOR[mk["kind"]])
            page.disc(cx, cy, 26, color, halo=38, halo_alpha=80)
            page.text(cx - 26, cy - 22, 52, str(n), "title_medium", 42, T.rgb("ground"), align="center", lines_max=1,
                      spacing=1.0)
    left_panel = GRID_LEFT + GRID_W + 60
    panel_w = page.W - left_panel - page.margin
    page.rect(left_panel, GRID_TOP, panel_w, 250, fill=T.rgb("card"))
    page.tracked(left_panel + 30, GRID_TOP + 24, T.label(language, "legend"), "mono", 24, T.rgb("grey"), 4)
    y = GRID_TOP + 80
    for kind in T.config().floor_plan["legend_kinds"]:
        page.disc(left_panel + 46, y + 18, 13, T.rgb(KIND_COLOR[kind]))
        page.text(left_panel + 80, y, panel_w - 100, T.label(language, kind), "body", 32, T.rgb("ink"), lines_max=1,
                  min_px=22)
        y += 54
    if step >= 1:
        mk = data["markers"][min(step, len(data["markers"])) - 1]
        page.rect(left_panel, GRID_TOP + 300, 6, 220, fill=T.rgb("amber"))
        page.tracked(left_panel + 30, GRID_TOP + 300, f"{T.label(language, 'point')} {min(step, len(data['markers']))}",
                     "mono", 24, T.rgb("amber"), 4)
        page.text(left_panel + 30, GRID_TOP + 340, panel_w - 40, mk["text"], "body", 36, T.rgb("ink"), lines_max=5,
                  min_px=26, spacing=1.25)
    page.text(page.margin, 970, page.W - 2 * page.margin,
              f"{T.label(language, 'schematic')} · {data['source'].get('name', '')}".strip(" ·"), "mono", 22,
              T.rgb("grey"), lines_max=1, min_px=16)
    return page.done()
