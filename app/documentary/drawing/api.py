"""The portraits of a case for the owner: how many the planner needs, which photos, and the button that makes them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Case, VisualAsset
from app.documentary.drawing import service as S

router = APIRouter(tags=["drawing"])


def overview(db: Session, case_id: int) -> dict:
    record = S.latest_record(db, case_id)
    chosen = {a.asset_code: a for a in db.query(VisualAsset).filter(
        VisualAsset.case_id == case_id, VisualAsset.draw_priority.isnot(None)).order_by(VisualAsset.draw_priority).all()}
    needed = [o["code"] for o in (record or {}).get("order") or []]

    def row(asset: VisualAsset, in_plan: bool) -> dict:
        st = S.state_of(asset)
        return {"asset_id": asset.id, "code": asset.asset_code, "priority": asset.draw_priority, "title": asset.title,
                "thumb_url": f"/api/visuals/{asset.id}/file?thumb=1", "needed": in_plan, "status": st["status"],
                "progress": st.get("progress", 0.0), "error": st.get("error")}

    rows = [row(chosen[c], True) for c in needed if c in chosen] + [row(a, False) for c, a in chosen.items() if c not in needed]
    return {"planned": record is not None, "needed": len(needed), "speed": (record or {}).get("speed"),
            "uncovered": (record or {}).get("uncovered") or [], "chosen": len(chosen), "portraits": rows,
            "ready": sum(1 for r in rows if r["needed"] and r["status"] == "ready"),
            "drawing": sum(1 for r in rows if r["status"] == "drawing")}


@router.get("/api/cases/{case_id}/drawing")
def drawing_overview(case_id: int, db: Session = Depends(get_db)):
    if db.get(Case, case_id) is None:
        raise HTTPException(404, "Case not found")
    return overview(db, case_id)


@router.post("/api/cases/{case_id}/drawing/create")
def create_portraits(case_id: int, db: Session = Depends(get_db)):
    """Make the portraits the planner needs (the photos you chose, in priority order), in the background."""
    if db.get(Case, case_id) is None:
        raise HTTPException(404, "Case not found")
    current = overview(db, case_id)
    if not current["planned"]:
        raise HTTPException(409, "The planner has not worked out how many portraits the film needs yet: run the planning job first.")
    if current["uncovered"]:
        raise HTTPException(409, "A gap of the film has no portrait that may be shown: choose another first photo before the portraits are made.")
    for r in current["portraits"]:
        if r["needed"]:
            S.start(db.get(VisualAsset, r["asset_id"]))
    return overview(db, case_id)
