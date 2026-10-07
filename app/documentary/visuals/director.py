"""Visual direction (Parts 24–32): from beats and the asset library to
shots — language-independent (shares of each beat; exact times come from
each language's real audio later).

1. Matching (deterministic): candidates per requirement, scored by
   entity match, role fit, verification, quality — after the hard
   filters: rights allowed for the render profile, not rejected, and the
   REVEAL FIREWALL (a visual that reveals evidence the viewer has not
   been told yet can never be chosen).
2. Attention/visual director (role visual_director): per beat the
   attention mode and its shots (about one per 8–15 s of narration):
   NEW_IMAGE, KEEP_CURRENT_IMAGE, CROP_EXISTING, ZOOM_EXISTING,
   SHOW_DOCUMENT, SHOW_MAP, SHOW_DATE, SHOW_QUOTE, BLACK_SCREEN,
   ATMOSPHERIC_BROLL, NO_VISUAL_CHANGE.
3. Validator: cognitive load, minimum holds, read items, evidence/
   illustration honesty, unknown assets, then the fallback hierarchy.
4. Motion director (deterministic, seeded): subtle moves, no repeated
   pattern, slower in emotional beats.
"""

from __future__ import annotations

import hashlib
import json
import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    AudioPlan, Case, EditorialBlueprint, StoryVersion, VisualAsset, VisualPlan,
)
from app.documentary.visuals import rights as R
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

COMMANDS = ("NEW_IMAGE", "KEEP_CURRENT_IMAGE", "CROP_EXISTING", "ZOOM_EXISTING",
            "SHOW_DOCUMENT", "SHOW_MAP", "SHOW_DATE", "SHOW_QUOTE", "BLACK_SCREEN",
            "ATMOSPHERIC_BROLL", "NO_VISUAL_CHANGE")
READ_COMMANDS = {"SHOW_DOCUMENT", "SHOW_QUOTE"}
CONTINUE_COMMANDS = {"KEEP_CURRENT_IMAGE", "CROP_EXISTING", "ZOOM_EXISTING", "NO_VISUAL_CHANGE"}
PHOTO_MOTIONS = ("SLOW_PUSH", "SLOW_PULL", "PAN_LEFT", "PAN_RIGHT", "CROP_FOCUS", "SUBTLE_2_5D")
CANDIDATES_PER_REQ = 4

DIRECTOR_SYSTEM = """
You are the picture editor and attention director of a high-end
true-crime documentary built from real photos, documents and maps. The
narration is final and carries the story: "If the listener closes their
eyes, the documentary must still work. If they open them, the visuals
should deepen understanding, not compete for attention."

For each beat decide:
- attention: scores 0–1 for listen, look, read, orient, feel.
- shots: the shots that fill the beat in order (see max_shots), each with:
  command — NEW_IMAGE (asset_id from that beat's candidates) |
    KEEP_CURRENT_IMAGE (stay on the previous picture: often the most
    professional choice) | CROP_EXISTING / ZOOM_EXISTING (reframe the
    current picture) | SHOW_MAP (the beat's map_place) | SHOW_DOCUMENT
    (the beat's document passage) | SHOW_DATE (the beat's date, over the
    current picture) | SHOW_QUOTE (the beat's quote, on a dark frame) |
    BLACK_SCREEN (words alone; hard or painful moments) |
    ATMOSPHERIC_BROLL (an illustration/context candidate, mood only) |
    NO_VISUAL_CHANGE
  from_sentence — the number (n) of the narration sentence at which this
    picture appears; the first shot starts at 0, numbers increase
  why — short reason
Rules:
- Rhythm like a broadcast documentary: while the story moves, a new
  picture roughly every 8–15 seconds (never more than max_shots for the
  beat); hold one image longer only on purpose (an emotional moment, a
  document, a face). Change when the story moves (new person, place,
  time, object, document) — not on a fixed grid.
- Each beat lists its narration sentences (n, text). Follow them: show
  what is being talked about at that moment
  (when the narration describes the house, show the house; when it
  reads the note, show the note). Order the shots like the narration.
- SHOW_DATE puts the date over the current picture; place it where the
  narration says the date (often the first shot of the beat).
- Never more than one thing to READ in a beat, and none while the
  narration is dense (information_density high) unless the beat is about
  that document or quote.
- A person's photo only when that person is the subject. A context or
  illustration picture must never pretend to be case evidence (not "the
  house" unless it IS the house).
- Use only asset_ids listed for that beat. If nothing fits, prefer
  KEEP_CURRENT_IMAGE, a map, a date, or BLACK_SCREEN over a wrong image.
- BLACK_SCREEN is a short pause (a few seconds, one sentence), never a
  whole beat: follow it with a picture or KEEP_CURRENT_IMAGE.
- Do not show the investigation (searches, police, rescue teams) before
  the story has told that something happened: the opening shows the
  place and the people, not what is coming.
- Maps orient: use them when the story arrives somewhere new.

Return JSON only:
{"beats": [{"beat_id": "B01", "attention": {"listen": 0.8, "look": 0.4,
  "read": 0.0, "orient": 0.3, "feel": 0.2},
  "shots": [{"command": "NEW_IMAGE", "asset_id": "VIS_000012",
             "from_sentence": 0, "why": "..."},
            {"command": "SHOW_MAP", "from_sentence": 4, "why": "..."}]}]}
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


def _entity_score(asset: VisualAsset, entity: dict) -> float:
    if entity["key"] in _loads(asset.entities_json, []):
        return 1.0
    text = " ".join(filter(None, [asset.title, asset.caption, asset.description])).lower()
    words = [w for w in re.findall(r"\w+", entity["name"].lower()) if len(w) > 2]
    if words and all(w in text for w in words):
        return 0.7
    if words and any(w in text for w in words):
        return 0.35
    return 0.0


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
        es = _entity_score(a, entity)
        if es <= 0:
            continue
        ver = {"verified": 1.0, "needs_review": 0.6}.get(a.verification_status, 0.35)
        q = a.quality_score if a.quality_score is not None else 0.5
        prio = {"high": 1.0, "medium": 0.85, "low": 0.7}[req["priority"]]
        scored.append((round(es * (0.5 + 0.5 * ver) * (0.6 + 0.4 * q) * prio, 4), a))
    scored.sort(key=lambda x: (-x[0], x[1].id))
    return scored


def max_shots(beat: dict) -> int:
    """About one picture per 8–15 s; long beats may hold more shots."""
    return max(2, min(8, round(beat_seconds(beat) / 10)))


def known_at(beat: dict) -> set[str]:
    return set(beat.get("viewer_knows") or []) | set(beat.get("reveals") or [])


def beat_seconds(beat: dict) -> float:
    return max((beat.get("words") or 0) / ai_config.documentary.wpm("en") * 60, 4.0)


# ---------------------------------------------------------------------------
# validation + fallback hierarchy
# ---------------------------------------------------------------------------


def _fallback_shot(beat_req: dict, has_current: bool) -> dict:
    """Part 25 hierarchy when no fitting asset exists."""
    if beat_req.get("map_place"):
        return {"command": "SHOW_MAP", "share": 1.0, "why": "fallback: map"}
    if beat_req.get("date_text"):
        return {"command": "SHOW_DATE", "share": 1.0, "why": "fallback: date"}
    if has_current:
        return {"command": "KEEP_CURRENT_IMAGE", "share": 1.0, "why": "fallback: hold"}
    return {"command": "BLACK_SCREEN", "share": 1.0, "why": "fallback: black"}


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
    if not marks or any(not isinstance(s.get("from_sentence"), int) for s in shots):
        return False
    pos = [min(max(s["from_sentence"], 0), len(marks) - 1) for s in shots]
    pos[0] = 0
    if any(b < a for a, b in zip(pos, pos[1:])):
        return False
    at = [marks[p]["at"] for p in pos] + [1.0]
    for i, s in enumerate(shots):
        s["share"] = max(at[i + 1] - at[i], 0.0)
    return True


def validate_visual_plan(raw: dict, blueprint: dict, requirements: dict,
                         candidates: dict[str, list[str]],
                         assets_by_code: dict[str, VisualAsset],
                         marks: dict[str, list[dict]] | None = None) -> tuple[dict, dict]:
    att = ai_config.attention
    motion = ai_config.motion
    given = {str(b.get("beat_id")): b for b in (raw or {}).get("beats") or []
             if isinstance(b, dict)}
    reqs = {b["beat_id"]: b for b in requirements.get("beats") or []}
    warnings, adjustments = [], []
    out_beats = []
    has_current = False
    last_asset = None
    for beat in blueprint.get("beats") or []:
        bid = beat["id"]
        g = given.get(bid) or {}
        req = reqs.get(bid, {})
        secs = beat_seconds(beat)
        blocked = blocked_at(blueprint, bid)
        dense = beat.get("information_density") == "high"
        about_document = beat.get("purpose") in ("evidence", "reveal") or \
            beat.get("attention") == "read"
        shots, reads = [], 0
        for s in (g.get("shots") or [])[:max_shots(beat)]:
            if not isinstance(s, dict):
                continue
            cmd = str(s.get("command") or "").upper()
            if cmd not in COMMANDS:
                warnings.append({"code": "unknown_command", "beat": bid, "value": cmd})
                continue
            shot = {"command": cmd, "why": str(s.get("why") or "")[:200]}
            try:
                shot["share"] = max(float(s.get("share") or 0), 0.0)
            except (TypeError, ValueError):
                shot["share"] = 0.0
            if isinstance(s.get("from_sentence"), (int, float)):
                shot["from_sentence"] = int(s["from_sentence"])
            if cmd in ("NEW_IMAGE", "ATMOSPHERIC_BROLL"):
                code = str(s.get("asset_id") or "")
                a = assets_by_code.get(code)
                if code not in candidates.get(bid, []) or a is None:
                    warnings.append({"code": "asset_not_candidate", "beat": bid, "asset": code})
                    continue
                if not firewall_ok(a, blocked):
                    warnings.append({"code": "reveal_firewall", "beat": bid, "asset": code})
                    continue
                if cmd == "NEW_IMAGE" and a.asset_role == "illustration":
                    cmd = shot["command"] = "ATMOSPHERIC_BROLL"
                shot["asset_id"] = code
                if a.asset_role == "illustration":
                    shot["label"] = "illustration"
            elif cmd == "SHOW_MAP" and not req.get("map_place"):
                warnings.append({"code": "no_map_place", "beat": bid})
                continue
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
            if cmd in READ_COMMANDS:
                if reads >= att.max_read_items_per_beat or (dense and not about_document):
                    adjustments.append({"beat": bid, "dropped": cmd, "reason": "cognitive_load"})
                    continue
                reads += 1
            shots.append(shot)
        if not shots:
            shots = [_fallback_shot(req, has_current or last_asset is not None)]
            adjustments.append({"beat": bid, "fallback": shots[0]["command"]})
        # anchors -> shares; shares -> sum 1; each shot at least the minimum hold
        _shares_from_sentences(shots, (marks or {}).get(bid) or [])
        total = sum(s["share"] for s in shots) or float(len(shots))
        for s in shots:
            s["share"] = (s["share"] or total / len(shots)) / total
        while len(shots) > 1 and min(s["share"] for s in shots) * secs < motion.min_hold_seconds:
            i = min(range(len(shots)), key=lambda k: shots[k]["share"])
            j = i - 1 if i > 0 else 1
            shots[j]["share"] += shots[i]["share"]
            adjustments.append({"beat": bid, "merged_short_shot": shots[i]["command"]})
            shots.pop(i)
        for s in shots:
            s["share"] = round(s["share"], 4)
            if s.get("asset_id"):
                last_asset = s["asset_id"]
            if s["command"] in ("NEW_IMAGE", "ATMOSPHERIC_BROLL", "SHOW_MAP", "SHOW_DOCUMENT"):
                has_current = True
            if s["command"] in ("SHOW_QUOTE", "BLACK_SCREEN"):
                has_current = False
        attention = g.get("attention") if isinstance(g.get("attention"), dict) else {}
        out_beats.append({
            "beat_id": bid,
            "attention": {k: round(max(0.0, min(1.0, float(attention.get(k, 0) or 0))), 2)
                          for k in ("listen", "look", "read", "orient", "feel")},
            "est_seconds": round(secs, 1),
            "shots": shots,
        })
    report = {"status": "needs_review" if warnings else "valid",
              "warnings": warnings, "adjustments": adjustments,
              "shots": sum(len(b["shots"]) for b in out_beats),
              "fallbacks": sum(1 for a in adjustments if "fallback" in a)}
    return {"beats": out_beats}, report


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
            elif cmd in ("BLACK_SCREEN", "SHOW_QUOTE", "NO_VISUAL_CHANGE"):
                s["motion"] = "NONE"
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


def _beat_view(beat: dict, req: dict, cands: list[tuple[float, VisualAsset]],
               after: str | None, marks: list[dict] | None = None) -> dict:
    return {
        "narration": [{"n": m["n"], "text": m["text"]} for m in marks or []],
        "beat_id": beat["id"], "purpose": beat.get("purpose"),
        "summary": beat.get("summary"), "human_focus": beat.get("human_focus"),
        "attention_hint": beat.get("attention"), "visual_intent": beat.get("visual_intent"),
        "information_density": beat.get("information_density"),
        "emotional_load": beat.get("emotional_load"),
        "seconds": round(beat_seconds(beat)), "max_shots": max_shots(beat), "after": after,
        "map_place": req.get("map_place"), "date_text": req.get("date_text"),
        "quote": (req.get("quote") or {}).get("text"),
        "document": bool(req.get("document")),
        "candidates": [
            {"asset_id": a.asset_code, "type": a.asset_type, "role": a.asset_role,
             "shows": (a.description or a.caption or a.title or "")[:220],
             "verified": a.verification_status, "quality": a.quality_score,
             "score": sc}
            for sc, a in cands
        ],
    }


class VisualDirector:
    def __init__(self):
        self.gen = get_generation_provider()

    def candidates(self, db: Session, case: Case, blueprint: dict, requirements: dict,
                   profile: str | None = None) -> dict[str, list[tuple[float, VisualAsset]]]:
        assets = db.query(VisualAsset).filter(VisualAsset.case_id == case.id).all()
        ents = {e["key"]: e for e in requirements.get("entities") or []}
        reqs = {b["beat_id"]: b for b in requirements.get("beats") or []}
        out = {}
        for beat in blueprint.get("beats") or []:
            blocked = blocked_at(blueprint, beat["id"])
            seen, picked = set(), []
            for r in reqs.get(beat["id"], {}).get("requirements", []):
                ent = ents.get(r["entity"])
                if not ent:
                    continue
                for sc, a in rank_candidates(r, ent, assets, blocked, profile)[:CANDIDATES_PER_REQ]:
                    if a.id not in seen:
                        seen.add(a.id)
                        picked.append((sc, a))
            out[beat["id"]] = picked
        return out

    async def create(self, db: Session, case: Case, plan_row: VisualPlan,
                     blueprint_row: EditorialBlueprint, audio_plan: AudioPlan | None,
                     chunk: int = 10, profile: str | None = None) -> VisualPlan:
        blueprint = json.loads(blueprint_row.blueprint_json or "{}")
        requirements = json.loads(plan_row.requirements_json or "{}")
        ap = json.loads(audio_plan.plan_json) if audio_plan else {}
        after = {pb["beat_id"]: (pb.get("after") or {}).get("type") for pb in ap.get("beats") or []}
        cands = self.candidates(db, case, blueprint, requirements, profile)
        reqs = {b["beat_id"]: b for b in requirements.get("beats") or []}
        beats = blueprint.get("beats") or []
        marks: dict[str, list[dict]] = {}
        master = db.get(StoryVersion, blueprint_row.story_version_id)
        if master is not None:
            from app.documentary.blueprint import version_sections
            from app.documentary.spoken import beat_sections

            try:
                for bs in beat_sections(version_sections(master), blueprint):
                    marks[bs["id"]] = sentence_marks(bs["text"])
            except (KeyError, IndexError, TypeError):
                marks = {}
        async def direct(i: int):
            part = beats[i:i + chunk]
            prev = beats[i - 1] if i else None
            payload = {
                "case": case.canonical_title,
                # chunks run in parallel: continuity comes from the story
                # (the beat before), repetition is handled by the validator
                "previous_beat": ({"beat_id": prev["id"], "summary": prev.get("summary"),
                                   "visual_intent": prev.get("visual_intent")}
                                  if prev else None),
                "beats": [_beat_view(b, reqs.get(b["id"], {}), cands.get(b["id"], []),
                                     after.get(b["id"]), marks.get(b["id"])) for b in part],
            }
            with track_run(db, case.id, "Visual Director",
                           input_summary=f"beats {part[0]['id']}–{part[-1]['id']}") as run:
                raw, res = await self.gen.generate_structured(
                    "visual_director", DIRECTOR_SYSTEM, json.dumps(payload, ensure_ascii=False))
                stamp_run(run, res, "visual_director")
            return raw, getattr(res, "model", None)

        from app.core.concurrency import gather_limited

        results = await gather_limited(None, [direct(i) for i in range(0, len(beats), chunk)])
        raw_beats, model = [], None
        for raw, m in results:
            model = m or model
            raw_beats += [b for b in (raw or {}).get("beats") or [] if isinstance(b, dict)]
        assets_by_code = {a.asset_code: a for lst in cands.values() for _, a in lst}
        plan, report = validate_visual_plan(
            {"beats": raw_beats}, blueprint, requirements,
            {bid: [a.asset_code for _, a in lst] for bid, lst in cands.items()},
            assets_by_code, marks)
        plan = assign_motion(plan, blueprint, ap, assets_by_code)
        from app.documentary.visuals.generated import attach_overlays

        plan = attach_overlays(plan, requirements)
        # what else could fill each beat (critic fixes pick from these)
        plan["candidates"] = {
            bid: [a.asset_code for sc, a in lst
                  if sc >= 0.5 and a.verification_status == "verified"
                  and a.asset_role != "illustration"]
            for bid, lst in cands.items()}
        validation = _loads(plan_row.validation_json, {})
        validation["plan"] = report
        plan_row.plan_json = json.dumps(plan, ensure_ascii=False)
        plan_row.validation_json = json.dumps(validation, ensure_ascii=False)
        plan_row.status = "planned"
        plan_row.generation_model = model
        db.commit()
        return plan_row
