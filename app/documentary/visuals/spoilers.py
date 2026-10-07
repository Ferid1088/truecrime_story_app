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


class Firewall:
    """Which pictures may not be shown at which beat (see module doc)."""

    def __init__(self, investigation: set[str] | None = None, custody: set[str] | None = None):
        self.investigation = set(investigation or ())
        self.custody = set(custody or ())

    def blocks(self, beat: str | None, a) -> bool:
        if a is None or beat is None:
            return False
        return ((beat in self.investigation and shows_investigation(a))
                or (beat in self.custody and shows_custody(a)))

    def why(self, beat: str | None, a) -> str | None:
        if beat in self.investigation and shows_investigation(a):
            return "shows the investigation before the incident"
        if beat in self.custody and shows_custody(a):
            return "shows custody/court before the story reaches the arrest"
        return None
