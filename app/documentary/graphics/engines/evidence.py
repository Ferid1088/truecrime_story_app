"""Engine 4 - a forensic examination: the sample, how it works, the reported result and its limits."""

from __future__ import annotations

import json
import math

from PIL import Image

from app.documentary.graphics import theme as T
from app.documentary.graphics.engines import kit
from app.documentary.graphics.page import Page, mode_cache

EXAMS = ("dna", "fingerprint", "toxicology", "trace", "fiber_hair", "digital", "ballistics", "bloodstain")
TOP = 340


class ForensicExamination(kit.Engine):
    kind = "forensic"

    def can_render(self, data: dict) -> str | None:
        if data.get("exam") not in EXAMS:
            return f"the examination must be one of {list(EXAMS)}"
        return (kit.item_problem(data.get("sample"), "the sample", 90, ("fact", "finding", "expert"))
                or kit.item_problem(data.get("method"), "the method", 110, ("illustration", "expert"), True)
                or kit.item_problem(data.get("result"), "the result", 110, ("fact", "finding", "expert", "allegation"))
                or (kit.item_problem(data["limits"], "the limits", 110, ("fact", "finding", "expert", "unknown"))
                    if data.get("limits") else None))

    def steps(self, data: dict) -> list[str]:
        return ["sample", "method", "result"] + (["limits"] if data.get("limits") else [])

    def draw(self, data_json: str, language: str, step: int):
        return _forensic(data_json, language, step)


def icon(page: Page, exam: str, cx: float, cy: float, size: int, color) -> None:
    """A plain line symbol of the kind of examination (an educational sign, never case data)."""
    d, r = page.d, size / 2
    cx = page.W - cx if page.rtl else cx
    w = 5
    if exam == "dna":
        for sign in (1, -1):
            pts = [(cx + sign * r * 0.5 * math.sin(t / 8 * math.pi), cy - r + t * r / 8) for t in range(17)]
            d.line(pts, fill=color, width=w)
        for t in range(0, 17, 2):
            y = cy - r + t * r / 8
            d.line((cx - r * 0.5 * math.sin(t / 8 * math.pi), y, cx + r * 0.5 * math.sin(t / 8 * math.pi), y),
                   fill=color, width=3)
    elif exam == "fingerprint":
        for k in range(1, 6):
            d.arc((cx - k * r / 5, cy - k * r / 5, cx + k * r / 5, cy + k * r / 5), 200, 340 + k * 4, fill=color, width=w)
    elif exam == "toxicology":
        d.line((cx - r * 0.2, cy - r, cx - r * 0.2, cy - r * 0.2, cx - r * 0.7, cy + r * 0.8, cx + r * 0.7,
                cy + r * 0.8, cx + r * 0.2, cy - r * 0.2, cx + r * 0.2, cy - r), fill=color, width=w)
    elif exam == "trace":
        for dx, dy, k in ((-0.5, -0.3, 10), (0.2, -0.6, 7), (0.5, 0.2, 12), (-0.2, 0.5, 8), (0.0, 0.0, 5)):
            d.ellipse((cx + dx * r - k, cy + dy * r - k, cx + dx * r + k, cy + dy * r + k), outline=color, width=4)
    elif exam == "fiber_hair":
        for k in (-0.5, 0, 0.5):
            d.line([(cx + k * r + 18 * math.sin(t / 3), cy - r + t * r / 8) for t in range(17)], fill=color, width=4)
    elif exam == "digital":
        d.rounded_rectangle((cx - r * 0.45, cy - r, cx + r * 0.45, cy + r), 14, outline=color, width=w)
        d.line((cx - r * 0.15, cy + r * 0.8, cx + r * 0.15, cy + r * 0.8), fill=color, width=w)
    elif exam == "ballistics":
        d.rounded_rectangle((cx - r * 0.3, cy - r, cx + r * 0.3, cy + r * 0.4), 40, outline=color, width=w)
        d.rectangle((cx - r * 0.3, cy + r * 0.4, cx + r * 0.3, cy + r), outline=color, width=w)
    else:
        for dx, dy, k in ((-0.45, -0.3, 22), (0.4, -0.5, 16), (0.1, 0.35, 28)):
            d.ellipse((cx + dx * r - k, cy + dy * r - k, cx + dx * r + k, cy + dy * r + k), outline=color, width=w)


@mode_cache(maxsize=32)
def _forensic(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    names = ForensicExamination().steps(data)
    shown = names[:step]
    page = Page(language)
    m, inner = page.margin, page.width_inside()
    kit.heading(page, "forensic", T.label(language, f"exam_{data['exam']}"), 96)
    gap = 50
    width = (inner - 2 * gap) // 3
    height = 420 if "limits" in shown else 560
    columns = (("sample", "f_sample", "blue"), ("method", "f_method", "amber"), ("result", "f_result", "ink"))
    for n, (key, label, color) in enumerate(columns):
        if key not in shown:
            continue
        alpha = kit.alpha_of(names.index(key), step - 1)
        left = m + n * (width + gap)
        kit.panel(page, left, TOP, width, height, T.rgb(color), alpha)
        page.tracked(left + 36, TOP + 34, T.label(language, label), "mono", 26, kit.blend(T.rgb(color), alpha), 5)
        y = TOP + 84
        if key == "method":
            icon(page, data["exam"], left + 80, y + 40, 80, kit.blend(T.rgb("amber"), alpha))
            y += 110
        kit.statement(page, left + 36, y, width - 72, data[key], alpha, px=40, lines_max=5 if key != "method" else 4,
                      min_px=28, tag="illustration" if key == "method" and data[key]["basis"] == "illustration" else None)
    if "limits" in shown:
        top = TOP + height + 36
        kit.panel(page, m, top, inner, 200, T.rgb("grey"), dashed=True)
        page.tracked(m + 40, top + 30, T.label(language, "f_limits"), "mono", 26, T.rgb("grey"), 5)
        page.text(m + 40, top + 76, inner - 80, data["limits"]["text"], "body", 44, T.rgb("ink"), lines_max=2,
                  min_px=30, spacing=1.3)
    return page.done()
