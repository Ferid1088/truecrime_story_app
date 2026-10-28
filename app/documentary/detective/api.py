"""Owner-inspection and targeted-revision routes for the detective toolkit (Section I).

Read-only inspection needs no flag check (it just shows whatever Sections B-H already persisted).
The repair endpoint reaches the Detective Director for real, so it respects the same
`detective_toolkit.enabled` gate as every other live call path in this feature.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.base import get_db
from app.db.models import DetectiveScene, RevealGraph
from app.documentary.detective import director as DIR
from app.documentary.detective.inspector import invalidate_dependent_scenes, plan_summary
from app.documentary.memory import record_knowledge_event

router = APIRouter(tags=["detective-toolkit"])


def _latest_graph(db: Session, case_id: int) -> RevealGraph:
    graph = (db.query(RevealGraph).filter_by(case_id=case_id)
             .order_by(RevealGraph.id.desc()).first())
    if graph is None:
        raise HTTPException(404, "No reveal graph for this case yet")
    return graph


@router.get("/api/cases/{case_id}/detective/plan")
def detective_plan(case_id: int, db: Session = Depends(get_db)):
    """Every detective scene for this case's current reveal graph: aim, chosen tools, the Main
    Visual Planner's accept/combine/defer/reject decision and why, and any design review already
    compiled for it. Section I's inspector view."""
    graph = _latest_graph(db, case_id)
    return plan_summary(db, graph)


class CorrectionPayload(BaseModel):
    reason: str
    new_text: str | None = None


@router.post("/api/cases/{case_id}/detective/claims/{claim_key}/correct")
def correct_claim(case_id: int, claim_key: str, payload: CorrectionPayload, db: Session = Depends(get_db)):
    """Record an owner correction to a claim as a knowledge event (Section B's event log is
    append-only; a correction is itself an event, not a silent row rewrite), then mark every scene
    whose actions actually bind to this claim as stale. Unrelated scenes, and this claim's own
    history, are untouched."""
    graph = _latest_graph(db, case_id)
    try:
        record_knowledge_event(
            db, graph.id, subject_type="claim", subject_key=claim_key, operation="update",
            new_state={"text": payload.new_text, "reason": payload.reason} if payload.new_text
            else {"reason": payload.reason},
        )
    except Exception as e:  # noqa: BLE001 - surfaced as a 422 with the memory layer's own message
        raise HTTPException(422, str(e)) from e
    affected = invalidate_dependent_scenes(db, graph.id, claim_key, payload.reason)
    return {"claim_key": claim_key, "invalidated_scenes": affected}


class RepairPayload(BaseModel):
    issues: list[str]
    language: str = "en"
    text_version: int = 1


@router.post("/api/cases/{case_id}/detective/scenes/{scene_key}/repair")
async def repair_scene(case_id: int, scene_key: str, payload: RepairPayload, db: Session = Depends(get_db)):
    """Targeted re-direction of one scene, without rerunning the whole plan — the Detective
    Director's own `repair` mode (Section C), scoped to this scene's current persisted content.
    A real paid call, so it honours the same off-by-default `detective_toolkit.enabled` gate as
    `toolkit.invoke`."""
    if not ai_config.detective_toolkit.enabled:
        raise HTTPException(409, "detective_toolkit.enabled is False; repair is not active")
    graph = _latest_graph(db, case_id)
    scene = db.query(DetectiveScene).filter_by(graph_id=graph.id, scene_key=scene_key).first()
    if scene is None:
        raise HTTPException(404, "No such scene for this case's current graph")
    from app.documentary.detective.inspector import scene_dict

    previous_output = {"scenes": [scene_dict(scene)]}
    result = await DIR.repair(db, graph.id, previous_output, payload.issues,
                              payload.language, payload.text_version)
    scene.stale = False
    scene.stale_reason = None
    db.commit()
    return {"agent_run_id": result["agent_run_id"], "counts": result["counts"]}
