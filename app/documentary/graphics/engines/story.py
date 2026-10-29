"""Engines 5 and 7 - an interview as it is documented, and the clock of an investigation."""

from __future__ import annotations

import json
from datetime import date

from PIL import Image

from app.documentary.graphics import dates
from app.documentary.graphics import theme as T
from app.documentary.graphics.engines import kit
from app.documentary.graphics.page import Page, mode_cache

TOP = 340
MODES = ("verbatim", "paraphrase")


class InterrogationAnalysis(kit.Engine):
    kind = "interrogation"

    def can_render(self, data: dict) -> str | None:
        cfg = kit.settings()
        turns = data.get("turns") or []
        if not 1 <= len(turns) <= cfg["max_turns"]:
            return f"an interview shows 1 to {cfg['max_turns']} turns"
        for n, t in enumerate(turns, 1):
            problem = kit.item_problem({"basis": "witness", **t}, f"turn {n}")
            if problem or t.get("role") not in ("question", "answer") or t.get("mode") not in MODES or not t.get("who"):
                return problem or f"turn {n}: role, speaker and verbatim/paraphrase are required"
        compare = data.get("compare")
        if compare:
            for key in ("earlier", "later"):
                if kit.item_problem(compare.get(key), key, 110, ("witness", "fact", "allegation")) or not compare[key].get("when"):
                    return f"compare {key}: a statement with its date and the fact it comes from"
            if kit.item_problem(compare.get("record"), "the record", 110, ("fact", "finding", "disputed")):
                return "compare: the independently established fact is required"
        return None

    def steps(self, data: dict) -> list[str]:
        return [f"turn{n}" for n in range(len(data["turns"]))] + (["compare"] if data.get("compare") else [])

    def draw(self, data_json: str, language: str, step: int):
        return _interview(data_json, language, step)


@mode_cache(maxsize=32)
def _interview(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    turns = data["turns"]
    page = Page(language)
    m, inner = page.margin, page.width_inside()
    kit.heading(page, "interrogation", data.get("title"), 64)
    comparing = data.get("compare") and step > len(turns)
    if comparing:
        compare = data["compare"]
        width = (inner - 100) // 3
        panel_h = 80 + max(kit.statement_height(page, width - 72, compare[k], px=42, lines_max=7, min_px=30,
                                                quoted=compare[k].get("mode") == "verbatim")
                           + (50 if compare[k].get("when") else 0) for k in ("earlier", "later", "record")) + 40
        top = kit.centered(TOP, panel_h)
        for n, (key, color, label) in enumerate((("earlier", "blue", "earlier"), ("later", "amber", "later"),
                                                 ("record", "ink", "inconsistent"))):
            if key == "record" and step < len(turns) + 1:
                continue
            left = m + n * (width + 50)
            kit.panel(page, left, top, width, panel_h, T.rgb(color))
            page.tracked(left + 36, top + 34, T.label(language, label), "mono", 26, T.rgb(color), 5)
            y = top + 80
            if compare[key].get("when"):
                y = page.text(left + 36, y, width - 72, compare[key]["when"], "mono", 24, T.rgb("grey"), lines_max=2, min_px=18) + 6
            kit.statement(page, left + 36, y, width - 72, compare[key], px=42, lines_max=7, min_px=30,
                          quoted=compare[key].get("mode") == "verbatim")
        return page.done()
    upto = min(step, len(turns))
    visible = turns[max(0, upto - kit.settings()["visible_dialogue"]):upto]
    y = TOP
    for t in visible:
        newest = t is turns[upto - 1]
        alpha = 255 if newest else kit.settings()["dim_alpha"]
        asking = t["role"] == "question"
        width = inner * 7 // 10
        left = m if asking else m + inner - width
        color = T.rgb("blue" if asking else "amber")
        kit.panel(page, left, y, width, 220, color, alpha)
        page.tracked(left + 32, y + 26, f"{T.label(language, t['role'])} · {t['who']}".upper() if not page.rtl
                     else f"{T.label(language, t['role'])} · {t['who']}", "mono", 24, kit.blend(color, alpha), 4)
        text = " ".join(t["text"].split())
        text = f"“{text}”" if t["mode"] == "verbatim" and language in ("en", "de") else text
        page.text(left + 32, y + 70, width - 64, text, "body", 46, kit.blend(T.rgb("ink"), alpha), lines_max=2,
                  min_px=32, spacing=1.26)
        page.tracked(left + 32, y + 176, T.label(language, "basis_verbatim" if t["mode"] == "verbatim" else "basis_paraphrase"),
                     "mono", 20, kit.blend(T.rgb("grey"), alpha), 3)
        y += 250
    return page.done()


def _span(start: dict, end: dict, language: str) -> str | None:
    """How long between two dated events, in the largest honest unit (days only when both dates have a day)."""
    if not start.get("month") or not end.get("month"):
        years = end["year"] - start["year"]
        return T.label(language, "unit_year" if years == 1 else "unit_years").format(n=dates.numerals(years, language)) \
            if years else None
    if start.get("day") and end.get("day"):
        days = (date(end["year"], end["month"], end["day"]) - date(start["year"], start["month"], start["day"])).days
        if days < 60:
            return T.label(language, "unit_day" if days == 1 else "unit_days").format(n=dates.numerals(days, language))
    months = (end["year"] - start["year"]) * 12 + end["month"] - start["month"]
    if months >= 24:
        return T.label(language, "unit_years").format(n=dates.numerals(months // 12, language))
    return T.label(language, "unit_month" if months == 1 else "unit_months").format(n=dates.numerals(months, language))


def _days(a: dict, b: dict) -> int:
    """Calendar days between two event dates; missing months/days count as the first of the period."""
    d = lambda e: date(e["year"], e.get("month") or 1, e.get("day") or 1)  # noqa: E731
    return (d(b) - d(a)).days


class InvestigationClock(kit.Engine):
    kind = "investigation_clock"

    def can_render(self, data: dict) -> str | None:
        cfg = kit.settings()
        events = data.get("events") or []
        if not 2 <= len(events) <= cfg["max_events"]:
            return f"the clock shows 2 to {cfg['max_events']} dated events"
        previous = None
        for n, e in enumerate(events, 1):
            problem = kit.item_problem(e, f"event {n}", 70)
            if problem or e.get("track") not in ("crime", "investigation") or not dates.valid(e.get("date")):
                return problem or f"event {n}: a track (crime or investigation) and a date with a year"
            if previous and _days(previous, e["date"]) < 0:
                return f"event {n}: events must be in date order"
            previous = e["date"]
        if not any(e["track"] == "investigation" for e in events):
            return "the clock needs at least one investigation event"
        return None

    def steps(self, data: dict) -> list[str]:
        return [f"event{n}" for n in range(len(data["events"]))]

    def draw(self, data_json: str, language: str, step: int):
        return _clock(data_json, language, step)


LINE_Y, STEP_X, NODE_W, FIRST_X = 560, 430, 360, 220


def _slots(events: list[dict]) -> list[dict]:
    """Events in date order with a gap marker before an investigation event that follows a long quiet stretch."""
    out, last = [], None
    gap_days = kit.settings()["gap_min_days"]
    for e in events:
        if e["track"] == "investigation":
            if last and _days(last["date"], e["date"]) >= gap_days:
                out.append({"gap": True, "from": last["date"], "to": e["date"]})
            last = e
        out.append(e)
    return out


@mode_cache(maxsize=32)
def _clock(data_json: str, language: str, step: int) -> Image.Image:
    data = json.loads(data_json)
    slots = _slots(data["events"])
    page = Page(language)
    m, inner = page.margin, page.width_inside()
    kit.heading(page, "clock", None, 96)
    shown_events = 0
    visible = []
    for s in slots:
        if s.get("gap"):
            if shown_events < step:
                visible.append(s)
            continue
        if shown_events < step:
            visible.append(s)
        shown_events += 1
    newest = len(visible) - 1
    offset = max(0.0, newest - 3.2)
    page.hline(m, LINE_Y, inner, T.rgb("rule"), 4)
    for i, s in enumerate(visible):
        cx = FIRST_X + (i - offset) * STEP_X
        if cx < m - 60 or cx > m + inner + 60:
            continue
        alpha = 255 if i == newest else kit.settings()["dim_alpha"]
        if s.get("gap"):
            page.dashed_rect(cx - STEP_X + 40, LINE_Y - 8, STEP_X - 80, 16, T.rgb("grey"), line=3)
            page.text(cx - STEP_X + 20, LINE_Y + 40, STEP_X - 40, T.label(language, "no_developments"), "mono", 22,
                      T.rgb("grey"), align="center", lines_max=2, min_px=16)
            span = _span(s["from"], s["to"], language)
            if span:
                page.text(cx - STEP_X + 20, LINE_Y - 80, STEP_X - 40, span, "title", 44, T.rgb("grey"), align="center",
                          lines_max=1, upper=True)
            continue
        crime = s["track"] == "crime"
        color = T.rgb("red" if crime else "blue")
        page.disc(cx, LINE_Y, 16 if i == newest else 12, kit.blend(color, alpha), halo=40 if i == newest else 0,
                  halo_alpha=80)
        big, year = dates.display(s["date"], language)
        top = LINE_Y - 80 - 230 if crime else LINE_Y + 70
        y = page.text(cx - 20, top, NODE_W, big, "title", 84, kit.blend(color, alpha), lines_max=1, upper=True, min_px=40)
        y = page.text(cx - 20, y, NODE_W, f"{year} · {T.label(language, 'track_' + s['track'])}".strip(" ·") if not page.rtl
                      else f"{year} · {T.label(language, 'track_' + s['track'])}", "mono", 24, T.rgb("grey"), lines_max=2, min_px=18)
        page.text(cx - 20, y + 8, NODE_W, s["text"], "body", 40, kit.blend(T.rgb("ink"), alpha), lines_max=4,
                  min_px=28, spacing=1.26)
    return page.done()
