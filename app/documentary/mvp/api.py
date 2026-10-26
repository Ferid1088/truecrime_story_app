"""Owner-review routes of the Main Visual Planner."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import MvpCandidate, MvpFilmReview, MvpRequirement, VisualAsset, VisualPlan
from app.documentary.mvp import review as RV
from app.documentary.mvp import verify as VF

router = APIRouter(tags=["main-visual-planner"])


class Decision(BaseModel):
    decision: str
    note: str | None = None
    render_sha256: str | None = None


def review_dict(row: MvpFilmReview) -> dict:
    return {"id": row.id, "case_id": row.case_id, "language": row.language, "state": row.state,
            "production_script_id": row.production_script_id, "render_path": row.render_path,
            "render_sha256": row.render_sha256, "validation": json.loads(row.validation_json or "{}"),
            "owner_note": row.owner_note, "created_at": row.created_at, "decided_at": row.decided_at}


@router.get("/api/cases/{case_id}/mvp/reviews")
def list_reviews(case_id: int, db: Session = Depends(get_db)):
    rows = db.query(MvpFilmReview).filter_by(case_id=case_id).order_by(MvpFilmReview.id.desc()).all()
    return [review_dict(r) for r in rows]


@router.post("/api/mvp/reviews/{review_id}/decision")
def decide(review_id: int, payload: Decision, db: Session = Depends(get_db)):
    row = db.get(MvpFilmReview, review_id)
    if row is None:
        raise HTTPException(404, "Review not found")
    try:
        return review_dict(RV.decide(db, row, payload.decision, payload.note, payload.render_sha256))
    except RV.ReviewError as e:
        raise HTTPException(409, str(e)) from e


class CandidateDecision(BaseModel):
    decision: str
    note: str | None = None


def candidate_dict(db: Session, cand: MvpCandidate, req: MvpRequirement) -> dict:
    asset = db.get(VisualAsset, cand.visual_asset_id)
    return {"id": cand.id, "requirement": req.requirement_key, "beat_id": req.beat_id,
            "entity": req.entity_key, "function": req.function, "role_needed": req.role_needed,
            "need": json.loads(req.spec_json or "{}").get("need"),
            "asset_id": asset.id, "asset_code": asset.asset_code, "asset_type": asset.asset_type,
            "title": asset.title, "source": asset.source_name, "rights": asset.rights_status,
            "thumb_url": f"/api/visuals/{asset.id}/file?thumb=1", "role": cand.role,
            "status": cand.final_status, "provenance": cand.provenance_status,
            "identity": cand.identity_status, "rights_decision": cand.rights_status,
            "ethics": cand.ethics_status, "scene": cand.scene_status,
            "reasons": json.loads(cand.reasons_json or "[]"),
            "verifiers": json.loads(cand.verifiers_json or "[]")}


@router.get("/api/cases/{case_id}/mvp/plan")
def mvp_plan(case_id: int, db: Session = Depends(get_db)):
    """The planner's state for the case: graph, requirements, candidate verdicts, search rounds, shortages."""
    plan = (db.query(VisualPlan).filter_by(case_id=case_id).order_by(VisualPlan.id.desc()).all())
    needs = next((p for p in plan if json.loads(p.validation_json or "{}").get("mvp", {}).get("language") is None
                  and "mvp" in json.loads(p.validation_json or "{}")), None)
    if needs is None:
        raise HTTPException(404, "No Main Visual Planner plan for this case yet")
    reqs = db.query(MvpRequirement).filter_by(visual_plan_id=needs.id).order_by(MvpRequirement.id).all()
    verdicts = {s: sum(1 for r in reqs for c in r.candidates if c.final_status == s)
                for s in ("approved", "rejected", "needs_review", "pending", "transient_failure")}
    return {"visual_plan_id": needs.id, "reveal_graph_id": json.loads(needs.validation_json)["mvp"].get(
        "reveal_graph_id"), "requirements": [
            {"key": r.requirement_key, "beat_id": r.beat_id, "node_key": r.node_key, "function": r.function,
             "entity": r.entity_key, "role_needed": r.role_needed, "priority": r.priority, "status": r.status,
             "rounds_used": r.rounds_used, "shortage": json.loads(r.shortage_json) if r.shortage_json else None,
             "candidates": len(r.candidates),
             "approved": sum(1 for c in r.candidates if c.final_status == "approved"),
             "search_rounds": [{"round": s.round_no, "provider": s.provider, "query": s.query,
                                "new_candidates": s.new_candidates, "cost_eur": s.cost_eur, "error": s.error}
                               for s in r.search_rounds]} for r in reqs],
            "candidate_totals": verdicts,
            "shortages": sum(1 for r in reqs if r.status == "shortage")}


@router.get("/api/cases/{case_id}/mvp/candidates")
def mvp_candidates(case_id: int, status: str = "needs_review", beat: str | None = None, limit: int = 100,
                   db: Session = Depends(get_db)):
    """The pictures the planner was unsure about (needs_review) or rejected, optionally of one beat only; the
    asset of each candidate is judged once, so a picture appears once per requirement it was judged for."""
    wanted = [s for s in status.split(",") if s in ("needs_review", "rejected", "approved")]
    query = (db.query(MvpCandidate, MvpRequirement)
             .join(MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id)
             .filter(MvpRequirement.case_id == case_id, MvpCandidate.final_status.in_(wanted or ["needs_review"])))
    if beat:
        query = query.filter(MvpRequirement.beat_id == beat)
    rows = query.order_by(MvpCandidate.final_status.desc(), MvpCandidate.id).limit(min(max(limit, 1), 500)).all()
    return [candidate_dict(db, c, r) for c, r in rows]


@router.get("/api/cases/{case_id}/mvp/gaps")
def mvp_gaps(case_id: int, language: str = "en", db: Session = Depends(get_db)):
    """The moments of the film that have no picture, video or graphic after every alternative, with how many
    pictures of each beat the planner rejected or was unsure about (what the owner can still approve)."""
    rows = db.query(VisualPlan).filter_by(case_id=case_id).order_by(VisualPlan.id.desc()).all()
    plan = next((p for p in rows if json.loads(p.validation_json or "{}").get("mvp", {}).get("language") == language
                 and "plan" in json.loads(p.validation_json or "{}")), None)
    if plan is None:
        raise HTTPException(404, f"No {language} direction for this case yet")
    report = json.loads(plan.validation_json)["plan"]
    gaps = report.get("gaps") or []
    open_by_beat: dict[str, dict[str, int]] = {}
    for cand, req in (db.query(MvpCandidate, MvpRequirement)
                      .join(MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id)
                      .filter(MvpRequirement.case_id == case_id,
                              MvpCandidate.final_status.in_(("needs_review", "rejected"))).all()):
        counts = open_by_beat.setdefault(req.beat_id, {"needs_review": 0, "rejected": 0})
        counts[cand.final_status] += 1
    return {"language": language, "gap_count": len(gaps),
            "gap_seconds": round(sum(g["end"] - g["start"] for g in gaps), 1), "gaps": gaps,
            "alternatives": report.get("alternatives", {}),
            "beats": [{"beat_id": b, **open_by_beat.get(b, {"needs_review": 0, "rejected": 0})}
                      for b in sorted({g["beat"] for g in gaps})]}


@router.post("/api/mvp/candidates/{candidate_id}/decision")
def decide_candidate(candidate_id: int, payload: CandidateDecision, db: Session = Depends(get_db)):
    """The owner settles a candidate the verifiers left open (or overrides one). The decision is stored as
    its own verifier entry; rights are not overridden here (use the editorial approval for that)."""
    cand = db.get(MvpCandidate, candidate_id)
    if cand is None:
        raise HTTPException(404, "Candidate not found")
    if payload.decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    req = db.get(MvpRequirement, cand.requirement_id)
    if payload.decision == "approve":
        if cand.rights_status == "blocked":
            raise HTTPException(409, "Rights block this picture; approve its rights first")
        cand.identity_status = cand.ethics_status = cand.scene_status = "approved"
        cand.role = cand.role or req.role_needed
    else:
        cand.identity_status = "rejected"
    cand.verifiers_json = json.dumps(json.loads(cand.verifiers_json or "[]")
                                     + [{"verifier": "owner", "decision": payload.decision,
                                         "note": payload.note}])
    cand.final_status = VF.final_status(cand)
    db.commit()
    VF.sync_asset_status(db, req.case_id)
    return candidate_dict(db, cand, req)
