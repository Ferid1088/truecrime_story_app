"""The portrait in coloured pencil: the same strokes as the grey drawing (PortraitDrawing), each taking the colour of the photo where it is
drawn, and a soft colour wash that fades in over the end of the drawing. Settings: config drawing.colour. Nothing here calls a provider."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.core.ai_config import ai_config
from app.documentary.drawing.portrait import CH, CW, PortraitDrawing


class ColourPortraitDrawing(PortraitDrawing):
    """A portrait drawn in the colours of its photo."""

    def __init__(self, photo: Path, seed: int = 7):
        super().__init__(photo, seed)
        cfg = ai_config.drawing_settings().colour
        self.cfg = cfg
        img = cv2.imread(str(photo))
        rgb = cv2.resize(self._window(img), (CW, CH), interpolation=cv2.INTER_CUBIC)
        pencil = cv2.cvtColor(cv2.GaussianBlur(rgb, (0, 0), 1.5), cv2.COLOR_BGR2HSV).astype(np.float32)
        pencil[..., 1] = np.clip(pencil[..., 1] * cfg.pencil_saturation + cfg.pencil_saturation_add, 0, 255)
        pencil[..., 2] = np.clip(pencil[..., 2] * cfg.pencil_value, 0, 255)
        colour = cv2.cvtColor(pencil.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
        self.ink = colour * cfg.ink_mix + np.minimum(colour, cfg.ink_floor) * (1.0 - cfg.ink_mix)
        wash = cv2.cvtColor(cv2.GaussianBlur(rgb, (0, 0), cfg.wash_blur), cv2.COLOR_BGR2HSV).astype(np.float32)
        wash[..., 1] = np.clip(wash[..., 1] * cfg.wash_saturation, 0, 255)
        wash[..., 2] = np.clip(wash[..., 2] * cfg.wash_value + cfg.wash_lift, 0, 255)
        self.wash_colour = cv2.cvtColor(wash.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
        self.wash_mask = cv2.GaussianBlur(np.clip(self.oval * 1.4, 0, 1).astype(np.float32), (0, 0), cfg.wash_mask_blur)[..., None]
        self.wash = 0.0

    @staticmethod
    def _window(img: np.ndarray) -> np.ndarray:
        """The same 4:5 part of the photo that the grey drawing is made from."""
        h, w = img.shape[:2]
        crop_w = min(w, int(h * CW / CH))
        crop_h = int(crop_w * CH / CW)
        x0 = (w - crop_w) // 2
        y0 = max(0, min(h - crop_h, int(h * 0.45 - crop_h / 2)))
        return img[y0:y0 + crop_h, x0:x0 + crop_w]

    def at(self, share: float) -> None:
        """The wash is in from `wash_start` of the drawing time to its end (smoothly)."""
        u = min(max((share - self.cfg.wash_start) / (1.0 - self.cfg.wash_start), 0.0), 1.0)
        self.wash = u * u * (3 - 2 * u)

    def image(self) -> np.ndarray:
        tint = 1 - self.wash * self.cfg.wash_strength * self.wash_mask * (1 - self.wash_colour / 255.0)
        stroke = (self.graphite * self.grain)[..., None] * 0.97
        return np.clip(self.paper * tint * (1 - stroke) + self.ink * stroke, 0, 255).astype(np.uint8)
