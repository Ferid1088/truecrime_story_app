"""The VisualPlanner agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.visuals.planner."""

from __future__ import annotations

from app.agents.runner import run_agent
import json
from sqlalchemy.orm import Session
from app.db.models import Case, EditorialBlueprint, VisualPlan
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.documentary.visuals.planner import (
    PLANNER_SYSTEM,
    _evidence,
    _planner_input,
    validate_requirements,
)


class VisualPlanner:
    def __init__(self):
        self.gen = get_generation_provider()

    async def create(self, db: Session, case: Case,
                     blueprint_row: EditorialBlueprint) -> VisualPlan:
        blueprint = json.loads(blueprint_row.blueprint_json or "{}")
        pack, sources = _evidence(db, case.id)
        payload = _planner_input(case, blueprint, pack, sources)
        with track_run(db, case.id, "Visual Planner",
                       input_summary=f"blueprint={blueprint_row.id}") as run:
            raw, res = await run_agent("visuals.plan", self.gen, json.dumps(payload, ensure_ascii=False), system=PLANNER_SYSTEM)
            stamp_run(run, res, "visual_planner")
        reqs, report = validate_requirements(raw, blueprint, pack, sources)
        count = db.query(VisualPlan).filter(VisualPlan.blueprint_id == blueprint_row.id).count()
        row = VisualPlan(
            case_id=case.id, blueprint_id=blueprint_row.id, version=count + 1,
            status="requirements" if report["status"] != "invalid" else "invalid",
            requirements_json=json.dumps(reqs, ensure_ascii=False),
            validation_json=json.dumps({"requirements": report}, ensure_ascii=False),
            generation_model=getattr(res, "model", None),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
