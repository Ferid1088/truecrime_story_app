"""Timeline: dated events drawn as one line that grows from time point to time point and turns at the page edge like a
snake, so that a long timeline always fits the page. Events of the same date are written together under one time point,
and every next time point is later than the one before. The current point has a halo and nothing that has not been
revealed is drawn; a language that reads from the right starts at the right."""

from __future__ import annotations

import json
import math

from PIL import Image

from app.documentary.graphics import dates, parts
from app.documentary.graphics import theme as T
from app.documentary.graphics.base import Graphic, clamp01
from app.documentary.graphics.page import Page, mode_cache

ROW_X0, ROW_X1 = 170, 1370            # the first and the last time point of a row (design coordinates, left to right)
TURN_X = 1860                         # where the line has turned back at the page edge
POINTS_PER_ROW = 4
NODE_W = 330
ROW_Y = {1: (560,), 2: (370, 700)}    # the line of each row, for a timeline of one row and of two rows
DATE_PX, YEAR_PX, LABEL_PX = 68, 26, 28
STEP_PX = 6


def mix(a, b, k: float):
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b, strict=True))


def date_key(date: dict) -> tuple[int, int, int]:
    return date["year"], date.get("month") or 0, date.get("day") or 0


def merge_points(events: list[dict]) -> list[dict]:
    """The time points of some events ({key, label, date}): one point per date, in date order, the texts of a date together
    (as many as a point may show, each cut to the length a text may have)."""
    cfg = T.config().timeline
    merged: dict[tuple[int, int, int], dict] = {}
    for e in events:
        point = merged.setdefault(date_key(e["date"]), {"date": e["date"], "labels": [], "fact_id": e["key"]})
        label = " ".join(e["label"].split())[:cfg["node_text_max_chars"]].rstrip()
        if label not in point["labels"]:
            point["labels"].append(label)
    return [{**p, "labels": p["labels"][:cfg["max_labels_per_point"]]} for _, p in sorted(merged.items())]


class Timeline(Graphic):
    kind = "timeline"

    def can_render(self, data: dict) -> str | None:
        cfg = T.config().timeline
        points = data.get("points") or []
        if not points:
            return "no time points"
        if len(points) > cfg["max_points"]:
            return f"at most {cfg['max_points']} time points on one card"
        previous = None
        for n, p in enumerate(points, 1):
            if not dates.valid(p.get("date")):
                return f"point {n}: the date needs a year (and a month before a day)"
            if previous is not None and date_key(p["date"]) <= previous:
                return f"point {n}: every time point must be later than the one before"
            previous = date_key(p["date"])
            labels = p.get("labels") or []
            if not 1 <= len(labels) <= cfg["max_labels_per_point"]:
                return f"point {n}: 1 to {cfg['max_labels_per_point']} events"
            if any(not " ".join(str(t).split()) or len(" ".join(str(t).split())) > cfg["node_text_max_chars"]
                   for t in labels):
                return f"point {n}: every event text must be 1-{cfg['node_text_max_chars']} characters"
            if not p.get("fact_id"):
                return f"point {n}: no fact id"
        return None

    def required_cues(self, data: dict) -> int:
        return len(data["points"])

    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        return _frame(json.dumps(data, sort_keys=True, ensure_ascii=False), language, float(step))

    def draw_between(self, data, language, step, progress, before, after):
        return _frame(json.dumps(data, sort_keys=True, ensure_ascii=False), language, step - 1 + progress)


def rows_of(count: int) -> int:
    return max(1, math.ceil(count / POINTS_PER_ROW))


def node_positions(count: int) -> list[tuple[float, float]]:
    """The place of every time point in design coordinates: row after row, the second row running back."""
    rows = rows_of(count)
    ys = ROW_Y[rows]
    step = (ROW_X1 - ROW_X0) / (POINTS_PER_ROW - 1)
    out = []
    for i in range(count):
        row, k = divmod(i, POINTS_PER_ROW)
        out.append((ROW_X0 + k * step if row % 2 == 0 else ROW_X1 - k * step, ys[row]))
    return out


def path_points(count: int) -> tuple[list[tuple[float, float]], list[float]]:
    """The whole snake as a polyline (design coordinates) and the distance along it at which each time point lies."""
    nodes = node_positions(count)
    rows = rows_of(count)
    ys = ROW_Y[rows]
    pts: list[tuple[float, float]] = [(ROW_X0 - 60, ys[0])]
    at: list[float] = []
    for i, (x, y) in enumerate(nodes):
        pts.append((x, y))
        at.append(len(pts) - 1)
        row, k = divmod(i, POINTS_PER_ROW)
        if k == POINTS_PER_ROW - 1 and row + 1 < rows:               # the line turns at the page edge to the next row
            edge_x = TURN_X if row % 2 == 0 else ROW_X0 - 90
            radius = (ys[row + 1] - ys[row]) / 2
            sign = 1 if row % 2 == 0 else -1
            pts.append((edge_x - sign * radius, ys[row]))
            for a in range(1, 18):
                ang = -math.pi / 2 + math.pi * a / 17
                pts.append((edge_x - sign * radius + sign * radius * math.cos(ang), ys[row] + radius + radius * math.sin(ang)))
    # a short tail after the last point
    last_x, last_y = nodes[-1]
    direction = 1 if (rows_of(count) - 1) % 2 == 0 else -1
    pts.append((last_x + direction * 70, last_y))
    # distance along the polyline of each time point
    cum = [0.0]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        cum.append(cum[-1] + math.hypot(x1 - x0, y1 - y0))
    return pts, [cum[i] for i in at]


def _cut(pts: list[tuple[float, float]], length: float) -> list[tuple[float, float]]:
    """The polyline up to `length` along it."""
    out = [pts[0]]
    run = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        seg = math.hypot(x1 - x0, y1 - y0)
        if run + seg >= length:
            k = (length - run) / seg if seg else 0.0
            out.append((x0 + (x1 - x0) * k, y0 + (y1 - y0) * k))
            return out
        out.append((x1, y1))
        run += seg
    return out


@mode_cache(maxsize=64)
def _frame(data_json: str, language: str, shown: float) -> Image.Image:
    """The page when `shown` reveals have happened (a fraction is a reveal in progress)."""
    data = json.loads(data_json)
    points = data["points"]
    page = Page(language)
    parts.header(page, T.label(language, "timeline"), T.rgb("amber"),
                 data.get("title") or T.label(language, "timeline"), 84, lines_max=1)
    amber, grey, ground, ink, rule = T.rgb("amber"), T.rgb("grey"), T.rgb("ground"), T.rgb("ink"), T.rgb("rule")
    pts, at = path_points(len(points))

    def screen(path):                     # a language that reads from the right starts at the right
        return [(page.W - x if page.rtl else x, y) for x, y in path]

    page.d.line(screen(pts), fill=rule, width=4, joint="curve")
    done = int(shown)
    partial = shown - done
    if shown > 0:
        full = at[min(done, len(points)) - 1] if done else 0.0
        reach = full if done >= len(points) or partial == 0 else full + (at[done] - full) * clamp01(partial)
        if reach > 0:
            page.d.line(screen(_cut(pts, reach)), fill=amber, width=5, joint="curve")
    nodes = node_positions(len(points))
    for i, point in enumerate(points):
        revealing = i == done and partial > 0
        if i >= done and not revealing:
            break
        k = 1.0 if i < done else clamp01((partial - 0.45) / 0.55)
        cx, cy = nodes[i]
        current = (i == done - 1 and partial == 0) or revealing
        if revealing and partial < 0.45:  # the point arrives as an empty ring, then fills
            page.ring(cx, cy, 20, grey, 4)
            continue
        page.disc(cx, cy, 15 if current else 12, mix(ground, amber, k), halo=42 if current else 33,
                  halo_alpha=int((92 if current else 52) * k))
        _node(page, point, language, cx, cy, mix(ground, amber if current or i >= done - 3 else grey, k),
              mix(ground, grey, k), mix(ground, ink, k), int(255 * k), rows_of(len(points)))
    if data.get("source"):
        source = f"{T.label(language, 'source')}: {data['source']}"
        page.text(ROW_X0, 940, ROW_X1 - ROW_X0 + 300, source if page.rtl else source.upper(), "mono", 24, grey, align="end",
                  lines_max=1)
    return page.done()


def _node(page: Page, point: dict, language: str, cx: float, cy: float, date_color, year_color, label_color, alpha: int,
          rows: int) -> None:
    """One time point's date and year above its node, and the events under it."""
    big, year = dates.display(point["date"], language)
    left = cx - 22
    top = cy - 94 - (int(YEAR_PX * 1.4) if year else 0)
    y = page.text(left, top, NODE_W, big, "title", DATE_PX, date_color, lines_max=1, min_px=44, spacing=1.0, upper=True,
                  alpha=alpha)
    if year:
        page.text(left, y, NODE_W, year, "mono", YEAR_PX, year_color, lines_max=1, spacing=1.4, alpha=alpha)
    y = cy + 34
    room = 150 if rows > 1 else 260
    for label in point["labels"]:
        y = page.text(left, y, NODE_W, label, "body", LABEL_PX, label_color, lines_max=3 if rows == 1 else 2, min_px=22,
                      spacing=1.2, alpha=alpha) + 8
        if y - cy > room:
            break
