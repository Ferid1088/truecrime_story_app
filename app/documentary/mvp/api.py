"""Owner-review routes of the Main Visual Planner."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.base import get_db
from app.db.models import MvpCandidate, MvpFilmReview, MvpRequirement, VisualAsset, VisualPlan
from app.documentary.mvp import direct as DR
from app.documentary.mvp import overlays as OV
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
            "clip_start": asset.clip_start, "clip_end": asset.clip_end,
            "width": asset.width, "height": asset.height, "cut": OV.cut_view(asset),
            "duration_seconds": asset.duration_seconds,
            "owner_note": asset.owner_note, "must_use": asset.must_use, "draw_priority": asset.draw_priority,
            "thumb_url": f"/api/visuals/{asset.id}/file?thumb=1", "role": cand.role,
            "status": cand.final_status, "provenance": cand.provenance_status,
            "identity": cand.identity_status, "rights_decision": cand.rights_status,
            "ethics": cand.ethics_status, "scene": cand.scene_status,
            "rights_blocked": cand.rights_status == "blocked", "rights_reason": asset.rights_reason,
            "reasons": json.loads(cand.reasons_json or "[]"),
            "verifiers": json.loads(cand.verifiers_json or "[]")}


def needs_plan_of(db: Session, case_id: int) -> VisualPlan:
    """The case's shared needs plan: the planner's plan row that is no language's own direction."""
    for row in db.query(VisualPlan).filter_by(case_id=case_id).order_by(VisualPlan.id.desc()).all():
        mvp = json.loads(row.validation_json or "{}").get("mvp", {})
        if mvp.get("reveal_graph_id") and not mvp.get("language"):
            return row
    raise HTTPException(404, "This case has no Main Visual Planner needs plan yet")


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


STATUSES = ("needs_review", "rejected", "approved", "pending", "transient_failure")


def grouped(db: Session, rows: list[tuple[MvpCandidate, MvpRequirement]]) -> list[dict]:
    """One entry per picture and entity: the same picture is judged once for every requirement it was offered for,
    but the owner decides it once (all its candidates get the decision). `candidate_ids` are those candidates."""
    groups: dict[tuple[int, str | None], dict] = {}
    for cand, req in rows:
        key = (cand.visual_asset_id, req.entity_key)
        if key not in groups:
            groups[key] = {**candidate_dict(db, cand, req), "candidate_ids": [], "requirements": []}
        groups[key]["candidate_ids"].append(cand.id)
        groups[key]["rights_blocked"] = groups[key]["rights_blocked"] or cand.rights_status == "blocked"
        groups[key]["requirements"].append({"requirement": req.requirement_key, "beat_id": req.beat_id,
                                            "function": req.function, "role_needed": req.role_needed,
                                            "need": json.loads(req.spec_json or "{}").get("need")})
    return sorted(groups.values(), key=lambda g: (-len(g["requirements"]), g["id"]))


@router.get("/api/cases/{case_id}/mvp/candidates")
def mvp_candidates(case_id: int, status: str = "needs_review", beat: str | None = None, limit: int = 100,
                   group: bool = False, cut_only: bool = False, db: Session = Depends(get_db)):
    """The pictures the planner was unsure about (needs_review) or rejected, optionally of one beat only. With
    `group` each picture appears once (for each entity it was judged for), however many requirements offered it. With
    `cut_only` only the trimmed and cut pictures and clips of the film: what the owner looks at in review 2."""
    from app.documentary.mvp import pipeline as MVP


    wanted = list(STATUSES) if status == "all" else [s for s in status.split(",") if s in STATUSES]
    query = (db.query(MvpCandidate, MvpRequirement)
             .join(MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id)
             .filter(MvpRequirement.case_id == case_id, MvpCandidate.final_status.in_(wanted or ["needs_review"])))
    if beat:
        query = query.filter(MvpRequirement.beat_id == beat)
    if cut_only:
        cut_assets = [c.visual_asset_id for c in MVP.cut_items(db, needs_plan_of(db, case_id))]
        query = query.filter(MvpCandidate.visual_asset_id.in_(cut_assets or [0]))
    rows = [(c, r) for c, r in query.order_by(MvpCandidate.final_status, MvpCandidate.id).all()
            if not VF.is_duplicate(c)]                      # a copy of a picture the planner keeps is not shown
    if group:
        return grouped(db, rows)[:min(max(limit, 1), 5000)]
    return [candidate_dict(db, c, r) for c, r in rows[:min(max(limit, 1), 500)]]


def apply_decision(db: Session, cand: MvpCandidate, decision: str, note: str | None,
                   override_rights: bool = False) -> MvpRequirement:
    req = db.get(MvpRequirement, cand.requirement_id)
    if decision == "approve":
        cand.identity_status = cand.ethics_status = cand.scene_status = "approved"
        cand.role = cand.role or req.role_needed
        if override_rights and cand.rights_status == "blocked":
            asset = db.get(VisualAsset, cand.visual_asset_id)
            VF.owner_rights_override(asset)
            cand.rights_status = VF.rights_decision(asset)[0]
            if cand.rights_status == "blocked":       # (blocked by what the picture shows, not by its licence)
                cand.rights_status = "preview_only"
    else:
        cand.identity_status = "rejected"
    cand.verifiers_json = json.dumps(json.loads(cand.verifiers_json or "[]")
                                     + [{"verifier": "owner", "decision": decision, "note": note,
                                         **({"rights_override": True} if override_rights else {})}])
    cand.final_status = VF.final_status(cand)
    return req


@router.post("/api/mvp/candidates/{candidate_id}/decision")
def decide_candidate(candidate_id: int, payload: CandidateDecision, db: Session = Depends(get_db)):
    """The owner settles a candidate the verifiers left open (or overrides one). The decision is stored as
    its own verifier entry; rights are not overridden here (use the editorial approval for that)."""
    cand = db.get(MvpCandidate, candidate_id)
    if cand is None:
        raise HTTPException(404, "Candidate not found")
    if payload.decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    if payload.decision == "approve" and cand.rights_status == "blocked":
        raise HTTPException(409, "Rights block this picture; approve its rights first")
    req = apply_decision(db, cand, payload.decision, payload.note)
    db.commit()
    VF.sync_asset_status(db, req.case_id)
    return candidate_dict(db, cand, req)


class OwnerNote(BaseModel):
    note: str | None = None


@router.put("/api/cases/{case_id}/mvp/assets/{asset_id}/note")
def set_owner_note(case_id: int, asset_id: int, payload: OwnerNote, db: Session = Depends(get_db)):
    """The owner's note on a picture or clip. The planner takes it as true: the director sees it beside the picture
    and a slot whose words match it counts as being about the picture. An empty note removes it. The story-timing
    label of the picture is dropped so that it is judged again with the note."""
    asset = db.get(VisualAsset, asset_id)
    if asset is None or asset.case_id != case_id:
        raise HTTPException(404, "No such picture in this case")
    note = " ".join((payload.note or "").split())
    limit = ai_config.mvp().owner_note.max_chars
    if len(note) > limit:
        raise HTTPException(422, f"The note may have at most {limit} characters")
    asset.owner_note = note or None
    spec = json.loads(asset.spec_json or "{}")
    if spec.pop("timing", None) is not None:
        asset.spec_json = json.dumps(spec, ensure_ascii=False)
    db.commit()
    return {"asset_id": asset.id, "owner_note": asset.owner_note}


class AssetSelection(BaseModel):
    """What the owner changed: a field that is not sent keeps its value."""

    must_use: bool = False
    draw_priority: int | None = Field(default=None, ge=0, le=10)       # 0 (or none) unselects the photo and frees its number


@router.put("/api/cases/{case_id}/mvp/assets/{asset_id}/selection")
def set_asset_selection(case_id: int, asset_id: int, payload: AssetSelection, db: Session = Depends(get_db)):
    """The owner's selection of a picture or clip: checked, it must be used 100 % in the film; a photo can also be given a
    draw priority 1 to 10 for the pencil drawing (a clip cannot be drawn, and two photos cannot share a priority)."""
    asset = db.get(VisualAsset, asset_id)
    if asset is None or asset.case_id != case_id:
        raise HTTPException(404, "No such picture in this case")
    must_use = payload.must_use if "must_use" in payload.model_fields_set else asset.must_use
    draw_priority = payload.draw_priority if "draw_priority" in payload.model_fields_set else asset.draw_priority
    draw_priority = draw_priority or None             # 0 means "not for drawing": the photo is unselected and its number is free
    if draw_priority is not None and "draw_priority" in payload.model_fields_set:
        if asset.asset_type != "photo":
            raise HTTPException(422, "Only a photo can be drawn")
        taken = (db.query(VisualAsset).filter(VisualAsset.case_id == case_id, VisualAsset.id != asset_id,
                                              VisualAsset.draw_priority == draw_priority).first())
        if taken is not None:
            raise HTTPException(409, f"Priority {draw_priority} already belongs to {taken.asset_code}; give that one another first")
    asset.must_use, asset.draw_priority = must_use, draw_priority
    db.commit()
    return {"asset_id": asset.id, "must_use": asset.must_use, "draw_priority": asset.draw_priority}


def reject_scope(db: Session, case_id: int, cands: list[MvpCandidate]) -> list[MvpCandidate]:
    """The candidates a rejection covers: all of the case's candidates of the rejected pictures."""
    assets = {c.visual_asset_id for c in cands}
    return (db.query(MvpCandidate).join(MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id)
            .filter(MvpCandidate.visual_asset_id.in_(assets), MvpRequirement.case_id == case_id).all())


class GroupDecision(BaseModel):
    candidate_ids: list[int]
    decision: str
    note: str | None = None
    override_rights: bool = False       # approve although the rights check blocked it: the owner's own decision


@router.post("/api/cases/{case_id}/mvp/candidates/decision")
def decide_group(case_id: int, payload: GroupDecision, db: Session = Depends(get_db)):
    """One decision for every candidate of a picture (the ids a grouped listing gives). Candidates whose rights block
    the picture are skipped on approval and named in the answer; nothing outside this case is touched."""
    if payload.decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    cands = (db.query(MvpCandidate).join(MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id)
             .filter(MvpCandidate.id.in_(payload.candidate_ids), MvpRequirement.case_id == case_id).all())
    if not cands:
        raise HTTPException(404, "No such candidates in this case")
    if payload.decision == "reject":      # a rejected picture is rejected for every person and requirement it was offered for
        cands = reject_scope(db, case_id, cands)
    skipped = [c.id for c in cands if payload.decision == "approve" and c.rights_status == "blocked"
               and not payload.override_rights]
    if len(skipped) == len(cands):
        raise HTTPException(409, "Rights block this picture; confirm to approve it on your own decision")
    for cand in cands:
        if cand.id not in skipped:
            apply_decision(db, cand, payload.decision, payload.note, payload.override_rights)
    db.commit()
    VF.sync_asset_status(db, case_id)
    return {"decided": len(cands) - len(skipped), "skipped": skipped, "decision": payload.decision}


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
    needs = db.get(VisualPlan, json.loads(plan.validation_json)["mvp"].get("needs_plan_id") or 0)
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
            "neighbour_cover": {"approved": bool(needs) and DR.cover_approved(needs),
                                "covered": report.get("neighbour_cover") or []},
            "beats": [{"beat_id": b, **open_by_beat.get(b, {"needs_review": 0, "rejected": 0})}
                      for b in sorted({g["beat"] for g in gaps})]}


class NeighbourCoverDecision(BaseModel):
    approved: bool


@router.post("/api/cases/{case_id}/mvp/neighbour-cover")
def decide_neighbour_cover(case_id: int, payload: NeighbourCoverDecision, db: Session = Depends(get_db)):
    """The owner's decision on the last resort for moments that stay bare after every alternative: cover each from
    its neighbours (the picture before and after, half each, or a photo that spoils nothing when a neighbour is not a
    still). It applies the next time the film is directed (Resume)."""
    needs = needs_plan_of(db, case_id)
    validation = json.loads(needs.validation_json or "{}")
    validation.setdefault("mvp", {})["neighbour_cover"] = {
        "approved": payload.approved, "decided_at": datetime.now().astimezone().isoformat()}
    needs.validation_json = json.dumps(validation, ensure_ascii=False)
    db.commit()
    return validation["mvp"]["neighbour_cover"]
