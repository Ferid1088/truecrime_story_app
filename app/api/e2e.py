"""Small, fail-closed fixtures used only by the isolated browser stack."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from app.db.base import SessionLocal

router = APIRouter(prefix="/api/e2e", tags=["e2e"])


@router.post("/reset-stage-control")
def reset_stage_control() -> dict:
    """Reset the stage-control fixture before a real-stack browser test.

    The route is mounted only when the no-network E2E environment is active;
    the seed script applies the second, database-name guard.
    """
    if os.environ.get("TC_TEST_NO_NETWORK") != "1":
        raise HTTPException(status_code=404, detail="E2E fixtures are disabled")
    from scripts.seed_stage_control_e2e import main

    main()
    with SessionLocal() as db:
        return {"status": "reset", "database": db.get_bind().url.database}


@router.post("/reset-approval-flow")
def reset_approval_flow() -> dict:
    if os.environ.get("TC_TEST_NO_NETWORK") != "1":
        raise HTTPException(status_code=404, detail="E2E fixtures are disabled")
    from scripts.seed_approval_e2e import main

    main()
    with SessionLocal() as db:
        case = db.query(__import__("app.db.models", fromlist=["Case"]).Case).first()
        return {"status": "reset", "database": db.get_bind().url.database,
                "case_id": case.id if case else None}


@router.post("/reset-shorts-flow")
def reset_shorts_flow() -> dict:
    if os.environ.get("TC_TEST_NO_NETWORK") != "1":
        raise HTTPException(status_code=404, detail="E2E fixtures are disabled")
    from scripts.seed_approval_e2e import main

    main(include_short_languages=True)
    with SessionLocal() as db:
        case = db.query(__import__("app.db.models", fromlist=["Case"]).Case).first()
        return {"status": "reset", "database": db.get_bind().url.database,
                "case_id": case.id if case else None}
