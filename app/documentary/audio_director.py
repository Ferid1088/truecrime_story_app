"""Music/Audio director — music, silence and breathing room around the
narration.

A music supervisor (role audio_director) reads the editorial blueprint
and decides, beat by beat, what the listener hears AROUND the narrator:

* the pause between paragraphs inside a beat (short / normal / long);
* what happens in the gap when the beat ends — a breath, a music bridge
  to a new scene, an emotional moment where music lets something land, a
  sting after a turn, deliberate SILENCE (room tone), or a chapter
  break — with its length, its mood and WHY;
* a music bed under the narration only when the configuration allows it
  (audio_direction.beds_under_narration; off by default: when the
  narrator speaks there is no music).

Moods name the emotional FUNCTION of a moment (audio_direction.moods:
suspense, investigation, mystery, melancholy, danger, discovery, tension,
relief, resolution, uncertainty) — never "suspense because it is true
crime". Moods of the older vocabulary still validate: "emotional" is
read as melancholy and "reflective" as uncertainty (MOOD_ALIASES), so
stored plans keep mixing.

The plan is language-independent (beats are shared by all languages);
exact times come from each language's real narration.

Deterministic guard-rails keep it professional: lengths are clamped to
the configured ranges, music-only moments stay special (time share and
spacing, with reveals and chapter ends protected; silence is not music
and never counts toward the share), emotional moments need an emotional
beat, stings need a turn, beds are removed while narration must stay
clean, and the last beat ends the film. Every adjustment is logged, and
every music or silence choice keeps a short `why` (after.why) that the
mix stores with each placement (MusicUsage).
"""

from __future__ import annotations

from app.core.prompts import prompt

import json

from sqlalchemy.orm import Session

from app.core.ai_config import AudioDirectionConfig, ai_config
from app.db.models import AudioPlan, Case, EditorialBlueprint
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

MUSIC_TYPES = ("music_bridge", "emotional_moment", "chapter_break", "sting")
# Choices that carry a reason: every music moment and every deliberate silence.
EXPLAINED_TYPES = MUSIC_TYPES + ("silence",)
STING_PURPOSES = {"reveal", "contradiction", "evidence", "false_lead", "chapter_end"}
PROTECTED_PURPOSES = {"reveal", "chapter_end"}

# Older plans used a four-mood vocabulary. Two of those words are not in
# the mood catalogue; they map onto the closest emotional function:
# "emotional" moments were grief and the human cost (melancholy), and
# "reflective" moments were the listener thinking about an open question
# (uncertainty). mystery and tension exist in both vocabularies.
MOOD_ALIASES = {"emotional": "melancholy", "reflective": "uncertainty"}

# What each mood is FOR. The prompt teaches the function of a moment,
# not a genre — the mood must come from the facts of the beat.
MOOD_FUNCTIONS = {
    "suspense": "something is about to happen or be learned and the listener "
                "knows it — waiting, leaning forward",
    "investigation": "methodical work: detectives, timelines, records, the "
                     "slow assembly of a case",
    "mystery": "something hidden or unexplained; an open question the story "
               "has just raised",
    "melancholy": "loss, grief, a life cut short — the human cost",
    "danger": "a real threat is present or approaching (only when the facts "
              "show one)",
    "discovery": "something is found or finally understood — a clue, a "
                 "body, a breakthrough",
    "tension": "pressure rises — a confrontation, a contradiction, a "
               "deadline, a lie about to break",
    "relief": "a danger passes, someone is found safe, a weight lifts",
    "resolution": "the case closes — a verdict, a conviction, the end of "
                  "the story",
    "uncertainty": "doubt, conflicting accounts, a question that may never "
                   "be answered",
}


def known_moods(cfg: AudioDirectionConfig | None = None) -> list[str]:
    cfg = cfg or ai_config.audio_direction
    return list(cfg.moods)


def normalize_mood(mood, kind: str | None = None,
                   cfg: AudioDirectionConfig | None = None) -> str:
    """A mood from the catalogue: aliases of the old vocabulary are
    mapped, anything unknown falls back by transition type (an emotional
    moment is melancholy; otherwise mystery — the most neutral colour)."""
    moods = known_moods(cfg)
    m = str(mood or "").strip().lower()
    m = MOOD_ALIASES.get(m, m)
    if m in moods:
        return m
    for fallback in (("melancholy",) if kind == "emotional_moment" else ()) + ("mystery",):
        if fallback in moods:
            return fallback
    return moods[0] if moods else "mystery"


def _mood_catalogue(cfg: AudioDirectionConfig) -> str:
    lines = []
    for m in cfg.moods:
        what = MOOD_FUNCTIONS.get(m)
        lines.append(f"   - {m}: {what}" if what else f"   - {m}")
    return "\n".join(lines)


def director_system_prompt(cfg: AudioDirectionConfig | None = None) -> str:
    cfg = cfg or ai_config.audio_direction
    rng = {k: f"{v[0]:g}–{v[1]:g} s" for k, v in cfg.transitions.items()}
    share = round(cfg.max_music_only_share * 100)
    moods = " | ".join(cfg.moods)
    if cfg.beds_under_narration:
        bed_rule = (
            prompt("documentary/audio_director/director_system_prompt_2").format(moods=moods))
    else:
        bed_rule = (
            'always "none". While the narrator speaks there is NO music —\n'
            "   the narration stays clean. Music belongs only in the gaps.")
    chapter_rule = (
        "\n     The film shows a chapter card in this gap: use chapter_break\n"
        "     exactly where a beat ends an act (ends_act true) — every act end —\n"
        "     and after a cold open (opening_title true); nowhere else."
        if ai_config.chapters.enabled else "")
    return prompt("documentary/audio_director/director_system_prompt").format(bed_rule=bed_rule, v0=rng['breath'], v1=rng['music_bridge'], v2=rng['emotional_moment'], v3=rng['sting'], v4=rng['silence'], v5=rng['chapter_break'], chapter_rule=chapter_rule, v6=_mood_catalogue(cfg), share=share, v7=cfg.min_seconds_between_music_moments)


def chapter_gaps(blueprint: dict) -> dict[str, str]:
    """Beats after which the film shows a card (chapters enabled): the
    last beat of every act but the last ("act_end"), and the opening hook
    when the film has a cold open ("opening_title")."""
    if not ai_config.chapters.enabled:
        return {}
    beats = blueprint.get("beats") or []
    out = {}
    for b, n in zip(beats, beats[1:]):
        if (b.get("act_id") or "") != (n.get("act_id") or ""):
            out[b["id"]] = "act_end"
    if (len(beats) > 1 and beats[0].get("purpose") == "hook"
            and beats[0]["id"] not in out and ai_config.chapters.title_card):
        out[beats[0]["id"]] = "opening_title"
    return out


def _director_input(blueprint: dict) -> dict:
    gaps = chapter_gaps(blueprint)
    return {
        "central_question": blueprint.get("central_question"),
        "arcs": blueprint.get("arcs"),
        "beats": [
            {
                "beat_id": b["id"], "act_id": b.get("act_id"),
                "ends_act": gaps.get(b["id"]) == "act_end",
                "opening_title": gaps.get(b["id"]) == "opening_title",
                "purpose": b.get("purpose"),
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


def _bed(value, cfg: AudioDirectionConfig) -> str:
    """The requested bed as a catalogue mood, or "none"."""
    if value in (None, "", "none"):
        return "none"
    m = MOOD_ALIASES.get(str(value).strip().lower(), str(value).strip().lower())
    return m if m in cfg.moods else "none"


def _explain(pb: dict) -> str:
    """The reason stored with a music or silence choice: the director's
    words, plus any guard-rail that changed the choice — so the audit
    never shows a reason for a cue the director did not pick."""
    kind = pb["after"]["type"]
    why = pb["_why"] or (
        f"{kind.replace('_', ' ')} after the {pb.get('purpose') or 'beat'} beat "
        "(the director gave no reason)")
    changed = [c for c in pb["_changes"] if c["to"] == kind]
    if changed:
        c = changed[-1]
        why += f" [validator: {c['from']} → {kind}, {c['reason']}]"
    return why[:300]


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
    beds_removed = 0
    for i, b in enumerate(beats):
        g = given.get(b["id"])
        if g is None:
            warnings.append({"code": "beat_missing_in_plan", "beat": b["id"]})
            g = {}
        breath = g.get("paragraph_breath")
        if breath not in cfg.paragraph_breath_ms:
            breath = "normal"
        bed = _bed(g.get("bed"), cfg)
        if bed != "none" and not cfg.beds_under_narration:
            # Clean narration: the narrator never speaks over music.
            adjustments.append({"beat": b["id"], "bed": [g.get("bed"), "none"],
                                "reason": "no_music_under_narration"})
            beds_removed += 1
            bed = "none"
        level = g.get("bed_level") if g.get("bed_level") in cfg.bed_levels_db else "very_low"
        after = g.get("after") if isinstance(g.get("after"), dict) else {}
        kind = after.get("type")
        if kind not in cfg.transitions and kind != "end":
            if kind is not None:
                warnings.append({"code": "unknown_transition", "beat": b["id"],
                                 "value": kind})
            kind = "breath"
        asked = after.get("mood")
        mood = normalize_mood(asked, kind, cfg)
        if asked in MOOD_ALIASES:
            adjustments.append({"beat": b["id"], "mood": [asked, mood],
                                "reason": "mood_alias"})
        try:
            seconds = float(after.get("seconds"))
        except (TypeError, ValueError):
            seconds = None
        beat_why = str(g.get("why") or "")[:200]
        plan_beats.append({
            "beat_id": b["id"], "purpose": b.get("purpose"),
            "emotional_load": b.get("emotional_load"),
            "seconds_narration": (b.get("words") or 0) / wpm * 60,
            "paragraph_breath": breath, "bed": bed, "bed_level": level,
            "after": {"type": kind, "seconds": seconds, "mood": mood},
            "why": beat_why,
            "_why": str(after.get("why") or "").strip()[:240] or beat_why,
            "_changes": [],
        })

    def adjust(pb, to_type, reason, seconds=None):
        change = {"beat": pb["beat_id"], "from": pb["after"]["type"],
                  "to": to_type, "reason": reason}
        adjustments.append(change)
        pb["_changes"].append(change)
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

    # --- chapter cards: a chapter_break exactly at every act end (and
    # after a cold open), nowhere else -----------------------------------
    gaps = chapter_gaps(blueprint)
    if gaps:
        for i, pb in enumerate(plan_beats[:-1]):
            kind = pb["after"]["type"]
            if pb["beat_id"] in gaps and kind != "chapter_break":
                adjust(pb, "chapter_break",
                       "act_end_is_chapter_break" if gaps[pb["beat_id"]] == "act_end"
                       else "title_after_cold_open")
            elif pb["beat_id"] not in gaps and kind == "chapter_break":
                adjust(pb, "music_bridge", "chapter_break_only_between_acts")

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

    # --- share cap (silence is not music: it never counts) -------------
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

    # --- every music / silence choice keeps its reason ----------------
    for pb in plan_beats:
        if pb["after"]["type"] in EXPLAINED_TYPES:
            pb["after"]["why"] = _explain(pb)
        pb.pop("_why", None)
        pb.pop("_changes", None)
        pb.pop("seconds_narration", None)
        pb.pop("emotional_load", None)

    bed_switches = sum(
        1 for a, b in zip(plan_beats, plan_beats[1:]) if a["bed"] != b["bed"]
    )
    report_music = music_seconds()
    moods: dict[str, int] = {}
    for pb in plan_beats:
        if pb["after"]["type"] in MUSIC_TYPES:
            moods[pb["after"]["mood"]] = moods.get(pb["after"]["mood"], 0) + 1
    status = "invalid" if errors else ("needs_review" if warnings else "valid")
    plan = {"notes": str(raw.get("notes") or "")[:600], "beats": plan_beats}
    report = {
        "status": status, "errors": errors, "warnings": warnings,
        "adjustments": adjustments,
        "estimated_runtime_seconds": round(runtime, 1),
        "music_only_seconds": round(report_music, 1),
        "music_only_share": round(report_music / max(runtime, 1.0), 3),
        "music_moments": sum(1 for pb in plan_beats if pb["after"]["type"] in MUSIC_TYPES),
        "silences": sum(1 for pb in plan_beats if pb["after"]["type"] == "silence"),
        "moods": moods,
        "beds_under_narration": cfg.beds_under_narration,
        "beds_removed": beds_removed,
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
