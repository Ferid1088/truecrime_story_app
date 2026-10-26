"""Floor plan: a building room by room with numbered markers, drawn only from a real plan in the case sources
(rooms, doors and markers come from the stored document) - never from an invented layout."""

from __future__ import annotations

import json
from functools import lru_cache

from PIL import Image

from app.documentary.graphics import parts
from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic
from app.documentary.graphics.page import Page

PLAN_LEFT, PLAN_TOP, PLAN_W, PLAN_H = 120, 280, 1120, 680
SIDE_LEFT, SIDE_W = 1340, 460
WALL, INNER_WALL, MARKER = 8, 4, 40
KIND_COLOR = {"found": "amber", "entry": "red", "witness": "blue"}
CREAM = (238, 232, 220)


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
        for d in [*(data.get("doors") or []), *(data.get("entrances") or [])]:
            if not (isinstance(d.get("x"), int) and isinstance(d.get("y"), int) and isinstance(d.get("w"), int)
                    and d.get("axis") in ("h", "v")):
                return "a door or entrance needs a position on the grid, a length and an axis (h or v)"
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
    parts.header(page, T.label(language, "floor_plan"), T.rgb("amber"),
                 data.get("title") or T.label(language, "floor_plan"), 108, lines_max=1)
    rooms = {r["id"]: r for r in data["rooms"]}
    cols = max(r["x"] + r["w"] for r in rooms.values())
    rows = max(r["y"] + r["h"] for r in rooms.values())
    cw, ch = PLAN_W / cols, PLAN_H / rows

    def px(gx: float, gy: float) -> tuple[float, float]:
        return PLAN_LEFT + gx * cw, PLAN_TOP + gy * ch

    page.rect(PLAN_LEFT, PLAN_TOP, PLAN_W, PLAN_H, fill=T.rgb("card"))
    grid = (28, 36, 44)
    for gx in range(cols * 2 + 1):                      # a faint drawing grid, as on plan paper
        page.vline(PLAN_LEFT + gx * cw / 2, PLAN_TOP, PLAN_H, grid, 1)
    for gy in range(rows * 2 + 1):
        page.hline(PLAN_LEFT, PLAN_TOP + gy * ch / 2, PLAN_W, grid, 1)
    for r in rooms.values():                            # inner walls first, the outer wall over them
        x0, y0 = px(r["x"], r["y"])
        page.rect(x0, y0, r["w"] * cw, r["h"] * ch, outline=CREAM, line=INNER_WALL)
    page.rect(PLAN_LEFT, PLAN_TOP, PLAN_W, PLAN_H, outline=CREAM, line=WALL)
    for door in data.get("doors") or []:                # a gap in the wall
        x0, y0 = px(door["x"], door["y"])
        if door["axis"] == "h":
            page.rect(x0, y0 - 6, door["w"] * cw, 12, fill=T.rgb("card"))
        else:
            page.rect(x0 - 6, y0, 12, door["w"] * ch, fill=T.rgb("card"))
    for gate in data.get("entrances") or []:            # where people came in, on the outer wall
        x0, y0 = px(gate["x"], gate["y"])
        if gate["axis"] == "h":
            page.rect(x0, y0 - 12, gate["w"] * cw, 24, fill=T.rgb("amber"))
        else:
            page.rect(x0 - 12, y0, 24, gate["w"] * ch, fill=T.rgb("amber"))
    for r in rooms.values():
        x0, y0 = px(r["x"], r["y"])
        page.text(x0 + 28, y0 + 26, int(r["w"] * cw) - 56, r["label"], "mono", 26, T.rgb("grey"), lines_max=1,
                  min_px=16, upper=True)
    for n, mk in enumerate(data["markers"], 1):
        if step < n:
            continue
        r = rooms[mk["room"]]
        x0, y0 = px(r["x"], r["y"])
        cx = x0 + r["w"] * cw * (0.5 + 0.18 * (-1) ** n * (n % 2)) if n % 2 else x0 + r["w"] * cw * 0.5
        cy = y0 + r["h"] * ch * 0.56
        color = T.rgb(KIND_COLOR[mk["kind"]])
        page.disc(cx, cy, MARKER, color, halo=54, halo_alpha=80)
        page.text(cx - MARKER, cy - 27, 2 * MARKER, str(n), "title_medium", 44, T.rgb("ground"), align="center",
                  lines_max=1, spacing=1.0)
    page.rect(SIDE_LEFT, PLAN_TOP, SIDE_W, 318, fill=T.rgb("card"), outline=T.rgb("rule"), line=2)
    page.tracked(SIDE_LEFT + 50, PLAN_TOP + 38, T.label(language, "legend"), "mono", 26, T.rgb("grey"), 5)
    y = PLAN_TOP + 112
    for kind in T.config().floor_plan["legend_kinds"]:
        page.disc(SIDE_LEFT + 64, y + 22, 14, T.rgb(KIND_COLOR[kind]))
        page.text(SIDE_LEFT + 102, y, SIDE_W - 130, T.label(language, kind), "body", 38, T.rgb("ink"), lines_max=1,
                  min_px=22)
        y += 64
    if step >= 1:
        n = min(step, len(data["markers"]))
        page.rect(SIDE_LEFT, 680, 6, 266, fill=T.rgb("amber"))
        page.tracked(SIDE_LEFT + 54, 718, f"{T.label(language, 'point')} {n}", "mono", 26, T.rgb("amber"), 5)
        page.text(SIDE_LEFT + 54, 756, SIDE_W - 60, data["markers"][n - 1]["text"], "body", 40, T.rgb("ink"),
                  lines_max=4, min_px=26, spacing=1.3)
    footer = f"{T.label(language, 'schematic')} · {data['source'].get('name', '')}".strip(" ·")
    page.text(page.margin, 1002, page.W - 2 * page.margin, footer, "mono", 24, T.rgb("grey"), lines_max=1, min_px=14)
    return page.done()
