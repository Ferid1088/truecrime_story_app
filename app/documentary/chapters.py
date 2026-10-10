"""Chapters and the running case timeline — on-screen cards made from the
story itself.

Chapters are the master story's acts. The gap after the last beat of an
act is a chapter_break (the audio plan makes sure of it); the next
chapter's card ("Chapter 2" and its title) sits at the end of that gap,
so the first picture of the new chapter appears just before its first
word. After a cold open (the film opens on a hook beat) the film's title
card follows, then the first chapter's card.

The running timeline: the dated case facts the story tells (the evidence
pack's T-ids that a beat reveals or relies on). A timeline card moves to
the date the story has reached and shows only dates the viewer already
knows — an event appears from the beat that tells it, never earlier.

Texts: role chapter_writer writes the film title, a title per chapter and
a short label per told event, natively in every language of the film;
role chapter_auditor (another model) approves or rejects every text in
every language — faithful to the facts and to the chapter, nothing given
away before the story reveals it (an unsolved case suggests no
solution), sober, a true translation with the narration's spelling of
names. A rejected text is written again with the auditor's reasons (at
most documentary.max_redos times); a text still rejected is left out and
reported — the card then shows only the chapter number, the timeline only
the date. Dates never come from a model: they are the facts' dates,
formatted per language (generated.format_date).
"""

from __future__ import annotations


from app.core.prompts import prompt

import json
import logging
import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    ChapterPlan,
)

log = logging.getLogger(__name__)

LABELS = {"en": "Chapter {n}", "de": "Kapitel {n}", "fa": "فصل {n}", "ar": "الفصل {n}"}
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
EVENT_IDS = ("reveals", "relies_on", "viewer_knows")


def chapter_label(n: int, language: str) -> str:
    text = LABELS.get(language, LABELS["en"]).format(n=n)
    return text.translate(_FA_DIGITS) if language == "fa" else text


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# what the cards are about (deterministic)
# ---------------------------------------------------------------------------


def acts_of(blueprint: dict, structure: dict | None = None) -> list[dict]:
    """The chapters in film order: [{act_id, number, first_beat,
    last_beat, beats, plan_title, purpose, reveals}] — one per act that
    has beats. `reveals`: what the act's turning beats disclose (a title
    may give none of it away)."""
    plans = {a.get("id"): a for a in (structure or {}).get("acts") or [] if isinstance(a, dict)}
    purposes = set(ai_config.attention.firewall_purposes)
    out: list[dict] = []
    for b in blueprint.get("beats") or []:
        act = b.get("act_id") or "act"
        if not out or out[-1]["act_id"] != act:
            p = plans.get(act) or {}
            out.append({"act_id": act, "number": len(out) + 1, "first_beat": b["id"],
                        "beats": [], "plan_title": p.get("title"), "purpose": p.get("purpose"),
                        "reveals": []})
        ch = out[-1]
        ch["beats"].append(b["id"])
        ch["last_beat"] = b["id"]
        if b.get("purpose") in purposes and b.get("summary"):
            ch["reveals"].append(str(b["summary"])[:200])
    return out


def cold_open(blueprint: dict) -> bool:
    """The film opens on a hook beat that is followed by more of its act."""
    beats = blueprint.get("beats") or []
    return (len(beats) > 1 and beats[0].get("purpose") == "hook"
            and beats[1].get("act_id") == beats[0].get("act_id"))


def told_events(blueprint: dict, timeline: list[dict]) -> list[dict]:
    """The dated facts the story tells, in date order, each with the beat
    that first tells it: [{id, date, claim, first_beat}]."""
    first: dict[str, str] = {}
    for b in blueprint.get("beats") or []:
        for key in EVENT_IDS:
            for x in b.get(key) or []:
                if isinstance(x, str) and x.startswith("T") and x not in first:
                    first[x] = b["id"]
    out = [{"id": t["id"], "date": str(t.get("event_date") or ""),
            "claim": str(t.get("claim") or "")[:300], "first_beat": first[t["id"]]}
           for t in timeline or [] if t.get("id") in first and t.get("event_date")]
    out.sort(key=lambda e: (e["date"], e["id"]))
    return out[:ai_config.chapters.max_labelled_events]


_MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"])}


def parse_date_text(text: str | None) -> tuple[int, int | None, int | None] | None:
    """'16 July 2007' / 'July 2007' / '2007' / '2007-07-16' -> (y, m, d)."""
    if not text:
        return None
    t = str(text).strip()
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", t)
    if m:
        return int(m.group(1)), int(m.group(2)) if m.group(2) else None, \
            int(m.group(3)) if m.group(3) else None
    year = month = day = None
    for tok in t.replace(",", " ").split():
        low = re.sub(r"(st|nd|rd|th)$", "", tok.lower().rstrip("."))
        if low in _MONTHS:
            month = _MONTHS[low]
        elif low.isdigit() and len(low) == 4:
            year = int(low)
        elif low.isdigit() and 1 <= int(low) <= 31:
            day = int(low)
    return (year, month, day) if year else None


def same_date(event_date: str, date_text: str | None) -> bool:
    """The beat's date (as precise as it is) is the event's date."""
    a, b = parse_date_text(event_date), parse_date_text(date_text)
    if not a or not b:
        return False
    return all(y is None or x == y for x, y in zip(a, b, strict=True))


def beat_events(blueprint: dict, events: list[dict], requirements: dict) -> dict[str, dict]:
    """Per beat the timeline event a timeline card there moves to: the
    event the beat first tells (the one on the beat's date when it tells
    several), else an earlier-told event on the beat's date."""
    reqs = {r.get("beat_id"): r for r in (requirements or {}).get("beats") or []}
    order = [b["id"] for b in blueprint.get("beats") or []]
    pos = {b: i for i, b in enumerate(order)}
    out: dict[str, dict] = {}
    for bid in order:
        date_text = (reqs.get(bid) or {}).get("date_text")
        here = [e for e in events if e["first_beat"] == bid]
        pick = next((e for e in here if same_date(e["date"], date_text)), None)
        if pick is None and here:
            pick = here[-1]
        if pick is None and date_text:
            pick = next((e for e in events if pos.get(e["first_beat"], 10**6) <= pos[bid]
                         and same_date(e["date"], date_text)), None)
        if pick is not None:
            out[bid] = {"id": pick["id"], "date": pick["date"], "event": pick["claim"][:160]}
    return out


# ---------------------------------------------------------------------------
# writer + auditor
# ---------------------------------------------------------------------------

WRITER_SYSTEM = prompt("documentary/chapters/writer_system")

AUDITOR_SYSTEM = prompt("documentary/chapters/auditor_system")


def _text(x) -> str:
    return " ".join(str(x or "").split()).strip().strip("\"'«»“”„").rstrip(".").strip()


def latest_chapter_plan(db: Session, blueprint_id: int | None) -> ChapterPlan | None:
    if not blueprint_id:
        return None
    return (db.query(ChapterPlan).filter(ChapterPlan.blueprint_id == blueprint_id)
            .order_by(ChapterPlan.id.desc()).first())


def plan_covers(row: ChapterPlan | None, languages: list[str]) -> bool:
    return row is not None and set(languages) <= set(_loads(row.languages_json, []))


# ---------------------------------------------------------------------------
# per language: what compose puts on screen
# ---------------------------------------------------------------------------


def _year_text(d: str, language: str) -> str | None:
    p = parse_date_text(d)
    if not p:
        return None
    return str(p[0]).translate(_FA_DIGITS) if language == "fa" else str(p[0])


def cards_for(row: ChapterPlan | None, language: str) -> dict:
    """The cards of one language: chapter labels/titles, the film title,
    the timeline events with their localized date and label. Texts left
    out are None (the card shows only the number / the date)."""
    from app.documentary.visuals.generated import format_date

    if row is None or not ai_config.chapters.enabled:
        return {}
    plan = _loads(row.plan_json, {})

    def date_text(d: str) -> str:
        p = parse_date_text(d)
        if not p:
            return d
        y, m, day = p
        en = " ".join(str(x) for x in (day, list(_MONTHS)[m - 1].title() if m else None, y)
                      if x is not None)
        return format_date(en, language) or en

    return {
        "cold_open": bool(plan.get("cold_open")),
        "beat_order": [b for c in plan.get("chapters") or [] for b in c.get("beats") or []],
        "film_title": (plan.get("film_title") or {}).get(language),
        "chapters": [{"act_id": c["act_id"], "number": c["number"],
                      "first_beat": c["first_beat"], "last_beat": c.get("last_beat"),
                      "label": chapter_label(c["number"], language),
                      "title": (c.get("title") or {}).get(language)}
                     for c in plan.get("chapters") or []],
        "events": [{"id": e["id"], "date": e["date"], "first_beat": e["first_beat"],
                    "date_text": date_text(e["date"]),
                    "year": _year_text(e["date"], language),
                    "label": (e.get("label") or {}).get(language)}
                   for e in plan.get("events") or []],
    }


def __getattr__(name: str):
    # the agent class lives in app/agents/chapters.py (imported when first asked for)
    if name == "ChapterWriter":
        from app.agents.chapters import ChapterWriter

        return ChapterWriter
    raise AttributeError(name)
