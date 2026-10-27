"""Engines 2, 3 and 4 - the path of an evidence item, what is known and not known, and a forensic examination."""

from __future__ import annotations

import json
import math
from functools import lru_cache

from PIL import Image

from app.documentary.graphics import theme as T
from app.documentary.graphics.engines import kit
from app.documentary.graphics.page import Page

STAGES = ("discovery", "collection", "documentation", "transfer", "storage", "analysis", "court")
EXAMS = ("dna", "fingerprint", "toxicology", "trace", "fiber_hair", "digital", "ballistics", "bloodstain")
TOP = 340


class ChainOfCustody(kit.Engine):
    kind = "custody"

    def can_render(self, data: dict) -> str | None:
        cfg = kit.settings()
        item = data.get("item") or {}
        stages = data.get("stages") or []
        if not item.get("name") or len(item["name"]) > cfg["label_max_chars"] or not item.get("fact_id"):
            return "the evidence item needs a name and the fact it comes from"
        if not 2 <= len(stages) <= cfg["max_stages"]:
            return f"a custody journey has 2 to {cfg['max_stages']} documented stages"
        for n, s in enumerate(stages, 1):
            if s.get("stage") not in STAGES:
                return f"stage {n}: the stage must be one of {list(STAGES)}"
            problem = kit.item_problem(s, f"stage {n}", 80, ("fact", "finding", "allegation", "witness", "expert"))
            if problem:
                return problem
            if s.get("gap_after") and not s["gap_after"].get("fact_id"):
                return f"stage {n}: a gap in the records needs the fact that says so"
        return None

    def steps(self, data: dict) -> list[str]:
        return [s["stage"] for s in data["stages"]]

    def draw(self, data_json: str, language: str, step: int):
        return _custody(data_json, language, step)


@lru_cache(maxsize=32)
def _custody(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    stages = data["stages"]
    page = Page(language)
    m, inner = page.margin, page.width_inside()
    kit.heading(page, "custody", data["item"]["name"], 96)
    slot = inner // len(stages)
    line_y = 470
    page.hline(m, line_y, inner, T.rgb("rule"), 4)
    for n, s in enumerate(stages):
        left = m + n * slot
        cx = left + 40
        if n < step - 1:
            page.rect(left + 40, line_y - 2, slot, 5, fill=T.rgb("amber") if not stages[n].get("gap_after") else T.rgb("rule"))
        if stages[n].get("gap_after") and n < step - 1:
            page.dashed_rect(left + 70, line_y - 8, slot - 60, 16, T.rgb("red"), line=3)
        if n >= step:
            page.ring(cx, line_y, 20, T.rgb("rule"), 4)
            continue
        current = n == step - 1
        alpha = 255 if current else kit.settings()["dim_alpha"]
        page.disc(cx, line_y, 22 if current else 14, kit.blend(T.rgb("amber"), alpha), halo=46 if current else 0,
                  halo_alpha=90)
        page.tracked(left, line_y - 78, T.label(language, f"stage_{s['stage']}"), "mono", 30,
                     kit.blend(T.rgb("amber"), alpha), 4)
        kit.statement(page, left, line_y + 56, slot - 40, s, alpha, px=42, lines_max=6, min_px=30)
        if s.get("gap_after") and current:
            top = 850
            kit.panel(page, left, top, inner - n * slot, 120, T.rgb("red"), dashed=True)
            page.tracked(left + 30, top + 24, T.label(language, "custody_gap"), "mono", 26, T.rgb("red"), 4)
            page.text(left + 30, top + 62, inner - n * slot - 60, s["gap_after"]["text"], "body", 30, T.rgb("ink"),
                      lines_max=1, min_px=22)
    return page.done()


class MapOfUnknowns(kit.Engine):
    kind = "unknowns"

    def can_render(self, data: dict) -> str | None:
        known, unknown, disputed = (data.get(k) or [] for k in ("known", "unknown", "disputed"))
        if not known or not unknown:
            return "the map needs at least one known and one unknown point"
        for what, items, allowed in (("known", known, ("fact", "finding", "witness", "expert")),
                                     ("unknown", unknown, ("unknown",)),
                                     ("disputed", disputed, ("disputed", "allegation", "witness"))):
            if what == "disputed" and not items:
                continue
            problem = kit.items_problem(items, what, 3, allowed)
            if problem:
                return problem
        span = data.get("span")
        if span and (kit.item_problem(span.get("start"), "span start", 60, ("fact", "finding", "witness"))
                     or (span.get("end") and kit.item_problem(span["end"], "span end", 60, ("fact", "finding", "witness")))):
            return "the span needs a documented start (and a documented end, or none)"
        return None

    def steps(self, data: dict) -> list[str]:
        return ["known", "unknown"] + (["disputed"] if data.get("disputed") else [])

    def draw(self, data_json: str, language: str, step: int):
        return _unknowns(data_json, language, step)


@lru_cache(maxsize=32)
def _unknowns(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    names = MapOfUnknowns().steps(data)
    shown = names[:step]
    page = Page(language)
    m, inner = page.margin, page.width_inside()
    kit.heading(page, "unknowns", None, 96)
    cols = len(names)
    width = (inner - 50 * (cols - 1)) // cols
    colors = {"known": "blue", "unknown": "amber", "disputed": "red"}
    height = 420 if data.get("span") and "unknown" in shown else 600
    for n, name in enumerate(names):
        if name not in shown:
            continue
        alpha = kit.alpha_of(n, step - 1)
        left = m + n * (width + 50)
        kit.panel(page, left, TOP, width, height, T.rgb(colors[name]), alpha, dashed=name == "unknown")
        page.tracked(left + 36, TOP + 34, T.label(language, f"{name}_col"), "mono", 28,
                     kit.blend(T.rgb(colors[name]), alpha), 5)
        y = TOP + 92
        for i, item in enumerate(data[name]):
            focus = name == "unknown" and i == 0
            y = page.text(left + 36, y, width - 72, item["text"], "title" if focus else "body", 64 if focus else 42,
                          kit.blend(T.rgb(colors[name] if focus else "ink"), alpha), lines_max=3, min_px=34,
                          spacing=1.1 if focus else 1.3, upper=focus) + 22
    if data.get("span") and "unknown" in shown:
        span, y = data["span"], 840
        page.disc(m + 30, y, 14, T.rgb("blue"))
        page.dashed_rect(m + 60, y - 5, inner - 120, 10, T.rgb("amber"), line=3)
        page.text(m + 60 + (inner - 120) // 2 - 40, y - 78, 80, "?", "title", 120, T.rgb("amber"), align="center",
                  lines_max=1)
        page.text(m, y + 36, inner // 2 - 40, span["start"]["text"], "body", 36, T.rgb("ink"), lines_max=2, min_px=26)
        if span.get("end"):
            page.disc(m + inner - 30, y, 14, T.rgb("blue"))
            page.text(m + inner // 2 + 40, y + 36, inner // 2 - 40, span["end"]["text"], "body", 32, T.rgb("ink"),
                      align="end", lines_max=2, min_px=26)
    return page.done()


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


@lru_cache(maxsize=32)
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
