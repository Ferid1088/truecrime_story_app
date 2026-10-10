"""The AudioDirector agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.audio_director."""

from __future__ import annotations

from app.agents.runner import run_agent
import json
from sqlalchemy.orm import Session
from app.core.ai_config import ai_config
from app.db.models import AudioPlan, Case, EditorialBlueprint
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.documentary.audio_director import (
    _director_input,
    director_system_prompt,
    validate_audio_plan,
)


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
            raw, res = await run_agent("documentary.audio_plan", self.gen, json.dumps(payload, ensure_ascii=False), system=system)
            stamp_run(run, res, "audio_director")
        plan, report = validate_audio_plan(raw, blueprint)
        repairs = 0
        while report["errors"] and repairs < ai_config.audio_direction.max_repair_iterations:
            repairs += 1
            with track_run(db, case.id, "Audio Director",
                           input_summary=f"repair {repairs}") as run:
                raw, res = await run_agent("documentary.audio_plan_repair", self.gen, json.dumps({**payload, "previous_plan": raw,
                                "errors_to_fix": report["errors"]},
                               ensure_ascii=False), system=system)
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
