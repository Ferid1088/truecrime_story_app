"""Engines 1, 6 and 8 - what a court heard, what it decided and how far a piece of evidence carries:
courtroom reasoning, sentencing explanation, evidence check."""

from __future__ import annotations

import json
from functools import lru_cache

from PIL import Image

from app.documentary.graphics import theme as T
from app.documentary.graphics.engines import kit
from app.documentary.graphics.page import Page

CLAIM = ("allegation", "fact", "witness", "expert", "disputed")
TOP = 300
PANEL_H = 450


class CourtroomReasoning(kit.Engine):
    kind = "courtroom"

    def can_render(self, data: dict) -> str | None:
        n = kit.settings()["items_per_group"]
        return (kit.item_problem(data.get("issue"), "the issue", 90)
                or kit.items_problem(data.get("prosecution"), "prosecution", n, CLAIM)
                or kit.items_problem(data.get("defense"), "defense", n, CLAIM)
                or (kit.item_problem(data["evidence"], "the evidence", allowed=CLAIM) if data.get("evidence") else None)
                or kit.item_problem(data.get("finding"), "the finding", allowed=("finding",)))

    def steps(self, data: dict) -> list[str]:
        return ["prosecution", "defense"] + (["evidence"] if data.get("evidence") else []) + ["finding"]

    def draw(self, data_json: str, language: str, step: int):
        return _courtroom(data_json, language, step)


@lru_cache(maxsize=32)
def _courtroom(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    names = CourtroomReasoning().steps(data)
    shown = names[:step]
    page = Page(language)
    m = page.margin
    kit.heading(page, "courtroom", data["issue"]["text"], 96)
    width = (page.W - 2 * m - 60) // 2
    for n, (key, color) in enumerate((("prosecution", "blue"), ("defense", "amber"))):
        if key not in shown:
            continue
        alpha = kit.alpha_of(names.index(key), step - 1)
        left = m + n * (width + 60)
        kit.panel(page, left, TOP, width, PANEL_H, T.rgb(color), alpha)
        page.tracked(left + 44, TOP + 38, T.label(language, key), "mono", 28, kit.blend(T.rgb(color), alpha), 5)
        y = TOP + 88
        for item in data[key]:
            y = kit.statement(page, left + 44, y, width - 88, item, alpha, px=40, lines_max=3, min_px=26,
                              show_tag=item["basis"] != "allegation") + 14
        kit.within(y, TOP + PANEL_H, f"the {key} column")
    band = TOP + PANEL_H + 30
    if "evidence" in shown and "finding" not in shown:
        kit.panel(page, m, band, page.width_inside(), 210, T.rgb("blue"))
        page.tracked(m + 44, band + 30, T.label(language, "court_evidence"), "mono", 28, T.rgb("blue"), 5)
        kit.within(kit.statement(page, m + 44, band + 72, page.width_inside() - 88, data["evidence"], px=48,
                                 lines_max=2, show_tag=False), band + 210, "the evidence")
    if "finding" in shown:
        kit.panel(page, m, band, page.width_inside(), 210, T.rgb("ink"))
        page.tracked(m + 44, band + 30, T.label(language, "court_finding"), "mono", 28, T.rgb("amber"), 5)
        kit.within(kit.statement(page, m + 44, band + 72, page.width_inside() - 88, data["finding"], px=48,
                                 lines_max=2, show_tag=False), band + 210, "the finding")
    return page.done()


class SentencingExplanation(kit.Engine):
    kind = "sentencing"

    def can_render(self, data: dict) -> str | None:
        cfg = kit.settings()
        charges = data.get("charges") or []
        if not 1 <= len(charges) <= cfg["max_charges"]:
            return f"charges: 1 to {cfg['max_charges']}"
        for n, c in enumerate(charges, 1):
            problem = kit.item_problem({**c, "text": c.get("outcome", "")}, f"charge {n}", cfg["label_max_chars"],
                                       ("fact", "finding", "allegation"))
            if problem or not c.get("charge") or len(c["charge"]) > cfg["label_max_chars"]:
                return problem or f"charge {n}: the charge needs a name of up to {cfg['label_max_chars']} characters"
        if len(charges) < 2 and not (data.get("aggravating") or data.get("mitigating") or data.get("arrangement")):
            return "a sentencing card explains more than one line: two charges, a documented factor or the arrangement"
        for key in ("aggravating", "mitigating"):
            if data.get(key) and kit.items_problem(data[key], key, cfg["items_per_group"], ("fact", "finding", "witness")):
                return kit.items_problem(data[key], key, cfg["items_per_group"], ("fact", "finding", "witness"))
        if data.get("arrangement") and (data["arrangement"].get("kind") not in ("concurrent", "consecutive")
                                        or not data["arrangement"].get("fact_id")):
            return "arrangement: concurrent or consecutive, with the fact it comes from"
        if data.get("appeal") and kit.item_problem(data["appeal"], "the appeal", allowed=("fact", "finding")):
            return kit.item_problem(data["appeal"], "the appeal", allowed=("fact", "finding"))
        return kit.item_problem(data.get("final"), "the final sentence", 90, ("finding", "fact"))

    def steps(self, data: dict) -> list[str]:
        return (["charges"] + (["factors"] if data.get("aggravating") or data.get("mitigating") else [])
                + ["final"] + (["appeal"] if data.get("appeal") else []))

    def draw(self, data_json: str, language: str, step: int):
        return _sentencing(data_json, language, step)


@lru_cache(maxsize=32)
def _sentencing(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    names = SentencingExplanation().steps(data)
    shown = names[:step]
    page = Page(language)
    m, inner = page.margin, page.width_inside()
    kit.heading(page, "sentencing", None, 96)
    factors = "factors" in shown
    left_w = (inner - 60) * 6 // 10 if factors else inner
    if "charges" in shown:
        alpha = kit.alpha_of(names.index("charges"), step - 1)
        page.tracked(m, TOP, T.label(language, "charges"), "mono", 28, kit.blend(T.rgb("amber"), alpha), 5)
        y = TOP + 56
        for c in data["charges"]:
            page.rect(m, y, left_w, 4, fill=kit.blend(T.rgb("rule"), alpha))
            page.text(m, y + 22, left_w * 48 // 100, c["charge"], "body_bold", 46, kit.blend(T.rgb("ink"), alpha),
                      lines_max=2, min_px=28)
            page.text(m + left_w * 52 // 100, y + 22, left_w * 48 // 100, c["outcome"], "title", 56,
                      kit.blend(T.rgb("amber"), alpha), align="end", lines_max=1, min_px=26, upper=True)
            y += 118
    if factors:
        alpha = kit.alpha_of(names.index("factors"), step - 1)
        left, width, y = m + left_w + 60, inner - left_w - 60, TOP
        for key, color in (("aggravating", "red"), ("mitigating", "blue")):
            for item in data.get(key) or []:
                page.tracked(left, y, T.label(language, key), "mono", 26, kit.blend(T.rgb(color), alpha), 5)
                y = page.text(left, y + 40, width, item["text"], "body", 42, kit.blend(T.rgb("ink"), alpha),
                              lines_max=4, min_px=30, spacing=1.3) + 34
    if "final" in shown:
        top = 740
        kit.panel(page, m, top, inner, 250, T.rgb("amber"))
        page.tracked(m + 48, top + 40, T.label(language, "final_sentence"), "mono", 28, T.rgb("amber"), 6)
        arrangement = (data.get("arrangement") or {}).get("kind")
        page.text(m + 48, top + 86, inner - 96, data["final"]["text"], "title", 100, T.rgb("ink"), lines_max=1,
                  min_px=48, spacing=1.0, upper=True)
        if arrangement:
            page.text(m + 48, top + 198, inner - 96, T.label(language, arrangement), "mono", 28, T.rgb("grey"),
                      lines_max=1)
    if "appeal" in shown:
        page.tracked(m, 960, T.label(language, "appeal"), "mono", 26, T.rgb("blue"), 5)
        page.text(m + 200, 952, inner - 200, data["appeal"]["text"], "body", 32, T.rgb("ink"), lines_max=1, min_px=24)
    return page.done()


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


@lru_cache(maxsize=32)
def _check(data_json: str, language: str, step: int) -> Image.Image:
    """The item stays on the left; the right side shows what the current step says (the newest block only, so
    nothing crowds), ending with the documented assessment."""
    data = json.loads(data_json)
    names = EvidenceCheck().steps(data)
    now = names[step - 1] if step else "item"
    page = Page(language)
    m, inner = page.margin, page.width_inside()
    kit.heading(page, "evidence_check", data["item"]["name"], 96)
    plate_w = 520
    kit.panel(page, m, TOP, plate_w, 640, T.rgb("amber"))
    page.tracked(m + 40, TOP + 40, T.label(language, "evidence_check"), "mono", 26, T.rgb("amber"), 5)
    page.text(m + 40, TOP + 100, plate_w - 80, data["item"]["name"], "title", 72, T.rgb("ink"), lines_max=5,
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
