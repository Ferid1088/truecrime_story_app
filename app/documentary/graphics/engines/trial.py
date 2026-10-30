"""Engine 8 - how far a piece of evidence carries: what it establishes, what it does not, and its assessment."""

from __future__ import annotations

import json

from PIL import Image

from app.documentary.graphics import theme as T
from app.documentary.graphics.engines import kit
from app.documentary.graphics.page import Page, mode_cache

TOP = 300


class EvidenceCheck(kit.Engine):
    kind = "evidence_check"

    def can_render(self, data: dict) -> str | None:
        n = kit.settings()["items_per_group"]
        item = data.get("item") or {}
        if not item.get("name") or len(item["name"]) > kit.settings()["label_max_chars"] or not item.get("fact_id"):
            return "the evidence item needs a name and the fact it comes from"
        if any("%" in json.dumps(data.get(k), ensure_ascii=False) for k in data):
            return "no percentages or scores: findings are qualitative"
        return (kit.items_problem(data.get("establishes"), "what it shows", n)
                or kit.items_problem(data.get("not_establishes"), "what it does not show", n, minimum=0)
                or kit.items_problem(data.get("supported_by") or [], "support", n, minimum=0)
                or kit.items_problem(data.get("contradicted_by") or [], "contradiction", n, minimum=0)
                or kit.item_problem(data.get("assessment"), "the assessment"))

    def steps(self, data: dict) -> list[str]:
        return (["item"] + (["limits"] if data.get("not_establishes") else [])
                + (["corroboration"] if data.get("supported_by") or data.get("contradicted_by") else [])
                + ["assessment"])

    def draw(self, data_json: str, language: str, step: int):
        return _check(data_json, language, step)


@mode_cache(maxsize=32)
def _check(data_json: str, language: str, step: int) -> Image.Image:
    """The item stays on the left; the right side shows what the current step says (the newest block only, so
    nothing crowds), ending with the documented assessment."""
    data = json.loads(data_json)
    names = EvidenceCheck().steps(data)
    now = names[step - 1] if step else "item"
    page = Page(language)
    m, inner = page.margin, page.width_inside()
    kit.heading(page, "evidence_check", None, 96)
    plate_w = 520
    name_lines, name_px = page.lines(data["item"]["name"], "title", 72, plate_w - 80, 5, 44)
    plate_h = 80 + round(len(name_lines) * name_px * 1.04) + 40
    kit.panel(page, m, TOP, plate_w, plate_h, T.rgb("amber"))
    page.text(m + 40, TOP + 56, plate_w - 80, data["item"]["name"], "title", 72, T.rgb("ink"), lines_max=5,
              min_px=44, spacing=1.04, upper=True)
    left, width, y = m + plate_w + 70, inner - plate_w - 70, TOP
    blocks = {"item": [("establishes", "blue")], "limits": [("not_establishes", "grey")],
              "corroboration": [("supported_by", "amber"), ("contradicted_by", "red")]}
    for key, color in blocks.get(now, []):
        for item in data.get(key) or []:
            if y == TOP or item is (data[key] or [None])[0]:
                page.tracked(left, y, T.label(language, key), "mono", 28, T.rgb(color), 5)
                y += 48
            y = page.text(left, y, width, item["text"], "body", 44, T.rgb("ink"), lines_max=3, min_px=32,
                          spacing=1.3) + 26
    if now == "assessment":
        page.tracked(left, y, T.label(language, "assessment"), "mono", 28, T.rgb("amber"), 5)
        y = page.text(left, y + 52, width, data["assessment"]["text"], "body", 54, T.rgb("ink"), lines_max=6,
                      min_px=36, spacing=1.32)
    kit.within(y, TOP + 640, "the evidence check")
    return page.done()
