"""Pictures that would give the story away at a beat.

Two deterministic rules on top of the claim firewall (director.blocked_at):

  * investigation pictures (search, police, cordon ...) never appear
    before the story's incident — the audience has not lost anyone yet;
  * custody pictures (inmate uniform, courtroom, sentencing, handcuffs,
    mugshot ...) never appear before the beat in which the story tells of
    the arrest — a man in an orange jumpsuit answers the film's question
    before the film has asked it.

What a picture shows is read from what the vision check saw (its
description / depicts), not from the article headline it came with:
a smiling family portrait on a page titled "... sentenced to life" is
not a custody picture.
"""

from __future__ import annotations

import json

from app.core.ai_config import ai_config


def _seen(a, with_title: bool) -> str:
    try:
        ver = json.loads(a.verification_json or "{}") if a.verification_json else {}
    except (TypeError, ValueError):
        ver = {}
    parts = [a.description, a.caption, ver.get("depicts")]
    if with_title:
        parts += [a.title, a.found_for]
    return " ".join(str(x or "") for x in parts).lower()


def shows_investigation(a) -> bool:
    if a is None:
        return False
    text = _seen(a, with_title=True)
    return any(t in text for t in ai_config.attention.investigation_terms)


def shows_custody(a) -> bool:
    if a is None:
        return False
    text = _seen(a, with_title=False)
    return any(t in text for t in ai_config.attention.custody_terms)


def arrest_beat(blueprint: dict | None) -> str | None:
    """The first beat whose content tells of an arrest or charge (the
    blueprint's own words), or None when the story never gets there."""
    terms = [t.lower() for t in ai_config.attention.arrest_terms]
    for b in (blueprint or {}).get("beats") or []:
        text = " ".join(str(b.get(k) or "") for k in
                        ("content", "summary", "goal", "question", "turn")).lower()
        if any(t in text for t in terms):
            return b.get("id") or b.get("beat_id")
    return None


def beats_before(order: list[str], beat: str | None) -> set[str]:
    return set(order[:order.index(beat)]) if beat in order else set()


def revealed_by(a) -> set[str]:
    """The case facts a picture/piece shows (what its vision check saw)."""
    raw = a.get("reveals") if isinstance(a, dict) else getattr(a, "reveals_json", None)
    if isinstance(raw, list):
        return {str(x) for x in raw}
    try:
        return {str(x) for x in (json.loads(raw) if raw else [])}
    except (TypeError, ValueError):
        return set()


def reveal_blocks(blueprint: dict | None) -> dict[str, list[str]]:
    """Per beat, the facts the story reveals only LATER (the claim
    firewall of director.blocked_at) — only beats where something is
    blocked."""
    from app.documentary.visuals.director import blocked_at

    out = {}
    for b in (blueprint or {}).get("beats") or []:
        ids = sorted(blocked_at(blueprint, b["id"]))
        if ids:
            out[b["id"]] = ids
    return out


def late_reveals(blueprint: dict | None, opening_beats: int = 2) -> set[str]:
    """Evidence ids the story withholds after its first `opening_beats`
    beats — what a title or thumbnail must not give away. Same rule as
    the picture firewall (`blocked_at`)."""
    from app.documentary.visuals.director import blocked_at

    beats = (blueprint or {}).get("beats") or []
    if opening_beats < 1 or len(beats) < opening_beats:
        return set()
    return blocked_at(blueprint, beats[opening_beats - 1]["id"])


def story_point(blueprint: dict | None, beat_id: str | None, recent: int = 6) -> dict:
    """Where the story is at a beat, for the auditors and the director:
    what the viewer has been told (the last few beats) and what the story
    tells only later (its later reveal/contradiction/evidence/false-lead/
    chapter-end beats). A picture may show nothing of the second list."""
    beats = (blueprint or {}).get("beats") or []
    ids = [b["id"] for b in beats]
    if beat_id not in ids:
        return {}
    i = ids.index(beat_id)
    purposes = set(ai_config.attention.firewall_purposes)
    return {
        "beat": beat_id,
        "told_so_far": [str(b.get("summary") or "")[:160] for b in beats[max(0, i - recent + 1):i + 1]
                        if b.get("summary")],
        "told_later": [str(b.get("summary") or "")[:160] for b in beats[i + 1:]
                       if b.get("purpose") in purposes and b.get("summary")][:10],
    }


class Firewall:
    """Which pictures may not be shown at which beat (see module doc),
    plus the claim firewall: a picture whose vision check found a fact
    the story reveals only later (`reveals`: beat -> fact ids)."""

    def __init__(self, investigation: set[str] | None = None, custody: set[str] | None = None,
                 reveals: dict[str, list[str] | set[str]] | None = None):
        self.investigation = set(investigation or ())
        self.custody = set(custody or ())
        self.reveals = {b: set(v) for b, v in (reveals or {}).items() if v}

    def blocks(self, beat: str | None, a) -> bool:
        return self.why(beat, a) is not None

    def why(self, beat: str | None, a) -> str | None:
        if a is None or beat is None:
            return None
        if beat in self.investigation and shows_investigation(a):
            return "shows the investigation before the incident"
        if beat in self.custody and shows_custody(a):
            return "shows custody/court before the story reaches the arrest"
        early = self.reveals.get(beat, set()) & revealed_by(a)
        if early:
            return f"shows what the story reveals only later ({', '.join(sorted(early))})"
        return None

    def as_json(self) -> dict:
        return {"investigation": sorted(self.investigation), "custody": sorted(self.custody),
                "reveals": {b: sorted(v) for b, v in sorted(self.reveals.items())}}

    @classmethod
    def from_json(cls, data: dict | None) -> "Firewall":
        data = data or {}
        return cls(set(data.get("investigation") or ()), set(data.get("custody") or ()),
                   data.get("reveals") or {})
