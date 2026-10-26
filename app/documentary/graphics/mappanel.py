"""The panel beside a zoom-in map: the places the camera passes (country, region, town, the place itself), the one
the map is at lit, with the map's credit. Timing is the camera's own (the zoom level reached), so panel and map
cannot drift apart. Drawn as the right side of the frame; for Persian and Arabic the whole page is mirrored."""

from __future__ import annotations

import numpy as np
from PIL import Image

from app.documentary.graphics import theme as T
from app.documentary.graphics.page import Page


def current_step(step_times: list[float], since: float) -> int:
    return max(sum(1 for t in step_times if since >= t) - 1, 0)


def map_width(frame_width: int) -> int:
    from app.core.ai_config import ai_config

    return int(round(frame_width * float(ai_config.zoom().panel["map_share"]) / 2) * 2)


def step_thresholds(names: int, zooms: list[float]) -> list[float]:
    """The zoom level at which each of `names` places becomes current: the widest from the start, the last at the
    closest level, the others spread between."""
    if names <= 1:
        return [zooms[-1]]
    return [zooms[round(i * (len(zooms) - 1) / (names - 1))] for i in range(names)]


class MapPanel:
    def __init__(self, language: str, names: list[str], attribution: list[str]):
        self.language, self.names, self.attribution = language, names, attribution
        self._steps: dict[int, Image.Image] = {}

    def step(self, index: int) -> Image.Image:
        index = min(index, len(self.names) - 1)
        if index not in self._steps:
            self._steps[index] = self._draw(index)
        return self._steps[index]

    def _draw(self, current: int) -> Image.Image:
        canvas = T.config().canvas
        share = 1.0 - map_width(canvas["width"]) / canvas["width"]
        width = int(canvas["width"] * share)
        page = Page(self.language, (width, canvas["height"]), T.rgb("ground"))
        pad = 56
        inner = width - 2 * pad
        page.tracked(pad, 150, T.label(self.language, "map_zoom"), "mono", 26, T.rgb("amber"), 4)
        y = page.text(pad, 196, inner, T.label(self.language, "map_title"), "title", 84, T.rgb("ink"), lines_max=3,
                      min_px=48, spacing=1.0, upper=True) + 50
        line_x = pad + 12
        rows = []
        for n, name in enumerate(self.names):
            last = n == len(self.names) - 1
            head = T.label(self.language, "now_showing") if n == current and last else \
                f"{T.label(self.language, 'step')} {n + 1}"
            rows.append((n, head, name))
        top = y
        gap = 118
        page.vline(line_x, top + 16, gap * (len(rows) - 1), T.rgb("rule"), 3)
        for n, head, name in rows:
            cy = top + 16 + n * gap
            lit = n == current
            reached = n <= current
            page.disc(line_x, cy, 11 if not lit else 14, T.rgb("amber") if reached else T.rgb("grey"),
                      halo=26 if lit else 0, halo_alpha=90)
            page.tracked(line_x + 44, cy - 22, head, "mono", 22, T.rgb("amber") if lit else T.rgb("grey"), 3)
            page.text(line_x + 44, cy + 6, inner - 60, name, "body_bold" if lit else "body", 40,
                      T.rgb("ink") if reached else T.rgb("grey"), lines_max=1, min_px=26)
        footer = " · ".join(self.attribution) if self.attribution else ""
        if footer:
            page.text(pad, canvas["height"] - 110, inner, footer, "mono", 20, T.rgb("grey"), lines_max=3, min_px=14)
        return page.done()

    def compose(self, map_frame: np.ndarray, since: float, step_times: list[float], width: int) -> np.ndarray:
        """The full frame (`width` wide): the map video at one side, the panel at the other (the map at the right
        for fa/ar)."""
        panel = np.asarray(self.step(current_step(step_times, since)))
        size = (width - map_frame.shape[1], map_frame.shape[0])
        if (panel.shape[1], panel.shape[0]) != size:
            import cv2

            panel = cv2.resize(panel, size, interpolation=cv2.INTER_AREA)
        parts = [panel, map_frame] if T.is_rtl(self.language) else [map_frame, panel]
        return np.concatenate(parts, axis=1)
