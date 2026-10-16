"""The stage director (Part 7c): after the voice step, from the REAL audio
(voice_seconds and the stored character alignment), it decides cameras,
framing, outfit, posture, gestures and lighting of one host appearance. The
direction is validated by deterministic guard-rails and remembered only when it
passes; it never changes the audio."""

from __future__ import annotations

import json

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import HostAppearance, HostScene, HostSegments
from app.documentary import looks as LK
from app.documentary import stage_direction as SD
from app.documentary import storage
from app.documentary import studio as ST
from app.documentary import visual_memory as VM
from app.documentary.host import _memory_index, archive, memories, persona_prompt, persona_sha256
from app.outfits.registry import OutfitRegistry
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run


class StageDirectionError(RuntimeError):
    def __init__(self, errors: list[dict]):
        super().__init__(f"the stage direction failed its guard-rails: {errors}")
        self.errors = errors


def is_sensitive(language: str, *texts: str) -> bool:
    low = " ".join(texts).lower()
    return any(t.lower() in low for t in ai_config.stage_direction.sensitive_terms.get(language, []))


def allowed_outfits(language: str) -> list[str]:
    ids = [o.outfit_id for o in OutfitRegistry().all()]
    allow = ai_config.stage_direction.outfit_allow_list.get(language)
    return [i for i in ids if i in allow] if allow else ids


def alignment_of(scene: HostScene) -> dict:
    """The stored character alignment written next to the voice file."""
    path = storage.resolve(scene.voice_path) if scene.voice_path else None
    side = path.with_name(path.name + ".json") if path else None
    if side is None or not side.exists():
        return {}
    return json.loads(side.read_text(encoding="utf-8")).get("alignment") or {}


class StageDirector:
    def __init__(self):
        self.gen = get_generation_provider()
        self.cfg = ai_config.host

    def _context(self, db: Session, scene: HostScene) -> dict:
        if not scene.voice_seconds:
            raise StageDirectionError([{"code": "no_real_voice_yet"}])
        profile = ST.channel_profile(scene.language)["studio_profile_id"]
        looks = LK.approved_looks(db, profile)
        if not looks:
            raise StageDirectionError([{"code": "no_approved_look", "studio": profile}])
        row = db.get(HostSegments, scene.host_segments_id)
        seg = next((s for s in json.loads(row.segments_json or "[]")
                    if s.get("id") == scene.host_segment_id), {})
        claims = " ".join(c.get("text", "") for c in seg.get("claims") or [])
        refs = set(_memory_index(archive(db, scene.case_id), memories(db, scene.case_id)))
        return {
            "profile": profile, "looks": looks, "refs": refs,
            "ends": SD.sentence_ends(alignment_of(scene)),
            "outfits": allowed_outfits(scene.language),
            "recent": VM.recent(db, scene.channel),
            "sensitive": is_sensitive(scene.language, scene.text, claims),
            "segment": seg,
        }

    def _input(self, scene: HostScene, ctx: dict) -> dict:
        seg = ctx["segment"]
        return {
            "appearance_id": f"case{scene.case_id}_{scene.host_segment_id}_{scene.language}",
            "language": scene.language, "position": scene.position,
            "studio_profile_id": ctx["profile"], "voice_seconds": scene.voice_seconds,
            "sentence_ends": ctx["ends"], "dialogue": scene.text,
            "purpose_from_plan": seg.get("purpose"), "dimensions": seg.get("dimensions"),
            "memory_reference": seg.get("memory_reference"), "claims": seg.get("claims"),
            "approved_looks": sorted(ctx["looks"]), "outfit_ids": ctx["outfits"],
            "last_outfit_any_channel": ctx["last_outfit"],
            "recent_appearances": [{k: r[k] for k in ("outfit_id", "posture", "lighting",
                                                      "emotion", "shots", "gestures")}
                                   for r in ctx["recent"]],
            "sensitive": ctx["sensitive"],
            "vocabulary": {"postures": SD.POSTURES, "gestures": SD.GESTURES,
                           "lighting": SD.LIGHTING, "emotions": SD.EMOTIONS,
                           "movements": SD.MOVEMENTS, "eye_contact": SD.EYE_CONTACT},
        }

    async def direct(self, db: Session, scene: HostScene) -> HostAppearance:
        ctx = self._context(db, scene)
        ctx["last_outfit"] = VM.last_outfit_any_channel(db)
        payload = self._input(scene, ctx)
        persona = persona_prompt(self.cfg)
        system = prompt("documentary/host/stage_director_system_prompt").replace(
            "<<PERSONA>>", persona)
        if persona not in system:
            raise RuntimeError("the host persona is missing from the stage director prompt")
        errors: list[dict] = []
        direction = None
        accepted = None   # a direction that passed every guard-rail
        raw: dict = {}
        for attempt in range(1 + self.cfg.max_repair_iterations):
            body = payload if attempt == 0 else {**payload, "previous_direction": raw,
                                                 "errors_to_fix": errors}
            with track_run(db, scene.case_id, "Stage Director",
                           input_summary=f"scene={scene.id} attempt={attempt} "
                                         f"persona_sha256={persona_sha256(self.cfg)}") as run:
                raw, res = await run_agent("host.stage_direction", self.gen,
                                           json.dumps(body, ensure_ascii=False), system=system)
                stamp_run(run, res, "host_stage_director")
            try:
                direction = SD.StageDirection(**raw)
            except (ValidationError, TypeError) as e:
                errors = [{"code": "schema", "detail": str(e)[:300]}]
                continue
            errors = SD.validate_direction(
                direction, voice_seconds=scene.voice_seconds, sentence_ends=ctx["ends"],
                approved_looks=ctx["looks"], recent=ctx["recent"], sensitive=ctx["sensitive"],
                memory_refs=ctx["refs"], outfit_ids=set(ctx["outfits"]))
            if errors:
                continue
            accepted = direction
            if attempt == 0 and VM.repeats_full_combination(direction, ctx["recent"]):
                # one request for variety; if that fails the guard-rails the first
                # direction stands (never a worse direction just to be different)
                errors = [{"code": "repeats_recent_combination"}]
                continue
            break
        if accepted is not None and errors:
            direction, errors = accepted, []
        if errors or direction is None:
            raise StageDirectionError(errors)
        row = VM.record(db, scene, direction, [], persona_sha256=persona_sha256(self.cfg))
        db.commit()
        return row
