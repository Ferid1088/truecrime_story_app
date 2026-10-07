"""Visual direction (Parts 24–32, Master task §9): from beats and the asset
library to shots — language-independent (shares of each beat; exact times
come from each language's real audio later).

The question for every narration sentence: "What should the viewer be
seeing while this sentence is being spoken?"

1. Matching (deterministic): candidates per requirement AND per entity a
   sentence names, scored by entity match, role fit, verification,
   quality and relevance tier (visual_direction.tier_weights) — after the
   hard filters: rights allowed for the render profile, not rejected,
   and the REVEAL FIREWALL (a visual that reveals evidence the viewer has
   not been told yet can never be chosen).
2. Visual director (role visual_director): per beat the attention mode
   and its shots, sentence by sentence: NEW_IMAGE, SHOW_CLIP (real
   footage), KEEP_CURRENT_IMAGE, CROP_EXISTING, ZOOM_EXISTING,
   SHOW_DOCUMENT, SHOW_MAP, SHOW_DATE, SHOW_QUOTE, BLACK_SCREEN,
   ATMOSPHERIC_BROLL, NO_VISUAL_CHANGE — or REQUEST_SEARCH when nothing
   fits a sentence (the production-time search, visuals/gaps.py, then
   looks for exactly that). The director sees each candidate's tier,
   kind, rights and how often the film already uses it, the entities
   each sentence names, the film's opening strategy and the case status.
   Beats are directed in order (chunk after chunk) so "already used"
   is true.
3. Validator: cognitive load, density (picture changes per beat and one
   per sentence), read items, evidence/illustration honesty, unknown
   assets, clips only for footage, maps by geography (never in the
   film's first seconds unless the opening is about the place, one map
   per place), search requests out of the shots, then the fallback
   hierarchy (an unused verified candidate before a card, a hold or
   black — never a map by default).
4. Motion director (deterministic, seeded): subtle moves, no repeated
   pattern, slower in emotional beats; footage plays as it is.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    AudioPlan, Case, EditorialBlueprint, StoryVersion, VisualAsset, VisualPlan,
)
from app.documentary.visuals import rights as R
from app.documentary.visuals.planner import fold, sentence_entities
from app.documentary.visuals.usage import asset_facts, asset_tier, category, limit_of
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

COMMANDS = ("NEW_IMAGE", "SHOW_CLIP", "KEEP_CURRENT_IMAGE", "CROP_EXISTING", "ZOOM_EXISTING",
            "SHOW_DOCUMENT", "SHOW_MAP", "SHOW_DATE", "SHOW_QUOTE", "BLACK_SCREEN",
            "ATMOSPHERIC_BROLL", "NO_VISUAL_CHANGE", "REQUEST_SEARCH")
READ_COMMANDS = {"SHOW_DOCUMENT", "SHOW_QUOTE"}
CONTINUE_COMMANDS = {"KEEP_CURRENT_IMAGE", "CROP_EXISTING", "ZOOM_EXISTING", "NO_VISUAL_CHANGE"}
ASSET_COMMANDS = {"NEW_IMAGE", "ATMOSPHERIC_BROLL", "SHOW_CLIP"}
# Commands that put something new on screen (density: at most one per
# sentence). SHOW_DATE only lays text over the current picture.
CHANGE_COMMANDS = {"NEW_IMAGE", "ATMOSPHERIC_BROLL", "SHOW_CLIP", "SHOW_MAP", "SHOW_DOCUMENT",
                   "SHOW_QUOTE", "BLACK_SCREEN", "CROP_EXISTING", "ZOOM_EXISTING"}
PHOTO_MOTIONS = ("SLOW_PUSH", "SLOW_PULL", "PAN_LEFT", "PAN_RIGHT", "CROP_FOCUS", "SUBTLE_2_5D")
CANDIDATES_PER_REQ = 4
# Beats starting within this many seconds are the film's opening (they
# follow the opening strategy).
OPENING_SECONDS = 40.0
# Beats per director call (directed in order: "already used" is exact).
DIRECT_CHUNK = 10

# What the opening strategy puts on screen first (prompt guidance) and
# which kind of picture the fallback prefers in the opening.
OPENING_VISUALS = {
    "critical_moment": "the place or object of the decisive moment; restrained, dark",
    "mysterious_statement": "the document or quote of the statement, or who made it",
    "victim_introduction": "the person at the centre (their portrait), then their world",
    "evidence_discovery": "the evidence, or exactly where it was found",
    "emergency_call": "darkness, the document/transcript of the call, or the place it came from",
    "important_location": "the place itself — its map may be the first picture",
    "contradiction": "the two sources or documents that disagree",
    "last_sighting": "the person, then the last place they were seen",
    "courtroom_outcome": "the court (building or courtroom), then the people",
    "unanswered_question": "the place or object the question is about",
    "timeline_anomaly": "the dates and documents, the place at that time",
    "previous_coverage": "the people and the place as the earlier film showed them",
}
OPENING_PREFERS = {
    "victim_introduction": {"person"}, "last_sighting": {"person", "place", "building"},
    "evidence_discovery": {"object", "document", "event"},
    "mysterious_statement": {"document", "person"}, "contradiction": {"document"},
    "emergency_call": {"document", "place", "building"},
    "important_location": {"place", "building"}, "courtroom_outcome": {"building", "organization"},
    "critical_moment": {"place", "building", "object"},
    "unanswered_question": {"place", "object"}, "timeline_anomaly": {"document", "place"},
    "previous_coverage": {"person", "place"},
}

DIRECTOR_SYSTEM = """
You are the picture editor of a high-end true-crime documentary built
from real photos, real footage, documents and maps. The narration is
final and carries the story. For EVERY narration sentence answer one
question: "What should the viewer be seeing while this sentence is
being spoken?" The pictures deepen the words; they never compete.

Each beat lists its narration sentences (n, text, names = the people,
places and things that sentence names) and its candidates: asset_id,
kind (photo|video|document), tier (1 exact case evidence or footage,
2 the exact person/place/object, 3 the exact city/building/area,
4 contextual imagery, 5 generic atmosphere), role (evidence|context|
illustration), rights, entity, shows, used (how often the film already
shows it), max_uses.

For each beat return attention scores (0–1: listen, look, read, orient,
feel) and the shots in narration order. Each shot: command,
from_sentence (the sentence at which it appears; the first shot starts
at 0; numbers increase), why (what the viewer sees and why now).
Commands:
  NEW_IMAGE (asset_id of a photo candidate of that beat)
  SHOW_CLIP (asset_id of a video candidate — real footage, plays muted)
  KEEP_CURRENT_IMAGE | CROP_EXISTING | ZOOM_EXISTING (stay on / reframe
    the current picture — often the most professional choice)
  SHOW_MAP (place: the beat's map_place or a place its sentences name)
  SHOW_DOCUMENT (the beat's document passage) | SHOW_DATE (the beat's
    date over the current picture) | SHOW_QUOTE (the beat's quote on a
    dark frame)
  BLACK_SCREEN (words alone: a short pause at hard or painful moments)
  ATMOSPHERIC_BROLL (an illustration/context candidate, mood only)
  NO_VISUAL_CHANGE
  REQUEST_SEARCH (from_sentence, entity, queries: 2–3 English image
    searches for exactly what that sentence shows, why) — when NOTHING
    listed fits the sentence. The production searches for it; meanwhile
    keep the current picture. Never fill the gap with a picture the film
    already showed.
Rules:
- Show what the sentence talks about: the person's picture when the
  sentence is about that person; the place when we arrive there; the
  evidence when it matters. Order the shots like the narration.
- Prefer the LOWEST tier: exact case material → the exact person, place
  or object → the exact city or building → contextual → generic only as
  the very last resort. A contextual or illustration picture never
  pretends to be case evidence (not "the house" unless it IS the house).
- Use real footage (SHOW_CLIP) where moving pictures help (places,
  events, searches) and it is relevant to the sentence. Only video
  candidates can be clips.
- Repetition: a generic or contextual picture is never shown twice
  (check used / max_uses). Prefer an unused candidate. A person or a
  piece of evidence may return when the sentence names them again.
- Maps only where geography matters, at the sentence that FIRST
  introduces a place; each place at most once per film; never the first
  picture of the film unless the opening strategy is important_location.
- The opening (beats marked "opening", the first 20–40 seconds) follows
  the film's opening strategy (see opening.guidance): victim_introduction
  → the person; evidence_discovery → the evidence; courtroom_outcome →
  the court; emergency_call → darkness, a document or the place;
  important_location → the place or its map; last_sighting → the person,
  then the place.
- Rhythm: while the story moves, a new picture roughly every 5–9
  seconds; at most max_shots picture changes in a beat and at most one
  per sentence; hold a picture longer only on purpose (a face, a
  document, an emotional moment).
- Never more than one thing to READ in a beat, and none while the
  narration is dense (information_density high) unless the beat is about
  that document or quote.
- BLACK_SCREEN is a short pause (one sentence), never a whole beat.
- Do not show the investigation (searches, police, rescue teams) before
  the story has told that something happened.
- case_status UNSOLVED: nothing may suggest a solution (no picture
  presented as the culprit).

Return JSON only:
{"beats": [{"beat_id": "B01", "attention": {"listen": 0.8, "look": 0.4,
  "read": 0.0, "orient": 0.3, "feel": 0.2},
  "shots": [{"command": "NEW_IMAGE", "asset_id": "VIS_000012",
             "from_sentence": 0, "why": "..."},
            {"command": "SHOW_CLIP", "asset_id": "VIS_000031",
             "from_sentence": 2, "why": "..."},
            {"command": "SHOW_MAP", "place": "...", "from_sentence": 3, "why": "..."},
            {"command": "REQUEST_SEARCH", "from_sentence": 5,
             "entity": "st_marys_church", "queries": ["..."], "why": "..."}]}]}
"""


# ---------------------------------------------------------------------------
# matching + reveal firewall
# ---------------------------------------------------------------------------


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (ValueError, TypeError):
        return default


def usable(asset: VisualAsset, profile: str | None = None) -> bool:
    return (asset.verification_status != "rejected"
            and asset.local_path is not None
            and R.allowed(asset.rights_status, profile))


def firewall_ok(asset: VisualAsset, blocked: set[str]) -> bool:
    """A visual may never show what the story reveals only later."""
    return not (set(_loads(asset.reveals_json, [])) & blocked)


def blocked_at(blueprint: dict, beat_id: str) -> set[str]:
    """Evidence the viewer must not see yet at this beat: what later
    reveal/contradiction/evidence/false-lead/chapter-end beats disclose
    (an orientation detail shown early is not a spoiler)."""
    purposes = set(ai_config.attention.firewall_purposes)
    beats = blueprint.get("beats") or []
    ids = [b["id"] for b in beats]
    if beat_id not in ids:
        return set()
    i = ids.index(beat_id)
    known = known_at(beats[i])
    later = {r for b in beats[i + 1:] if b.get("purpose") in purposes
             for r in b.get("reveals") or []}
    return later - known


def entity_score(asset: VisualAsset, entity: dict) -> float:
    """1.0 the asset was found/verified for this entity, 0.7 its text
    names it fully, 0.35 partly."""
    if entity["key"] == getattr(asset, "entity_key", None) or \
            entity["key"] in _loads(asset.entities_json, []):
        return 1.0
    text = " ".join(filter(None, [asset.title, asset.caption, asset.description])).lower()
    words = [w for w in re.findall(r"\w+", entity["name"].lower()) if len(w) > 2]
    if words and all(w in text for w in words):
        return 0.7
    if words and any(w in text for w in words):
        return 0.35
    return 0.0


_entity_score = entity_score  # older name


def tier_weight(asset: VisualAsset) -> float:
    return float(ai_config.visual_direction.tier_weights.get(str(asset_tier(asset)), 0.5))


def rank_candidates(req: dict, entity: dict, assets: list[VisualAsset],
                    blocked: set[str], profile: str | None = None) -> list[tuple[float, VisualAsset]]:
    scored = []
    for a in assets:
        if a.asset_type in ("map", "document", "card") and a.provider == "generated":
            continue
        if not usable(a, profile) or not firewall_ok(a, blocked):
            continue
        if a.asset_role not in req["acceptable_roles"]:
            continue
        es = entity_score(a, entity)
        if es <= 0:
            continue
        ver = {"verified": 1.0, "needs_review": 0.6}.get(a.verification_status, 0.35)
        q = a.quality_score if a.quality_score is not None else 0.5
        prio = {"high": 1.0, "medium": 0.85, "low": 0.7}.get(req.get("priority"), 0.85)
        score = es * (0.5 + 0.5 * ver) * (0.6 + 0.4 * q) * prio * tier_weight(a)
        scored.append((round(score, 4), a))
    scored.sort(key=lambda x: (-x[0], x[1].id or 0))
    return scored


def max_shots(beat: dict, sentences: int | None = None) -> int:
    """Picture changes a beat may have: beat_seconds / seconds_per_shot,
    clamped to 2..max_shots_per_beat — and never more than one per
    narration sentence."""
    vd = ai_config.visual_direction
    n = max(2, min(vd.max_shots_per_beat, round(beat_seconds(beat) / vd.seconds_per_shot)))
    return min(n, sentences) if sentences else n


def known_at(beat: dict) -> set[str]:
    return set(beat.get("viewer_knows") or []) | set(beat.get("reveals") or [])


def beat_seconds(beat: dict) -> float:
    return max((beat.get("words") or 0) / ai_config.documentary.wpm("en") * 60, 4.0)


def beat_starts(blueprint: dict) -> dict[str, float]:
    """Estimated start (seconds into the film) of every beat."""
    out, t = {}, 0.0
    for b in blueprint.get("beats") or []:
        out[b["id"]] = round(t, 2)
        t += beat_seconds(b)
    return out


def opening_beats(blueprint: dict, seconds: float = OPENING_SECONDS) -> set[str]:
    return {bid for bid, t in beat_starts(blueprint).items() if t < seconds}


def place_key(place: str | None) -> str:
    return " ".join(fold(place or "").replace(",", " ").split())


# ---------------------------------------------------------------------------
# validation + fallback hierarchy
# ---------------------------------------------------------------------------


def _fallback_shot(beat_req: dict, has_current: bool, options: list[VisualAsset] | None = None,
                   prefer: set[str] | None = None) -> dict:
    """Part 25 hierarchy when no fitting shot exists: an unused verified
    candidate (lowest tier; in the opening the kind its strategy wants),
    then the document or date card, then holding the current picture,
    then black. A map is never the default: maps are chosen where
    geography matters, not to fill."""
    if options:
        a = sorted(options, key=lambda x: ((x.entity_type or "") not in (prefer or set()),
                                           asset_tier(x), -(x.quality_score or 0.5),
                                           x.id or 0))[0]
        cmd = "SHOW_CLIP" if a.asset_type == "video" else (
            "ATMOSPHERIC_BROLL" if a.asset_role == "illustration" else "NEW_IMAGE")
        shot = {"command": cmd, "asset_id": a.asset_code, "share": 1.0,
                "why": f"fallback: unused verified candidate (tier {asset_tier(a)})"}
        if a.asset_role == "illustration":
            shot["label"] = "illustration"
        if a.asset_type == "video":
            shot.update(_clip_window({}, a))
        return shot
    if beat_req.get("document"):
        return {"command": "SHOW_DOCUMENT", "share": 1.0, "why": "fallback: document"}
    if beat_req.get("date_text"):
        return {"command": "SHOW_DATE", "share": 1.0, "why": "fallback: date card"}
    if has_current:
        return {"command": "KEEP_CURRENT_IMAGE", "share": 1.0, "why": "fallback: hold"}
    return {"command": "BLACK_SCREEN", "share": 1.0, "why": "fallback: black"}


def _clip_window(s: dict, a: VisualAsset) -> dict:
    """The part of a stored clip a shot plays: the director may start it
    later inside the stored window, never outside it."""
    lo = float(a.clip_start or 0.0)
    hi = a.clip_end if a.clip_end is not None else a.duration_seconds
    hi = float(hi) if hi is not None else None
    try:
        start = float(s.get("clip_start")) if s.get("clip_start") is not None else lo
    except (TypeError, ValueError):
        start = lo
    start = max(lo, start)
    if hi is not None:
        start = min(start, max(hi - 1.0, lo))
    return {"clip_start": round(start, 3), "clip_end": round(hi, 3) if hi is not None else None}


def sentence_marks(text: str) -> list[dict]:
    """Narration sentences of a beat with the fraction of the beat (by
    words) at which each one starts — language-independent anchors."""
    from app.documentary.voice_blocks import split_sentences

    sents = [x for para in (text or "").split("\n\n") if para.strip()
             for x in split_sentences(para, "en")]
    total = sum(len(x.split()) for x in sents) or 1
    out, acc = [], 0
    for i, x in enumerate(sents):
        out.append({"n": i, "at": round(acc / total, 4), "text": x})
        acc += len(x.split())
    return out


def _shares_from_sentences(shots: list[dict], marks: list[dict]) -> bool:
    """Turn from_sentence anchors into shares. False if unusable."""
    if not shots or not marks or any(not isinstance(s.get("from_sentence"), int) for s in shots):
        return False
    pos = [min(max(s["from_sentence"], 0), len(marks) - 1) for s in shots]
    pos[0] = 0
    if any(b < a for a, b in zip(pos, pos[1:])):
        return False
    at = [marks[p]["at"] for p in pos] + [1.0]
    for i, s in enumerate(shots):
        s["share"] = max(at[i + 1] - at[i], 0.0)
    return True


def _entity_key(value, entities: dict[str, dict]) -> str | None:
    """A planner entity key from a key or a name the model used."""
    v = str(value or "").strip()
    if not v:
        return None
    key = re.sub(r"[^a-z0-9_]+", "_", v.lower()).strip("_")
    if key in entities:
        return key
    f = fold(v)
    for k, e in entities.items():
        if f in [fold(x) for x in [e.get("name") or ""] + list(e.get("aliases") or [])]:
            return k
    return None


def _search_request(s: dict, bid: str, marks: list[dict], sent_ents: list[list[str]],
                    entities: dict[str, dict]) -> dict:
    n = s.get("from_sentence")
    n = int(n) if isinstance(n, (int, float)) else 0
    if marks:
        n = min(max(n, 0), len(marks) - 1)
    key = _entity_key(s.get("entity"), entities)
    if key is None and not s.get("entity") and sent_ents and n < len(sent_ents) and sent_ents[n]:
        key = sent_ents[n][0]
    queries = []
    for q in s.get("queries") or []:
        q = " ".join(str(q).split())[:120]
        if q and q.lower() not in {x.lower() for x in queries}:
            queries.append(q)
    return {"beat_id": bid, "from_sentence": n,
            "sentence": marks[n]["text"] if marks and n < len(marks) else None,
            "entity": key,
            "entity_name": (entities[key]["name"] if key else str(s.get("entity") or "")[:200]
                            or None),
            "queries": queries[:ai_config.visual_direction.queries_per_request],
            "why": str(s.get("why") or "")[:300] or "the director found nothing that fits",
            "source": "director"}


def _map_place(s: dict, req: dict, sent_ents: list[list[str]],
               entities: dict[str, dict]) -> str | None:
    """The place a SHOW_MAP shows: the beat's map_place, or a place or
    building one of the beat's sentences names (never an invented one)."""
    given = str(s.get("place") or s.get("map_place") or "").strip()
    beat_place = req.get("map_place")
    if not given:
        return beat_place
    if beat_place and place_key(given.split(",")[0]) == place_key(beat_place.split(",")[0]):
        return beat_place
    named = {k for ents in sent_ents for k in ents}
    for k in named:
        e = entities.get(k) or {}
        if e.get("type") not in ("place", "building"):
            continue
        names = [e.get("name") or ""] + list(e.get("aliases") or [])
        if place_key(given.split(",")[0]) in {place_key(x.split(",")[0]) for x in names}:
            return e["name"]
    return beat_place


def _relevance(shot: dict, sent_ents: list[list[str]],
               assets_by_code: dict[str, VisualAsset]) -> tuple:
    """How much a shot is about its sentence: shows an entity that
    sentence names, shows a person, its tier — then maps, documents and
    quotes (deliberate), then holds."""
    a = assets_by_code.get(shot.get("asset_id") or "")
    if a is not None:
        facts = asset_facts(a)
        n = shot.get("from_sentence")
        named = bool(isinstance(n, int) and 0 <= n < len(sent_ents)
                     and set(facts["entities"]) & set(sent_ents[n]))
        return (int(named), int(facts["entity_type"] == "person"), -facts["tier"])
    if shot["command"] in ("SHOW_MAP", "SHOW_DOCUMENT", "SHOW_QUOTE"):
        return (1, 0, -3)
    return (0, 0, -9)


def validate_visual_plan(raw: dict, blueprint: dict, requirements: dict,
                         candidates: dict[str, list[str]],
                         assets_by_code: dict[str, VisualAsset],
                         marks: dict[str, list[dict]] | None = None, *,
                         opening: dict | str | None = None,
                         keep: dict[str, dict] | None = None) -> tuple[dict, dict]:
    """Deterministic rules over the director's raw plan.

    opening: the film's opening strategy ({"strategy"} or the name) —
      only "important_location" may show a map first / in the first
      visual_direction.first_map_not_before_seconds.
    keep: already validated beats (beat_id -> plan beat) that stay as they
      are (a re-direction of some beats); they still count for the
      film-level rules (maps per place, pictures already used)."""
    att = ai_config.attention
    motion = ai_config.motion
    vd = ai_config.visual_direction
    strategy = opening.get("strategy") if isinstance(opening, dict) else opening
    map_first_ok = strategy == "important_location"
    prefer = OPENING_PREFERS.get(strategy or "", set())
    given = {str(b.get("beat_id")): b for b in (raw or {}).get("beats") or []
             if isinstance(b, dict)}
    reqs = {b["beat_id"]: b for b in requirements.get("beats") or []}
    ents = {e["key"]: e for e in requirements.get("entities") or []}
    ent_list = list(ents.values())
    warnings, adjustments, search_requests = [], [], []
    out_beats = []
    has_current = False
    shown_picture = False
    last_asset = None
    mapped: set[str] = set()
    # maps the opening could not show (too early / before any picture):
    # they return at the first beat where geography may be shown
    deferred: dict[str, str] = {}
    used: Counter = Counter()
    starts = beat_starts(blueprint)
    openers = opening_beats(blueprint)

    def note_shot(s: dict, req: dict):
        nonlocal has_current, shown_picture, last_asset
        cmd = s["command"]
        if s.get("asset_id") and cmd in ASSET_COMMANDS:
            used[s["asset_id"]] += 1
            last_asset = s["asset_id"]
        if cmd == "SHOW_MAP":
            mapped.add(place_key(s.get("map_place") or req.get("map_place")))
        if cmd in ("NEW_IMAGE", "ATMOSPHERIC_BROLL", "SHOW_CLIP", "SHOW_MAP", "SHOW_DOCUMENT"):
            has_current = shown_picture = True
        if cmd in ("SHOW_QUOTE", "BLACK_SCREEN"):
            has_current = False

    for beat in blueprint.get("beats") or []:
        bid = beat["id"]
        req = reqs.get(bid, {})
        bmarks = (marks or {}).get(bid) or []
        sent_ents = [sentence_entities(m["text"], ent_list) for m in bmarks]
        if keep and bid in keep:
            for s in keep[bid].get("shots") or []:
                note_shot(s, req)
            out_beats.append(keep[bid])
            continue
        g = given.get(bid) or {}
        secs = beat_seconds(beat)
        beat_start = starts.get(bid, 0.0)
        blocked = blocked_at(blueprint, bid)
        dense = beat.get("information_density") == "high"
        about_document = beat.get("purpose") in ("evidence", "reveal") or \
            beat.get("attention") == "read"
        limit = max_shots(beat, len(bmarks) or None)
        shots, reads, changes = [], 0, 0
        for s in g.get("shots") or []:
            if not isinstance(s, dict):
                continue
            cmd = str(s.get("command") or "").upper()
            if cmd not in COMMANDS:
                warnings.append({"code": "unknown_command", "beat": bid, "value": cmd})
                continue
            if cmd == "REQUEST_SEARCH":
                search_requests.append(_search_request(s, bid, bmarks, sent_ents, ents))
                continue
            shot = {"command": cmd, "why": str(s.get("why") or "")[:200]}
            try:
                shot["share"] = max(float(s.get("share") or 0), 0.0)
            except (TypeError, ValueError):
                shot["share"] = 0.0
            if isinstance(s.get("from_sentence"), (int, float)):
                shot["from_sentence"] = int(s["from_sentence"])
            if cmd in ASSET_COMMANDS:
                code = str(s.get("asset_id") or "")
                a = assets_by_code.get(code)
                if code not in candidates.get(bid, []) or a is None:
                    warnings.append({"code": "asset_not_candidate", "beat": bid, "asset": code})
                    continue
                if not firewall_ok(a, blocked):
                    warnings.append({"code": "reveal_firewall", "beat": bid, "asset": code})
                    continue
                if a.asset_type == "video":
                    if cmd != "SHOW_CLIP":
                        adjustments.append({"beat": bid, "asset": code, "to": "SHOW_CLIP",
                                            "reason": "footage plays as a clip"})
                    cmd = "SHOW_CLIP"
                elif cmd == "SHOW_CLIP":
                    warnings.append({"code": "clip_not_video", "beat": bid, "asset": code})
                    cmd = "NEW_IMAGE"
                if cmd == "NEW_IMAGE" and a.asset_role == "illustration":
                    cmd = "ATMOSPHERIC_BROLL"
                shot["command"] = cmd
                shot["asset_id"] = code
                if a.asset_role == "illustration":
                    shot["label"] = "illustration"
                if cmd == "SHOW_CLIP":
                    shot.update(_clip_window(s, a))
            elif cmd == "SHOW_MAP":
                place = _map_place(s, req, sent_ents, ents)
                if not place:
                    warnings.append({"code": "no_map_place", "beat": bid})
                    continue
                shot["map_place"] = place
            elif cmd == "SHOW_DATE" and not req.get("date_text"):
                warnings.append({"code": "no_date", "beat": bid})
                continue
            elif cmd == "SHOW_QUOTE" and not req.get("quote"):
                warnings.append({"code": "no_quote", "beat": bid})
                continue
            elif cmd == "SHOW_DOCUMENT" and not req.get("document"):
                warnings.append({"code": "no_document", "beat": bid})
                continue
            elif cmd in CONTINUE_COMMANDS - {"NO_VISUAL_CHANGE"} and not (has_current or shots):
                warnings.append({"code": "nothing_to_keep", "beat": bid})
                continue
            if cmd in CHANGE_COMMANDS:
                fs = shot.get("from_sentence")
                if fs is not None and any(x.get("from_sentence") == fs for x in shots
                                          if x["command"] in CHANGE_COMMANDS):
                    adjustments.append({"beat": bid, "dropped": cmd,
                                        "reason": "one_shot_per_sentence"})
                    continue
                if changes >= limit:
                    adjustments.append({"beat": bid, "dropped": cmd, "reason": "density"})
                    continue
            if cmd in READ_COMMANDS:
                if reads >= att.max_read_items_per_beat or (dense and not about_document):
                    adjustments.append({"beat": bid, "dropped": cmd, "reason": "cognitive_load"})
                    continue
                reads += 1
            if cmd in CHANGE_COMMANDS:
                changes += 1
            shots.append(shot)
        # anchors -> shares; shares -> sum 1; each shot at least the minimum hold
        if shots:
            _shares_from_sentences(shots, bmarks)
            total = sum(s["share"] for s in shots) or float(len(shots))
            for s in shots:
                s["share"] = (s["share"] or total / len(shots)) / total
            while len(shots) > 1 and min(s["share"] for s in shots) * secs < motion.min_hold_seconds:
                i = min(range(len(shots)), key=lambda k: shots[k]["share"])
                j = i - 1 if i > 0 else 1
                # the more relevant of the two stays on screen for both
                # (a short sentence about the victim keeps her picture)
                if _relevance(shots[i], sent_ents, assets_by_code) > \
                        _relevance(shots[j], sent_ents, assets_by_code):
                    i, j = j, i
                shots[j]["share"] += shots[i]["share"]
                if i < j and "from_sentence" in shots[i]:
                    shots[j]["from_sentence"] = shots[i]["from_sentence"]
                adjustments.append({"beat": bid, "merged_short_shot": shots[i]["command"],
                                    "kept": shots[j].get("asset_id") or shots[j]["command"]})
                shots.pop(i)
        # the planner wants this place on a map (geography matters here)
        # and the director gave none: it is shown once — here, or at the
        # first beat where a map may be shown
        mp = req.get("map_place")
        if mp and place_key(mp) not in mapped and place_key(mp) not in deferred and not any(
                x["command"] == "SHOW_MAP" and place_key(x.get("map_place")) == place_key(mp)
                for x in shots):
            deferred[place_key(mp)] = mp
            adjustments.append({"beat": bid, "map_wanted": mp})
        # a map that could not be shown yet comes back here — at the
        # sentence naming the place, else at this beat's start
        if deferred and (map_first_ok or (
                shown_picture and beat_start >= vd.first_map_not_before_seconds)):
            for key, place in list(deferred.items()):
                deferred.pop(key)
                if key in mapped or any(x["command"] == "SHOW_MAP" and
                                        place_key(x.get("map_place")) == key for x in shots):
                    continue
                shots = _insert_deferred_map(shots, place, bmarks, secs, motion.min_hold_seconds,
                                             before=last_asset)
                adjustments.append({"beat": bid, "deferred_map": place})
        # maps by geography: never in the film's first seconds or as its
        # first picture (unless the opening is about the place), each
        # place once per film
        acc, seen_picture = 0.0, shown_picture
        for s in list(shots):
            start = beat_start + acc * secs
            acc += s["share"]
            if s["command"] != "SHOW_MAP":
                if s["command"] in ASSET_COMMANDS | {"SHOW_DOCUMENT"}:
                    seen_picture = True
                continue
            key = place_key(s["map_place"])
            reason = None
            if not map_first_ok and start < vd.first_map_not_before_seconds:
                reason = "map_too_early"
            elif not map_first_ok and not seen_picture:
                reason = "map_as_first_picture"
            elif key in mapped:
                reason = "place_already_mapped"
            if reason is None:
                mapped.add(key)
                seen_picture = True
                continue
            warnings.append({"code": reason, "beat": bid, "place": s["map_place"]})
            if reason in ("map_too_early", "map_as_first_picture"):
                deferred.setdefault(key, s["map_place"])
            k = shots.index(s)
            if len(shots) > 1:
                shots[k - 1 if k > 0 else 1]["share"] += s["share"]
            shots.pop(k)
        if not shots:
            options = [assets_by_code[c] for c in candidates.get(bid, [])
                       if c in assets_by_code and used[c] == 0
                       and assets_by_code[c].verification_status == "verified"
                       and firewall_ok(assets_by_code[c], blocked)]
            shots = [_fallback_shot(req, has_current or last_asset is not None, options,
                                    prefer if bid in openers else None)]
            adjustments.append({"beat": bid, "fallback": shots[0]["command"],
                                "why": shots[0]["why"]})
        for s in shots:
            s["share"] = round(s["share"], 4)
            if s.get("asset_id") and used[s["asset_id"]]:
                s["repeat"] = used[s["asset_id"]]  # earlier uses in this plan
            note_shot(s, req)
        attention = g.get("attention") if isinstance(g.get("attention"), dict) else {}
        out_beats.append({
            "beat_id": bid,
            "attention": {k: round(max(0.0, min(1.0, float(attention.get(k, 0) or 0))), 2)
                          for k in ("listen", "look", "read", "orient", "feel")},
            "est_seconds": round(secs, 1),
            "shots": shots,
            "sentences": [{"n": m["n"], "at": m["at"], "text": m["text"], "entities": e}
                          for m, e in zip(bmarks, sent_ents)],
        })
    report = {"status": "needs_review" if warnings else "valid",
              "warnings": warnings, "adjustments": adjustments,
              "shots": sum(len(b["shots"]) for b in out_beats),
              "fallbacks": sum(1 for a in adjustments if "fallback" in a),
              "search_requests": len(search_requests),
              "opening_strategy": strategy}
    return {"beats": out_beats, "search_requests": search_requests}, report


DEFERRED_MAP_SECONDS = 9.0


def _insert_deferred_map(shots: list[dict], place: str, bmarks: list[dict], secs: float,
                         min_hold: float, before: str | None = None) -> list[dict]:
    """Put a map of `place` into a beat: from the first sentence that
    names the place (else the beat's start), taking part of the shot
    running there — a short orientation (DEFERRED_MAP_SECONDS), never the
    rest of the beat. A following "keep / zoom / crop the current
    picture" would now act on the map: it returns to the picture that was
    on screen before (`before`). Shares stay normalized."""
    from app.lifecycle.identity import fold

    words = [w for w in fold(place.split(",")[0]).split() if len(w) >= 3]
    n = next((m["n"] for m in bmarks if words and all(w in fold(m["text"]) for w in words)), None)
    map_shot = {"command": "SHOW_MAP", "map_place": place,
                "why": f"where this happens: {place} (held back from the opening)"}
    if not shots:
        return [{**map_shot, "share": 1.0, "from_sentence": n or 0}]
    # the shot that is on screen at sentence n (or the first shot)
    idx = 0
    if n is not None:
        for i, sh in enumerate(shots):
            if (sh.get("from_sentence") or 0) <= n:
                idx = i
    host = shots[idx]
    if host["share"] * secs < 2 * min_hold:
        return shots          # no room for a map in this beat
    share = round(min(host["share"] / 2, max(DEFERRED_MAP_SECONDS, min_hold) / secs), 4)
    fs = n if n is not None and n > (host.get("from_sentence") or 0) else host.get("from_sentence")
    if host["command"] in CONTINUE_COMMANDS and idx == 0 and fs == host.get("from_sentence"):
        # the beat opens on the previous picture: the map comes first,
        # then the picture returns
        host["share"] = round(host["share"] - share, 4)
        shots.insert(0, {**map_shot, "share": share, "from_sentence": fs})
        after = 1
    else:
        rest = round(host["share"] / 2 - share, 4)
        host["share"] = round(host["share"] / 2, 4)
        shots.insert(idx + 1, {**map_shot, "share": share, "from_sentence": fs})
        after = idx + 2
        if rest > 0:
            back = {**host, "share": rest, "from_sentence": fs}
            if back["command"] in CONTINUE_COMMANDS:
                back = {**back, "command": "NEW_IMAGE" if before else "KEEP_CURRENT_IMAGE",
                        "asset_id": before}
            back["why"] = "back to the picture after the map"
            shots.insert(after, back)
            after += 1
    if after < len(shots) and shots[after]["command"] in CONTINUE_COMMANDS and before:
        nxt = shots[after]
        shots[after] = {**nxt, "command": "NEW_IMAGE", "asset_id": before,
                        "why": (nxt.get("why") or "") + " (back to the picture after the map)"}
    return shots


# ---------------------------------------------------------------------------
# motion director (deterministic)
# ---------------------------------------------------------------------------


def _seed(*parts) -> int:
    return int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def assign_motion(plan: dict, blueprint: dict, audio_plan: dict | None,
                  assets_by_code: dict[str, VisualAsset]) -> dict:
    cfg = ai_config.motion
    beats = {b["id"]: b for b in blueprint.get("beats") or []}
    after = {pb["beat_id"]: (pb.get("after") or {}).get("type")
             for pb in (audio_plan or {}).get("beats") or []}
    recent: list[str] = []
    prev_after = None
    for pb in plan["beats"]:
        beat = beats.get(pb["beat_id"], {})
        emotional = beat.get("emotional_load") == "high" or beat.get("attention") == "feel"
        for i, s in enumerate(pb["shots"]):
            cmd = s["command"]
            if i == 0 and prev_after in ("chapter_break", "silence"):
                s["transition_in"] = "FADE_BLACK"
            elif cmd in CONTINUE_COMMANDS:
                s["transition_in"] = "NONE"
            else:
                s["transition_in"] = "CROSSFADE"
            if cmd == "SHOW_MAP":
                s["motion"] = "MAP_ZOOM"
            elif cmd == "SHOW_DOCUMENT":
                s["motion"] = "DOCUMENT_HIGHLIGHT"
            elif cmd in ("BLACK_SCREEN", "SHOW_QUOTE", "NO_VISUAL_CHANGE", "SHOW_CLIP"):
                s["motion"] = "NONE"  # footage moves by itself: no Ken Burns
            elif cmd in ("KEEP_CURRENT_IMAGE",):
                s["motion"] = "CONTINUE"
            elif cmd in ("CROP_EXISTING", "ZOOM_EXISTING"):
                s["motion"] = "CROP_FOCUS"
            elif cmd == "SHOW_DATE":
                s["motion"] = "CONTINUE"
            else:
                a = assets_by_code.get(s.get("asset_id") or "")
                options = list(PHOTO_MOTIONS)
                if not (a and a.verification_json and '"subject_type": "person"' in a.verification_json):
                    options.remove("CROP_FOCUS")
                if not cfg.parallax_enabled or (a and a.subject_type == "person"):
                    options.remove("SUBTLE_2_5D")
                if emotional:
                    options = [m for m in options if m in ("SLOW_PUSH", "CROP_FOCUS")] or options
                run = recent[-cfg.max_same_motion_run:]
                if len(run) == cfg.max_same_motion_run and len(set(run)) == 1:
                    options = [m for m in options if m != run[0]] or options
                s["motion"] = options[_seed(pb["beat_id"], i, s.get("asset_id")) % len(options)]
            s["speed"] = round(cfg.emotional_slowdown if emotional else 1.0, 2)
            if s["motion"] not in ("CONTINUE", "NONE"):
                recent.append(s["motion"])
        prev_after = after.get(pb["beat_id"])
    return plan


# ---------------------------------------------------------------------------
# director agent
# ---------------------------------------------------------------------------


def _candidate_view(sc: float, a: VisualAsset, used: Counter) -> dict:
    facts = asset_facts(a)
    view = {"asset_id": a.asset_code, "kind": a.asset_type, "type": a.asset_type,
            "role": a.asset_role, "tier": facts["tier"], "rights": a.rights_status,
            "entity": (facts["entities"] or [None])[0],
            "shows": (a.description or a.caption or a.title or "")[:220],
            "verified": a.verification_status, "quality": a.quality_score,
            "score": sc, "used": int(used.get(a.asset_code, 0)),
            "max_uses": limit_of(category(facts))}
    if a.asset_type == "video":
        lo = a.clip_start or 0.0
        hi = a.clip_end if a.clip_end is not None else a.duration_seconds
        view["seconds"] = round((hi or 0) - lo, 1) if hi else None
    return view


def _beat_view(beat: dict, req: dict, cands: list[tuple[float, VisualAsset]],
               after: str | None, marks: list[dict] | None = None,
               entities: dict[str, dict] | None = None, used: Counter | None = None,
               opening: dict | None = None, searched: list[dict] | None = None) -> dict:
    entities = entities or {}
    ent_list = list(entities.values())
    narration = []
    for m in marks or []:
        keys = sentence_entities(m["text"], ent_list)
        narration.append({"n": m["n"], "text": m["text"],
                          "names": [entities[k]["name"] for k in keys]})
    view = {
        "narration": narration,
        "beat_id": beat["id"], "purpose": beat.get("purpose"),
        "summary": beat.get("summary"), "human_focus": beat.get("human_focus"),
        "attention_hint": beat.get("attention"), "visual_intent": beat.get("visual_intent"),
        "information_density": beat.get("information_density"),
        "emotional_load": beat.get("emotional_load"),
        "seconds": round(beat_seconds(beat)),
        "max_shots": max_shots(beat, len(marks or []) or None), "after": after,
        "map_place": req.get("map_place"), "date_text": req.get("date_text"),
        "quote": (req.get("quote") or {}).get("text"),
        "document": bool(req.get("document")),
        "candidates": [_candidate_view(sc, a, used or Counter()) for sc, a in cands],
    }
    if opening:
        view["opening"] = opening
    if searched:
        view["searched"] = searched
    return view


def beat_candidates(db: Session, case: Case, blueprint: dict, requirements: dict,
                    profile: str | None = None, marks: dict[str, list[dict]] | None = None
                    ) -> dict[str, list[tuple[float, VisualAsset]]]:
    """Candidates per beat: for each requirement AND for each entity a
    sentence of the beat names (the narration may name a church the
    planner did not list for that beat). The best footage of an entity
    is always offered next to its photos."""
    assets = db.query(VisualAsset).filter(VisualAsset.case_id == case.id).all()
    ents = {e["key"]: e for e in requirements.get("entities") or []}
    ent_list = list(ents.values())
    reqs = {b["beat_id"]: b for b in requirements.get("beats") or []}
    out = {}
    for beat in blueprint.get("beats") or []:
        bid = beat["id"]
        blocked = blocked_at(blueprint, bid)
        wanted = list(reqs.get(bid, {}).get("requirements", []))
        have = {r["entity"] for r in wanted}
        for m in (marks or {}).get(bid) or []:
            for key in sentence_entities(m["text"], ent_list):
                if key not in have:
                    have.add(key)
                    wanted.append({"entity": key, "purpose": "orientation", "priority": "medium",
                                   "acceptable_roles": ["evidence", "context"]})
        seen, picked = set(), []
        for r in wanted:
            ent = ents.get(r["entity"])
            if not ent:
                continue
            ranked = rank_candidates(r, ent, assets, blocked, profile)
            top = ranked[:CANDIDATES_PER_REQ]
            if not any(a.asset_type == "video" for _, a in top):
                top += [x for x in ranked if x[1].asset_type == "video"][:1]
            for sc, a in top:
                if a.id not in seen:
                    seen.add(a.id)
                    picked.append((sc, a))
        out[bid] = picked
    return out


def beat_marks(db: Session, blueprint_row: EditorialBlueprint, blueprint: dict
               ) -> dict[str, list[dict]]:
    """Sentence anchors of every beat from the master story."""
    master = db.get(StoryVersion, blueprint_row.story_version_id)
    if master is None:
        return {}
    from app.documentary.blueprint import version_sections
    from app.documentary.spoken import beat_sections

    try:
        return {bs["id"]: sentence_marks(bs["text"])
                for bs in beat_sections(version_sections(master), blueprint)}
    except (KeyError, IndexError, TypeError):
        return {}


def fill_candidates(cands: dict[str, list[tuple[float, VisualAsset]]]) -> dict[str, list[str]]:
    """What else could fill each beat (fills, swaps and critic fixes pick
    from these): checked, case-specific enough, never an illustration."""
    return {bid: [a.asset_code for sc, a in lst
                  if sc >= 0.5 and a.verification_status == "verified"
                  and a.asset_role != "illustration"]
            for bid, lst in cands.items()}


def ensure_found_used(raw_beats: list[dict], searched: dict[str, list[dict]],
                      candidates: dict[str, list[str]], assets: dict[str, VisualAsset],
                      limits: dict[str, int]) -> list[dict]:
    """After a production search the sentence it was made for shows what
    was found — unless the director chose a map, a document or a quote
    there on purpose. The found picture replaces whatever starts at that
    sentence (a hold, a repeat); without such a shot it is added, or it
    replaces the shot on screen at that sentence when the beat already
    has all the picture changes it may have."""
    for rb in raw_beats:
        bid = str(rb.get("beat_id"))
        shots = [s for s in rb.get("shots") or [] if isinstance(s, dict)]
        for note in searched.get(bid) or []:
            found = [c for c in note.get("found") or [] if c in candidates.get(bid, [])
                     and c in assets]
            if not found or any(s.get("asset_id") in found for s in shots):
                continue
            n = int(note.get("from_sentence") or 0)
            code = found[0]
            a = assets[code]
            new = {"command": "SHOW_CLIP" if a.asset_type == "video" else "NEW_IMAGE",
                   "asset_id": code, "from_sentence": n,
                   "why": (f"production search for {note.get('entity_name') or note.get('entity')}: "
                           "found for this sentence")}
            shots = [s for s in shots if not (s.get("command") == "REQUEST_SEARCH"
                                              and s.get("from_sentence") == n)]
            at = next((k for k, s in enumerate(shots) if s.get("from_sentence") == n
                       and str(s.get("command") or "").upper() in CHANGE_COMMANDS
                       | CONTINUE_COMMANDS), None)
            if at is not None:
                if str(shots[at].get("command") or "").upper() in (
                        "SHOW_MAP", "SHOW_DOCUMENT", "SHOW_QUOTE"):
                    continue
                shots[at] = new
            else:
                changes = [k for k, s in enumerate(shots)
                           if str(s.get("command") or "").upper() in CHANGE_COMMANDS]
                if len(changes) >= limits.get(bid, 2):
                    on_screen = [k for k in changes
                                 if (shots[k].get("from_sentence") or 0) <= n]
                    k = on_screen[-1] if on_screen else changes[0]
                    if str(shots[k].get("command") or "").upper() in (
                            "SHOW_MAP", "SHOW_DOCUMENT", "SHOW_QUOTE"):
                        continue
                    shots[k] = {**new, "from_sentence": shots[k].get("from_sentence", n)}
                else:
                    pos = next((k for k, s in enumerate(shots)
                                if (s.get("from_sentence") or 0) > n), len(shots))
                    shots.insert(pos, new)
            rb["shots"] = shots
    return raw_beats


class VisualDirector:
    def __init__(self):
        self.gen = get_generation_provider()

    def candidates(self, db: Session, case: Case, blueprint: dict, requirements: dict,
                   profile: str | None = None, marks: dict | None = None
                   ) -> dict[str, list[tuple[float, VisualAsset]]]:
        return beat_candidates(db, case, blueprint, requirements, profile, marks)

    def _context(self, db: Session, case: Case, plan_row: VisualPlan,
                 blueprint_row: EditorialBlueprint, audio_plan: AudioPlan | None,
                 profile: str | None) -> dict:
        from app.documentary.openings import opening_of_blueprint

        blueprint = json.loads(blueprint_row.blueprint_json or "{}")
        requirements = json.loads(plan_row.requirements_json or "{}")
        ap = json.loads(audio_plan.plan_json) if audio_plan else {}
        marks = beat_marks(db, blueprint_row, blueprint)
        opening = opening_of_blueprint(db, blueprint_row)
        strategy = opening.get("strategy")
        assets = db.query(VisualAsset).filter(VisualAsset.case_id == case.id).all()
        return {
            "assets": {a.asset_code: a for a in assets},
            "blueprint": blueprint, "requirements": requirements, "ap": ap,
            "after": {pb["beat_id"]: (pb.get("after") or {}).get("type")
                      for pb in ap.get("beats") or []},
            "marks": marks,
            "cands": self.candidates(db, case, blueprint, requirements, profile, marks),
            "reqs": {b["beat_id"]: b for b in requirements.get("beats") or []},
            "entities": {e["key"]: e for e in requirements.get("entities") or []},
            "opening": {"strategy": strategy, "reason": opening.get("reason"),
                        "guidance": OPENING_VISUALS.get(strategy or "")},
            "openers": opening_beats(blueprint),
            "case_status": getattr(case, "resolution_status", None) or "UNKNOWN",
        }

    async def _direct(self, db: Session, case: Case, ctx: dict, part: list[dict],
                      prev: dict | None, used: Counter, mapped: list[str],
                      searched: dict[str, list[dict]] | None = None) -> tuple[list[dict], str | None]:
        payload = {
            "case": case.canonical_title,
            "case_status": ctx["case_status"],
            "opening": ctx["opening"],
            "places_already_mapped": mapped,
            "previous_beat": ({"beat_id": prev["id"], "summary": prev.get("summary"),
                               "visual_intent": prev.get("visual_intent")} if prev else None),
            "beats": [_beat_view(b, ctx["reqs"].get(b["id"], {}), ctx["cands"].get(b["id"], []),
                                 ctx["after"].get(b["id"]), ctx["marks"].get(b["id"]),
                                 ctx["entities"], used,
                                 ctx["opening"] if b["id"] in ctx["openers"] else None,
                                 (searched or {}).get(b["id"]))
                      for b in part],
        }
        with track_run(db, case.id, "Visual Director",
                       input_summary=f"beats {part[0]['id']}–{part[-1]['id']}") as run:
            raw, res = await self.gen.generate_structured(
                "visual_director", DIRECTOR_SYSTEM, json.dumps(payload, ensure_ascii=False))
            stamp_run(run, res, "visual_director")
        beats = [b for b in (raw or {}).get("beats") or [] if isinstance(b, dict)]
        return beats, getattr(res, "model", None)

    @staticmethod
    def _count(raw_beats: list[dict], reqs: dict, used: Counter, mapped: list[str]) -> None:
        """What the film already shows after these beats (for the next chunk)."""
        for b in raw_beats:
            for s in b.get("shots") or []:
                if not isinstance(s, dict):
                    continue
                cmd = str(s.get("command") or "").upper()
                if cmd in ASSET_COMMANDS and s.get("asset_id"):
                    used[str(s["asset_id"])] += 1
                if cmd == "SHOW_MAP":
                    place = s.get("place") or reqs.get(str(b.get("beat_id")), {}).get("map_place")
                    if place and place not in mapped:
                        mapped.append(place)

    def _finish(self, ctx: dict, plan: dict, plan_row: VisualPlan, report: dict,
                key: str = "plan") -> None:
        from app.documentary.visuals.generated import attach_overlays

        plan = assign_motion(plan, ctx["blueprint"], ctx["ap"], ctx["assets"])
        plan = attach_overlays(plan, ctx["requirements"])
        plan["candidates"] = fill_candidates(ctx["cands"])
        plan["opening"] = {k: ctx["opening"].get(k) for k in ("strategy", "reason")}
        plan["case_status"] = ctx["case_status"]
        validation = _loads(plan_row.validation_json, {})
        validation[key] = report
        plan_row.plan_json = json.dumps(plan, ensure_ascii=False)
        plan_row.validation_json = json.dumps(validation, ensure_ascii=False)

    async def create(self, db: Session, case: Case, plan_row: VisualPlan,
                     blueprint_row: EditorialBlueprint, audio_plan: AudioPlan | None,
                     chunk: int = DIRECT_CHUNK, profile: str | None = None) -> VisualPlan:
        ctx = self._context(db, case, plan_row, blueprint_row, audio_plan, profile)
        beats = ctx["blueprint"].get("beats") or []
        used: Counter = Counter()
        mapped: list[str] = []
        raw_beats, model = [], None
        # in film order: each chunk knows what the film already shows
        for i in range(0, len(beats), max(chunk, 1)):
            part = beats[i:i + chunk]
            got, m = await self._direct(db, case, ctx, part, beats[i - 1] if i else None,
                                        used, mapped)
            model = m or model
            self._count(got, ctx["reqs"], used, mapped)
            raw_beats += got
        plan, report = validate_visual_plan(
            {"beats": raw_beats}, ctx["blueprint"], ctx["requirements"],
            {bid: [a.asset_code for _, a in lst] for bid, lst in ctx["cands"].items()},
            ctx["assets"], ctx["marks"], opening=ctx["opening"])
        self._finish(ctx, plan, plan_row, report)
        plan_row.status = "planned"
        plan_row.generation_model = model
        db.commit()
        return plan_row

    async def redirect(self, db: Session, case: Case, plan_row: VisualPlan,
                       blueprint_row: EditorialBlueprint, audio_plan: AudioPlan | None,
                       beat_ids: list[str], profile: str | None = None,
                       searched: dict[str, list[dict]] | None = None) -> dict:
        """Direct only `beat_ids` again with the library as it is now (after
        a production search); every other beat stays exactly as planned
        and still counts for the film-level rules. `searched`: per beat
        what was searched for which sentence and what was found."""
        ctx = self._context(db, case, plan_row, blueprint_row, audio_plan, profile)
        old = _loads(plan_row.plan_json, {})
        old_beats = {pb["beat_id"]: pb for pb in old.get("beats") or []}
        beats = [b for b in ctx["blueprint"].get("beats") or [] if b["id"] in set(beat_ids)]
        if not beats:
            return {"redirected": []}
        keep = {bid: pb for bid, pb in old_beats.items() if bid not in set(beat_ids)}
        used: Counter = Counter()
        mapped: list[str] = []
        self._count(list(keep.values()), ctx["reqs"], used, mapped)
        for pb in keep.values():  # validated shots name their map place
            for s in pb.get("shots") or []:
                if s.get("command") == "SHOW_MAP" and s.get("map_place") \
                        and s["map_place"] not in mapped:
                    mapped.append(s["map_place"])
        order = [b["id"] for b in ctx["blueprint"].get("beats") or []]
        prev = None
        first = order.index(beats[0]["id"])
        if first:
            prev = ctx["blueprint"]["beats"][first - 1]
        raw_beats, model = await self._direct(db, case, ctx, beats, prev, used, mapped, searched)
        assets_by_code = ctx["assets"]
        cand_codes = {bid: [a.asset_code for _, a in lst] for bid, lst in ctx["cands"].items()}
        limits = {b["id"]: max_shots(b, len(ctx["marks"].get(b["id"]) or []) or None)
                  for b in beats}
        raw_beats = ensure_found_used(raw_beats, searched or {}, cand_codes, assets_by_code,
                                      limits)
        # beats the model left out keep their earlier shots
        missing = {b["id"] for b in beats} - {str(r.get("beat_id")) for r in raw_beats}
        for bid in missing:
            if bid in old_beats:
                keep[bid] = old_beats[bid]
        plan, report = validate_visual_plan(
            {"beats": raw_beats}, ctx["blueprint"], ctx["requirements"], cand_codes,
            assets_by_code, ctx["marks"], opening=ctx["opening"], keep=keep)
        redone = {b["id"] for b in beats} - missing
        plan["search_requests"] = [r for r in old.get("search_requests") or []
                                   if r.get("beat_id") not in redone] + plan["search_requests"]
        if old.get("texts"):
            plan["texts"] = old["texts"]
        self._finish(ctx, plan, plan_row, report, key="redirect")
        if model:
            plan_row.generation_model = model
        db.commit()
        return {"redirected": sorted(redone, key=order.index), "report": report}
