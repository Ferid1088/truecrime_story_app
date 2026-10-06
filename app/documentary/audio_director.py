"""Audio director — breathing room, music moments and silence.

A professional sound designer / music supervisor (role audio_director)
reads the editorial blueprint and decides, beat by beat, how the film
should breathe:

* the pause between paragraphs inside a beat (short / normal / long);
* a music bed under the narration (none / mystery / tension / emotional
  / reflective) and how quiet it stays;
* what happens when the beat ends — a breath, a music bridge to a new
  scene, an emotional moment where music lets something land, a sting
  after a turn, near-silence, or a chapter break.

The plan is language-independent (beats are shared by all four
languages); exact times come from each language's real narration.

Deterministic guard-rails keep it professional: lengths are clamped to
the configured ranges, music-only moments stay special (time share and
spacing, with reveals and chapter ends protected), emotional moments
need an emotional beat, stings need a turn, and the last beat ends the
film. Every adjustment is logged.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import AudioDirectionConfig, ai_config
from app.db.models import AudioPlan, Case, EditorialBlueprint
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

MUSIC_TYPES = ("music_bridge", "emotional_moment", "chapter_break", "sting")
MOODS = ("mystery", "tension", "emotional", "reflective")
STING_PURPOSES = {"reveal", "contradiction", "evidence", "false_lead", "chapter_end"}
PROTECTED_PURPOSES = {"reveal", "chapter_end"}


def director_system_prompt(cfg: AudioDirectionConfig | None = None) -> str:
    cfg = cfg or ai_config.audio_direction
    rng = {k: f"{v[0]:g}–{v[1]:g} s" for k, v in cfg.transitions.items()}
    share = round(cfg.max_music_only_share * 100)
    return f"""
You are an award-winning sound designer and music supervisor for
narrative true-crime audio documentaries — the calibre of the best
long-form podcasts and streaming documentaries. The narration is final.
Your job is the listening experience: give the listener time to
breathe, to think and to feel, without slowing the story to a crawl and
without decorating it.

For EVERY beat decide:
1. paragraph_breath — pause between paragraphs inside the beat:
   short | normal | long. Dense, factual or emotional beats need more
   air; a brisk hook can use short.
2. bed — music under the narration: none | mystery | tension |
   emotional | reflective, and bed_level: very_low | low. Music under
   words must never compete with them. Leave dense information and
   quotations clean (none). Keep one bed running across several
   consecutive beats instead of switching every beat.
3. after — what happens when the beat ends, before the next begins:
   - breath ({rng['breath']}): the default; room to think.
   - music_bridge ({rng['music_bridge']}): narration stops, music carries
     us to a new scene, place or time.
   - emotional_moment ({rng['emotional_moment']}): narration stops after
     a human or painful moment; music lets it land.
   - sting ({rng['sting']}): one low accent after a turn or a reveal.
   - silence ({rng['silence']}): almost nothing — for the hardest moments.
   - chapter_break ({rng['chapter_break']}): between big movements of the
     film.
   Give "seconds" and, for music, a "mood": mystery | tension |
   emotional | reflective.

Rules:
- Music-only moments (music_bridge, emotional_moment, sting,
  chapter_break) are special: together at most about {share}% of the
  running time, and normally at least
  {cfg.min_seconds_between_music_moments:g} seconds of narration between
  two of them — except right after a reveal or at a chapter end.
- Every beat change gets at least a breath — a real pause, not a comma.
- Rhythm: the listener should rarely go more than three or four minutes
  without a music_bridge, emotional_moment, sting, silence or
  chapter_break. Put music_bridge at real scene changes (a new place, a
  new time, a new person's thread) and emotional_moment after a human,
  painful or intimate beat — that is where a listener needs to stop and
  feel or think.
- Music must not claim what the facts do not: no menace where the facts
  are neutral, nothing triumphant over victims, no sound effects.
- The last beat ends the film: after = {{"type": "end"}}.

Return JSON only:
{{"notes": "two or three sentences on the overall sound arc",
  "beats": [{{"beat_id": "B01", "paragraph_breath": "normal",
    "bed": "mystery", "bed_level": "very_low",
    "after": {{"type": "music_bridge", "seconds": 5, "mood": "mystery"}},
    "why": "short reason"}}]}}
"""


def _director_input(blueprint: dict) -> dict:
    return {
        "central_question": blueprint.get("central_question"),
        "arcs": blueprint.get("arcs"),
        "beats": [
            {
                "beat_id": b["id"], "purpose": b.get("purpose"),
                "summary": b.get("summary"), "words": b.get("words"),
                "emotional_load": b.get("emotional_load"),
                "information_density": b.get("information_density"),
                "mystery_intensity": b.get("mystery_intensity"),
                "attention": b.get("attention"),
                "audio_intent": b.get("audio_intent"),
                "suggested_pause": b.get("pause_after"),
                "suggested_music": b.get("music_intent"),
                "human_focus": b.get("human_focus"),
            }
            for b in blueprint.get("beats") or []
        ],
    }


def validate_audio_plan(raw: dict, blueprint: dict,
                        cfg: AudioDirectionConfig | None = None) -> tuple[dict, dict]:
    cfg = cfg or ai_config.audio_direction
    raw = raw if isinstance(raw, dict) else {}
    beats = blueprint.get("beats") or []
    errors: list[dict] = []
    warnings: list[dict] = []
    adjustments: list[dict] = []
    given = {}
    for item in raw.get("beats") or []:
        if isinstance(item, dict) and item.get("beat_id"):
            given[str(item["beat_id"])] = item
    known = {b["id"] for b in beats}
    for bid in given:
        if bid not in known:
            warnings.append({"code": "unknown_beat", "beat": bid})
    if not beats:
        errors.append({"code": "no_beats"})
    elif not given:
        errors.append({"code": "no_plan"})

    wpm = ai_config.words_per_minute_for("en")
    plan_beats: list[dict] = []
    for i, b in enumerate(beats):
        g = given.get(b["id"])
        if g is None:
            warnings.append({"code": "beat_missing_in_plan", "beat": b["id"]})
            g = {}
        breath = g.get("paragraph_breath")
        if breath not in cfg.paragraph_breath_ms:
            breath = "normal"
        bed = g.get("bed") if g.get("bed") in MOODS else "none"
        level = g.get("bed_level") if g.get("bed_level") in cfg.bed_levels_db else "very_low"
        after = g.get("after") if isinstance(g.get("after"), dict) else {}
        kind = after.get("type")
        if kind not in cfg.transitions and kind != "end":
            if kind is not None:
                warnings.append({"code": "unknown_transition", "beat": b["id"],
                                 "value": kind})
            kind = "breath"
        mood = after.get("mood") if after.get("mood") in MOODS else (
            "emotional" if kind == "emotional_moment" else "mystery")
        try:
            seconds = float(after.get("seconds"))
        except (TypeError, ValueError):
            seconds = None
        plan_beats.append({
            "beat_id": b["id"], "purpose": b.get("purpose"),
            "emotional_load": b.get("emotional_load"),
            "seconds_narration": (b.get("words") or 0) / wpm * 60,
            "paragraph_breath": breath, "bed": bed, "bed_level": level,
            "after": {"type": kind, "seconds": seconds, "mood": mood},
            "why": str(g.get("why") or "")[:200],
        })

    def adjust(pb, to_type, reason, seconds=None):
        adjustments.append({"beat": pb["beat_id"], "from": pb["after"]["type"],
                            "to": to_type, "reason": reason})
        pb["after"]["type"] = to_type
        pb["after"]["seconds"] = seconds

    # --- structural rules ---------------------------------------------
    for i, pb in enumerate(plan_beats):
        last = i == len(plan_beats) - 1
        kind = pb["after"]["type"]
        if last:
            if kind != "end":
                adjust(pb, "end", "last_beat_ends_film")
            continue
        if kind == "end":
            adjust(pb, "breath", "end_only_at_last_beat")
        elif kind == "emotional_moment" and pb["emotional_load"] == "low":
            adjust(pb, "music_bridge", "emotional_moment_needs_emotional_beat")
        elif kind == "sting" and pb["purpose"] not in STING_PURPOSES:
            adjust(pb, "breath", "sting_needs_a_turn")

    def protected(pb) -> bool:
        return pb["after"]["type"] == "chapter_break" or pb["purpose"] in PROTECTED_PURPOSES

    # --- spacing: music moments stay special --------------------------
    since = cfg.min_seconds_between_music_moments  # allow one early
    for pb in plan_beats:
        since += pb["seconds_narration"]
        if pb["after"]["type"] in MUSIC_TYPES:
            if since < cfg.min_seconds_between_music_moments and not protected(pb):
                adjust(pb, "breath", "too_soon_after_previous_music_moment")
            else:
                since = 0.0

    # --- clamp lengths ---------------------------------------------------
    for pb in plan_beats:
        kind = pb["after"]["type"]
        if kind == "end":
            pb["after"]["seconds"] = 0.0
            continue
        lo, hi = cfg.transitions[kind]
        s = pb["after"]["seconds"]
        clamped = (lo + hi) / 2 if s is None else min(max(s, lo), hi)
        if s is not None and abs(clamped - s) > 1e-6:
            adjustments.append({"beat": pb["beat_id"], "clamped": [s, clamped]})
        pb["after"]["seconds"] = round(clamped, 2)

    # --- share cap ---------------------------------------------------------
    runtime = sum(pb["seconds_narration"] + pb["after"]["seconds"] for pb in plan_beats)
    music = [pb for pb in plan_beats if pb["after"]["type"] in MUSIC_TYPES]

    def music_seconds():
        return sum(pb["after"]["seconds"] for pb in plan_beats
                   if pb["after"]["type"] in MUSIC_TYPES)

    budget = cfg.max_music_only_share * max(runtime, 1.0)
    if music_seconds() > budget:
        for pb in music:  # first shorten the unprotected ones
            if not protected(pb):
                lo = cfg.transitions[pb["after"]["type"]][0]
                if pb["after"]["seconds"] > lo:
                    adjustments.append({"beat": pb["beat_id"],
                                        "shortened": [pb["after"]["seconds"], lo]})
                    pb["after"]["seconds"] = lo
        for pb in reversed(music):  # then drop unprotected, latest first
            if music_seconds() <= budget:
                break
            if not protected(pb) and pb["after"]["type"] in MUSIC_TYPES:
                adjust(pb, "breath", "music_share_cap",
                       seconds=sum(cfg.transitions["breath"]) / 2)

    for pb in plan_beats:
        pb.pop("seconds_narration", None)
        pb.pop("emotional_load", None)

    bed_switches = sum(
        1 for a, b in zip(plan_beats, plan_beats[1:]) if a["bed"] != b["bed"]
    )
    report_music = music_seconds()
    status = "invalid" if errors else ("needs_review" if warnings else "valid")
    plan = {"notes": str(raw.get("notes") or "")[:600], "beats": plan_beats}
    report = {
        "status": status, "errors": errors, "warnings": warnings,
        "adjustments": adjustments,
        "estimated_runtime_seconds": round(runtime, 1),
        "music_only_seconds": round(report_music, 1),
        "music_only_share": round(report_music / max(runtime, 1.0), 3),
        "music_moments": sum(1 for pb in plan_beats if pb["after"]["type"] in MUSIC_TYPES),
        "bed_switches": bed_switches,
    }
    return plan, report


class AudioDirector:
    def __init__(self):
        self.gen = get_generation_provider()

    async def create(self, db: Session, case: Case, blueprint_row: EditorialBlueprint
                     ) -> AudioPlan:
        if blueprint_row.status == "invalid":
            raise RuntimeError("The blueprint is invalid; fix it before audio direction.")
        blueprint = json.loads(blueprint_row.blueprint_json or "{}")
        system = director_system_prompt()
        payload = _director_input(blueprint)
        with track_run(db, case.id, "Audio Director",
                       input_summary=f"blueprint={blueprint_row.id} "
                                     f"beats={len(payload['beats'])}") as run:
            raw, res = await self.gen.generate_structured(
                "audio_director", system, json.dumps(payload, ensure_ascii=False))
            stamp_run(run, res, "audio_director")
        plan, report = validate_audio_plan(raw, blueprint)
        repairs = 0
        while report["errors"] and repairs < ai_config.audio_direction.max_repair_iterations:
            repairs += 1
            with track_run(db, case.id, "Audio Director",
                           input_summary=f"repair {repairs}") as run:
                raw, res = await self.gen.generate_structured(
                    "audio_director", system,
                    json.dumps({**payload, "previous_plan": raw,
                                "errors_to_fix": report["errors"]},
                               ensure_ascii=False))
                stamp_run(run, res, "audio_director")
            plan, report = validate_audio_plan(raw, blueprint)
        report["repair_iterations"] = repairs
        count = db.query(AudioPlan).filter(
            AudioPlan.blueprint_id == blueprint_row.id).count()
        row = AudioPlan(
            case_id=case.id, blueprint_id=blueprint_row.id, version=count + 1,
            status=report["status"],
            plan_json=json.dumps(plan, ensure_ascii=False),
            validation_json=json.dumps(report, ensure_ascii=False),
            generation_provider=getattr(res, "provider", None),
            generation_model=getattr(res, "model", None),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def latest_audio_plan(db: Session, blueprint_id: int) -> AudioPlan | None:
    return (
        db.query(AudioPlan)
        .filter(AudioPlan.blueprint_id == blueprint_id, AudioPlan.status != "invalid")
        .order_by(AudioPlan.version.desc())
        .first()
    )


def audio_plan_dict(row: AudioPlan) -> dict:
    return {
        "id": row.id, "case_id": row.case_id, "blueprint_id": row.blueprint_id,
        "version": row.version, "status": row.status,
        "generation_model": row.generation_model, "created_at": row.created_at,
        "plan": json.loads(row.plan_json or "{}"),
        "validation": json.loads(row.validation_json or "{}"),
    }
