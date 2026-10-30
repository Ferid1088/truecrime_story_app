"""The page around a zoom-in map: the map takes `zoom_map.panel.map_share` of the frame; beside it a panel lists the
places the camera passes (country, region, town, the place itself) with the current one lit; on the map itself sit
the level and scale label and the card naming the marked place. Timing is the camera's own (the zoom level reached),
so panel and map cannot drift apart. For Persian and Arabic the whole page is mirrored."""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

from app.core.ai_config import ai_config
from app.documentary.graphics import theme as T
from app.documentary.graphics.page import Page
from app.documentary.visuals import shaping

MAP_TEXT, MAP_TEXT_BORDER = (255, 255, 255, 255), (0, 0, 0, 255)       # names on the map: pure white with a black border


def outlined(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font, language: str) -> None:
    """Text on the map: white, with a black border that keeps it readable on any tile."""
    draw.text(xy, text, font=font, fill=MAP_TEXT, stroke_width=max(2, round(font.size * 0.12)), stroke_fill=MAP_TEXT_BORDER,
              **shaping.kwargs(language))


LEVELS = ("level_country", "level_region", "level_town", "level_place")
SCALE_STEPS = (1, 2, 5)


def current_step(step_times: list[float], since: float) -> int:
    return max(sum(1 for t in step_times if since >= t) - 1, 0)


def map_width(frame_width: int) -> int:
    return int(round(frame_width * float(ai_config.zoom().panel["map_share"]) / 2) * 2)


def step_thresholds(names: int, zooms: list[float]) -> list[float]:
    """The zoom level at which each of `names` places becomes current: the widest from the start, the last at the
    closest level, the others spread between."""
    if names <= 1:
        return [zooms[-1]]
    return [zooms[round(i * (len(zooms) - 1) / (names - 1))] for i in range(names)]


def level_keys(count: int) -> list[str]:
    """The level label of each listed place: the widest is the country level, the last always the street level."""
    if count <= 1:
        return [LEVELS[-1]]
    return [LEVELS[round(i * (len(LEVELS) - 1) / (count - 1))] for i in range(count)]


def scale_text(zoom: float, lat: float) -> str:
    """'1:2,000' for a MapLibre zoom (512 px world) at a latitude, on a 96 dpi screen, rounded to 1, 2 or 5 times a
    power of ten (a scale label, not a measurement)."""
    metres_per_px = 78271.517 * math.cos(math.radians(lat)) / 2 ** zoom
    denominator = metres_per_px / 0.00026458
    power = 10 ** math.floor(math.log10(max(denominator, 1.0)))
    nearest = min((s * power for s in (*SCALE_STEPS, 10)), key=lambda v: abs(math.log(v / denominator)))
    return f"1:{int(nearest):,}"


def project(lon: float, lat: float, camera: dict, width: int, height: int) -> tuple[float, float]:
    """Screen position of a point for a camera looking straight down (no pitch, no bearing)."""
    world = 512.0 * 2 ** camera["zoom"]

    def mx(x: float) -> float:
        return (x + 180.0) / 360.0 * world

    def my(y: float) -> float:
        s = math.sin(math.radians(max(min(y, 85.05), -85.05)))
        return (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * world

    return width / 2 + mx(lon) - mx(camera["lon"]), height / 2 + my(lat) - my(camera["lat"])


def camera_at(curve: list[dict], since: float) -> dict:
    """The camera of the sampled curve at `since` seconds (the sample just before it)."""
    return next((c for c in reversed(curve) if c["t"] <= since), curve[0])


class MapPanel:
    def __init__(self, language: str, panel: dict, place: str, attribution: list[str]):
        self.language, self.names, self.attribution = language, panel["names"], attribution
        self.curve, self.target, self.place = panel.get("camera") or [], panel.get("target"), place.split(",")[0].strip()
        self._steps: dict[int, Image.Image] = {}

    # -- the panel -----------------------------------------------------------
    def step(self, index: int) -> Image.Image:
        index = min(index, len(self.names) - 1)
        if index not in self._steps:
            self._steps[index] = self._draw(index)
        return self._steps[index]

    def _draw(self, current: int) -> Image.Image:
        canvas = T.config().canvas
        width = canvas["width"] - map_width(canvas["width"])
        page = Page(self.language, (width, canvas["height"]), T.rgb("ground"))
        pad = 72
        inner = width - 2 * pad
        page.tracked(pad, 88, T.label(self.language, "map_zoom"), "mono", 26, T.rgb("amber"), 4)
        page.text(pad, 124, inner, T.label(self.language, "map_title"), "title", 104, T.rgb("ink"), lines_max=3,
                  min_px=52, spacing=1.0, upper=True)
        line_x, top, gap = pad + 14, 392, 116
        rows = len(self.names)
        page.vline(line_x, top + 16, gap * (rows - 1), T.rgb("rule"), 3)
        for n, name in enumerate(self.names):
            cy = top + 16 + n * gap
            lit, passed = n == current, n < current
            page.disc(line_x, cy, 14 if lit else 12, T.rgb("amber") if lit else (T.rgb("grey") if passed else T.rgb("rule")),
                      halo=30 if lit else 0, halo_alpha=90)
            head = T.label(self.language, "now_showing") if lit and n == rows - 1 else f"{T.label(self.language, 'step')} {n + 1}"
            page.tracked(line_x + 46, cy - 24, head, "mono", 22, T.rgb("amber") if lit else T.rgb("grey"), 3)
            page.text(line_x + 46, cy + 4, inner - 60, name, "body_bold" if lit else "body", 40,
                      T.rgb("ink") if lit or passed else T.rgb("grey"), lines_max=1, min_px=24)
        footer = " · ".join([T.label(self.language, "map_source"), *self.attribution])
        lines_max = int(ai_config.zoom().panel["footer_lines_max"])
        lines, used = page.lines(footer, "mono", 20, inner, lines_max, 14)
        page.text(pad, canvas["height"] - 44 - len(lines) * int(used * 1.18 * (1.22 if page.rtl else 1.0)), inner, footer,
                  "mono", 20, T.rgb("grey"), lines_max=lines_max, min_px=14)
        return page.done()

    # -- on the map -------------------------------------------------------------
    def _annotate(self, map_frame: np.ndarray, since: float, step: int) -> np.ndarray:
        """The level and scale label, and (once the camera is at town level or closer) the card naming the place."""
        if not self.curve or self.target is None:
            return map_frame
        camera = camera_at(self.curve, since)
        height, width = map_frame.shape[:2]
        img = Image.fromarray(map_frame).convert("RGBA")
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        rtl = T.is_rtl(self.language)
        level = T.label(self.language, level_keys(len(self.names))[min(step, len(self.names) - 1)])
        label = f"{level} · {scale_text(camera['zoom'], self.target[0])}"
        font = T.font(self.language, "mono", 28)
        w = shaping.width(label, font, self.language)
        x = width - 56 - w if rtl else 56
        outlined(draw, (x, 54), shaping.display(label, self.language), font, self.language)
        if step >= len(self.names) - 2 and not camera.get("pitch") and not camera.get("bearing"):
            self._marker_card(draw, camera, width, height, rtl)
        return np.asarray(Image.alpha_composite(img, layer).convert("RGB"))

    def _marker_card(self, draw: ImageDraw.ImageDraw, camera: dict, width: int, height: int, rtl: bool) -> None:
        x, y = project(self.target[1], self.target[0], camera, width, height)
        small, big = T.font(self.language, "mono", 22), T.font(self.language, "title", 46)
        head = T.label(self.language, "marker_place")
        name = self.place.upper() if not rtl else self.place
        w = max(shaping.width(head, small, self.language), shaping.width(name, big, self.language)) + 56
        h, gap = 118, 36
        left = x - gap - w if rtl else x + gap
        if not 0 <= left <= width - w or not 0 <= y - h / 2 <= height - h:
            return
        outlined(draw, (left + 28, y - h / 2 + 18), shaping.display(head, self.language), small, self.language)
        outlined(draw, (left + 28, y - h / 2 + 46), shaping.display(name, self.language), big, self.language)

    def compose(self, map_frame: np.ndarray, since: float, step_times: list[float], width: int) -> np.ndarray:
        """The full frame (`width` wide): the map video (with its labels) at one side, the panel at the other."""
        step = current_step(step_times, since)
        panel = np.asarray(self.step(step))
        size = (width - map_frame.shape[1], map_frame.shape[0])
        if (panel.shape[1], panel.shape[0]) != size:
            import cv2

            panel = cv2.resize(panel, size, interpolation=cv2.INTER_AREA)
        annotated = self._annotate(map_frame, since, step)
        parts = [panel, annotated] if T.is_rtl(self.language) else [annotated, panel]
        return np.concatenate(parts, axis=1)
