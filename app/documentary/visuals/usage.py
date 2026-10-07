"""Repetition control (Master task §9.3): how often each picture has been
on screen in THIS film, and whether it may be shown again.

A film that shows the same generic street three times feels assembled
by a machine; a film that shows the victim's face whenever the narrator
speaks of her feels directed. So every path of the production script
that puts a picture on screen — the director's shots, black-screen fills,
long-hold sequences, swaps of pictures that became unusable, critic
replacements — asks one UsageTracker.

Categories and limits (config visual_direction):
  map       — once per PLACE (all zoom levels of a place are one map);
  generic   — tier >= 4 and not a person: max_generic_appearances;
  person    — entity type person, tier <= 2: max_person_appearances,
              at least min_repeat_gap_seconds between two appearances;
  context   — other contextual pictures (role "context", or a tier >= 4
              picture of a person): max_context_appearances;
  evidence  — exact case material, people/places/objects of the case
              (tier <= 3): max_evidence_appearances.

Reuse rules: an UNUSED alternative always comes first (lowest tier
first). A reuse is allowed only when it is justified — the person or
thing in the picture is named in the sentence being spoken, or nothing
else exists for that moment — and stays within the category limit. A
shot that continues the previous one (same picture, no gap: a reframe,
a held picture) is the same appearance, not a reuse.

Every appearance is written to MediaUsage (record_media_usage) with its
reason, its number in the film and, for a reuse, its justification.
"""

from __future__ import annotations

import json
import math
from typing import Callable, Iterable

from app.core.ai_config import ai_config
from app.documentary.visuals import tiers as T

MAP, GENERIC, PERSON, CONTEXT, EVIDENCE = "map", "generic", "person", "context", "evidence"
# A shot starting within this time of the end of the previous shot of the
# same picture continues that appearance.
CONTINUE_GAP_SECONDS = 0.05
# Words stored per MediaUsage row (what was said while the shot ran).
MAX_SENTENCE_CHARS = 1000

PICTURE_KINDS = ("image", "video", "document", "map")


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _place_key(place: str | None) -> str:
    return " ".join(str(place or "").lower().replace(",", " ").split())


def asset_tier(a) -> int:
    """The relevance tier of an asset (an asset found before tiers
    existed gets the provisional tier of how it was found)."""
    tier = getattr(a, "relevance_tier", None)
    if tier:
        return int(tier)
    return T.provisional_tier(getattr(a, "provider", None), getattr(a, "asset_role", None),
                              getattr(a, "entity_type", None))


def asset_facts(a) -> dict:
    """What repetition control needs to know of an asset. The same facts
    travel on every shot of a production script, so a script can be
    checked (critics) without the library."""
    ents: list[str] = []
    if getattr(a, "entity_key", None):
        ents.append(a.entity_key)
    ents += [e for e in _loads(getattr(a, "entities_json", None), []) if e not in ents]
    place = None
    if a.asset_type == "map":
        place = _loads(a.spec_json, {}).get("place") or a.location
    etype = getattr(a, "entity_type", None)
    if not etype and getattr(a, "subject_type", None) == "person":
        etype = "person"
    return {"asset_id": a.asset_code, "type": a.asset_type, "role": a.asset_role,
            "tier": asset_tier(a), "entity_type": etype, "entities": ents, "place": place}


def category(f: dict) -> str:
    if f.get("type") == "map":
        return MAP
    tier = int(f.get("tier") or 3)
    person = f.get("entity_type") == "person"
    if tier >= 4 and not person:
        return GENERIC
    if person and tier <= 2:
        return PERSON
    if f.get("role") == "context" or tier >= 4:
        return CONTEXT
    return EVIDENCE


def limit_of(cat: str) -> int:
    cfg = ai_config.visual_direction
    return {MAP: 1, GENERIC: cfg.max_generic_appearances, PERSON: cfg.max_person_appearances,
            CONTEXT: cfg.max_context_appearances,
            EVIDENCE: cfg.max_evidence_appearances}.get(cat, cfg.max_generic_appearances)


class UsageTracker:
    """Appearances per picture on the film's timeline (seconds). Map
    zoom levels of one place count as one picture."""

    def __init__(self, facts: dict[str, dict] | None = None):
        self.facts: dict[str, dict] = dict(facts or {})
        self.spans: dict[str, list[list[float]]] = {}

    # -- construction --------------------------------------------------------
    @classmethod
    def for_assets(cls, assets: dict) -> "UsageTracker":
        return cls({code: asset_facts(a) for code, a in assets.items()})

    @classmethod
    def from_shots(cls, shots: list[dict], facts: dict[str, dict] | None = None,
                   skip: Iterable[int] = ()) -> "UsageTracker":
        """A tracker that knows every picture of these shots (all of the
        film at once: a fill never takes a picture the film shows later)."""
        tr = cls(facts)
        skip = set(skip)
        for k, s in enumerate(shots):
            tr.learn(s)
            for alt in s.get("alternatives") or []:
                tr.learn(alt)
            if k not in skip and s.get("asset_id") and s.get("kind") in PICTURE_KINDS:
                tr.add(s["asset_id"], s["start"], s["end"])
        return tr

    def learn(self, info: dict) -> None:
        """Facts of a shot or alternative dict (when the library is not at hand)."""
        code = info.get("asset_id")
        if not code or code in self.facts:
            return
        kind = info.get("type") or {"map": "map", "video": "video",
                                     "document": "document"}.get(info.get("kind"), "photo")
        self.facts[code] = {
            "asset_id": code, "type": kind, "role": info.get("role"),
            "tier": info.get("tier"), "entity_type": info.get("entity_type"),
            "entities": list(info.get("entities") or []),
            "place": info.get("place") or (info.get("map_info") or {}).get("place")}

    # -- facts -----------------------------------------------------------------
    def fact(self, code: str) -> dict:
        return self.facts.get(code) or {"asset_id": code, "tier": 3}

    def key(self, code: str) -> str:
        f = self.fact(code)
        if f.get("type") == "map" and f.get("place"):
            return "map|" + _place_key(f["place"])
        return code

    def category(self, code: str) -> str:
        return category(self.fact(code))

    def limit(self, code: str) -> int:
        return limit_of(self.category(code))

    def tier(self, code: str) -> int:
        return int(self.fact(code).get("tier") or 3)

    def named(self, code: str, names: set[str] | Iterable[str]) -> list[str]:
        """The entities of this picture that the sentence names."""
        names = set(names or ())
        return [e for e in self.fact(code).get("entities") or [] if e in names]

    # -- timeline ------------------------------------------------------------
    def add(self, code: str, start: float, end: float) -> None:
        spans = self.spans.setdefault(self.key(code), [])
        for sp in spans:
            if start <= sp[1] + CONTINUE_GAP_SECONDS and end >= sp[0] - CONTINUE_GAP_SECONDS:
                sp[0], sp[1] = min(sp[0], start), max(sp[1], end)
                return
        spans.append([float(start), float(end)])
        spans.sort()

    def discard(self, code: str, start: float, end: float) -> None:
        """Forget [start, end) of this picture (a held picture that is
        turned into a sequence is on screen only for its lead)."""
        key = self.key(code)
        kept = []
        for a, b in self.spans.get(key, []):
            if b <= start or a >= end:
                kept.append([a, b])
                continue
            if a < start:
                kept.append([a, start])
            if b > end:
                kept.append([end, b])
        if kept:
            self.spans[key] = sorted(kept)
        else:
            self.spans.pop(key, None)

    def appearances(self, code: str) -> int:
        return len(self.spans.get(self.key(code), []))

    def seconds(self, code: str) -> float:
        return round(sum(b - a for a, b in self.spans.get(self.key(code), [])), 3)

    def last_shown(self, code: str, before: float | None = None) -> float | None:
        ends = [b for a, b in self.spans.get(self.key(code), [])
                if before is None or a < before]
        return max(ends) if ends else None

    def continues(self, code: str, start: float) -> bool:
        """A shot at `start` would continue an appearance of this picture."""
        return any(abs(b - start) <= CONTINUE_GAP_SECONDS or a <= start < b
                   for a, b in self.spans.get(self.key(code), []))

    def gap(self, code: str, start: float, end: float) -> float:
        """Seconds to the nearest other appearance (inf when none)."""
        best = math.inf
        for a, b in self.spans.get(self.key(code), []):
            if a <= end and b >= start:
                return 0.0
            best = min(best, start - b if b <= start else a - end)
        return best

    def allows(self, code: str, start: float, end: float) -> tuple[bool, str]:
        """Within the limits of its category (count, and the person gap)?"""
        n = self.appearances(code)
        if n == 0 or self.continues(code, start):
            return True, ""
        cat, lim = self.category(code), self.limit(code)
        if n + 1 > lim:
            return False, f"{cat} picture already shown {n}x (limit {lim})"
        if cat == PERSON:
            g = self.gap(code, start, end)
            need = ai_config.visual_direction.min_repeat_gap_seconds
            if g < need:
                return False, f"same face {g:.0f}s ago (min {need:.0f}s apart)"
        return True, ""

    def pick(self, pool: Iterable[str], start: float, end: float,
             names: set[str] | Iterable[str] = (), avoid: Iterable[str] = (),
             reserved: Iterable[str] = (), allow_repeat: bool = True,
             prefer: Callable[[str], int] | None = None) -> dict | None:
        """The picture to show at [start, end) from `pool` (codes in the
        caller's order of preference):
          1. an unused picture, lowest tier first — one the film does not
             plan to show elsewhere (`reserved`) before one it does;
          2. a reuse of a picture the sentence names (within its limits);
          3. a reuse because nothing else exists (within its limits).
        `avoid`: pictures just on screen (never the same twice in a row).
        Returns {"asset_id", "repeat", "repeat_justified", "repeat_reason",
        "reason"} or None (nothing may be shown: hold or black)."""
        names, avoid, reserved = set(names or ()), set(avoid or ()), set(reserved or ())
        order = {c: k for k, c in enumerate(dict.fromkeys(c for c in pool if c))}
        codes = [c for c in order if c not in avoid]

        def rank(c):
            return ((prefer(c) if prefer else 0), self.tier(c), order[c])

        unused = sorted((c for c in codes if self.appearances(c) == 0),
                        key=lambda c: (c in reserved, *rank(c)))
        if unused:
            c = unused[0]
            return {"asset_id": c, "repeat": False, "repeat_justified": None,
                    "repeat_reason": None,
                    "reason": f"unused picture (tier {self.tier(c)})"}
        if not allow_repeat:
            return None
        ok = [c for c in codes if self.allows(c, start, end)[0]]

        def recency(c):
            last = self.last_shown(c, start)
            return -math.inf if last is None else last

        named = sorted((c for c in ok if self.named(c, names)),
                       key=lambda c: (*rank(c), recency(c)))
        if named:
            c = named[0]
            who = ", ".join(self.named(c, names))
            return {"asset_id": c, "repeat": True, "repeat_justified": True,
                    "repeat_reason": f"named in the sentence being spoken ({who})",
                    "reason": f"returns: {who} is named again"}
        rest = sorted(ok, key=lambda c: (recency(c), *rank(c)))
        if rest:
            c = rest[0]
            return {"asset_id": c, "repeat": True, "repeat_justified": True,
                    "repeat_reason": "nothing else exists for this moment",
                    "reason": "reuse: no unused picture left for this moment"}
        return None


# ---------------------------------------------------------------------------
# annotation + persistence
# ---------------------------------------------------------------------------


def named_between(sentences: list[dict], start: float, end: float) -> set[str]:
    """Entities named by the sentences spoken during [start, end): the
    sentence running at `start` and every sentence beginning before `end`.
    sentences: [{"start", "end", "entities"}] on the film's timeline."""
    out: set[str] = set()
    for s in sentences or []:
        if s["start"] < end and s.get("end", s["start"]) > start - 0.05:
            out.update(s.get("entities") or [])
    return out


def default_reason(shot: dict) -> str:
    """Why a shot is on screen when the director gave no words for it."""
    if shot.get("kind") == "map":
        return f"map: {shot.get('place') or 'the place'} is introduced here"
    if shot.get("reframe") or shot.get("command") in ("CROP_EXISTING", "ZOOM_EXISTING"):
        return "the picture on screen is reframed (same appearance)"
    what = {"video": "footage", "document": "document"}.get(shot.get("kind"), "picture")
    return f"director: {what} planned for beat {shot.get('beat_id') or '?'}"


def annotate(shots: list[dict], tracker_facts: dict[str, dict],
             sentences: list[dict] | None = None) -> None:
    """Writes shot["usage"] for every picture shot, in film order:
    appearance number (consecutive shots of one picture are one
    appearance), tier, category, the reason it is on screen and — for a
    reuse — whether and why it was justified. A reuse whose path did not
    record a justification is justified only when the sentence names
    what the picture shows; otherwise it is marked unjustified (the
    critics replace it)."""
    tr = UsageTracker(tracker_facts)
    for s in shots:
        tr.learn(s)
    prev = None
    for s in sorted(shots, key=lambda x: x["start"]):
        code = s.get("asset_id")
        if not code or s.get("kind") not in PICTURE_KINDS:
            prev = None
            continue
        cont = prev is not None and tr.key(prev.get("asset_id") or "") == tr.key(code) \
            and abs(prev["end"] - s["start"]) <= 0.5
        if cont:
            appearance = tr.appearances(code)
            tr.add(code, min(s["start"], prev["end"]), s["end"])  # one appearance
        else:
            appearance = tr.appearances(code) + 1
            tr.add(code, s["start"], s["end"])
        usage = {"appearance": max(appearance, 1), "tier": tr.tier(code),
                 "category": tr.category(code),
                 "reason": s.get("fill_reason") or s.get("why") or default_reason(s),
                 "repeat_justified": None, "repeat_reason": None}
        if appearance > 1:
            if cont:
                usage.update(repeat_justified=(prev.get("usage") or {}).get("repeat_justified"),
                             repeat_reason=(prev.get("usage") or {}).get("repeat_reason"))
            elif s.get("repeat_reason"):
                usage.update(repeat_justified=bool(s.get("repeat_justified", True)),
                             repeat_reason=s["repeat_reason"])
            else:
                who = tr.named(code, named_between(sentences or [], s["start"], s["end"]))
                usage.update(repeat_justified=bool(who),
                             repeat_reason=(f"named in the sentence being spoken ({', '.join(who)})"
                                            if who else "repeat without justification"))
        s["usage"] = usage
        prev = s


def spoken_text(subtitles: list[dict], start: float, end: float) -> str | None:
    """What the narrator says while a shot is on screen (display text)."""
    text = " ".join(x["text"] for x in subtitles or []
                    if x["start"] < end and x["end"] > start)
    return text[:MAX_SENTENCE_CHARS] or None


def record_media_usage(db, row, script: dict, film_key: str | None = None) -> int:
    """MediaUsage rows of one production script (delete + insert, so a
    critic fix or a re-compose never leaves stale rows). Returns the
    number of rows written."""
    from app.db.models import MediaUsage, VisualAsset

    db.query(MediaUsage).filter(MediaUsage.production_script_id == row.id).delete(
        synchronize_session="fetch")
    shots = script.get("shots") or []
    codes = {s.get("asset_id") for s in shots if s.get("asset_id")}
    ids = {a.asset_code: a.id for a in db.query(VisualAsset).filter(
        VisualAsset.case_id == row.case_id, VisualAsset.asset_code.in_(codes or {""})).all()}
    film_key = film_key or script.get("film_key")
    subs = script.get("subtitles") or []
    n = 0
    for s in shots:
        aid = ids.get(s.get("asset_id"))
        if aid is None or s.get("kind") not in PICTURE_KINDS:
            continue
        u = s.get("usage") or {}
        db.add(MediaUsage(
            asset_id=aid, case_id=row.case_id, production_script_id=row.id,
            film_key=film_key, language=row.language, shot_index=s.get("index"),
            beat_id=s.get("beat_id"), start=s["start"], end=s["end"],
            seconds=round(s["end"] - s["start"], 3), kind=s.get("kind"),
            tier=u.get("tier") or s.get("tier"),
            sentence=spoken_text(subs, s["start"], s["end"]),
            reason=(u.get("reason") or s.get("why") or None),
            appearance=int(u.get("appearance") or 1),
            repeat_justified=u.get("repeat_justified"),
            repeat_reason=u.get("repeat_reason"),
        ))
        n += 1
    db.commit()
    return n
