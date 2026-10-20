"""Small, fail-closed fixtures used only by the isolated browser stack."""

from __future__ import annotations

import os
import time

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
        return {"status": "reset", "database": db.get_bind().engine.url.database}


@router.post("/reset-approval-flow")
def reset_approval_flow() -> dict:
    if os.environ.get("TC_TEST_NO_NETWORK") != "1":
        raise HTTPException(status_code=404, detail="E2E fixtures are disabled")
    # A prior browser page can still be finishing a read while Playwright
    # starts the next test. PostgreSQL's TRUNCATE needs an AccessExclusiveLock
    # on every business table, so retry the disposable fixture reset after a
    # transient deadlock instead of leaking a false test failure.
    from sqlalchemy.exc import OperationalError

    from scripts.seed_approval_e2e import main

    for attempt in range(3):
        try:
            main()
            break
        except OperationalError as exc:
            if "deadlock detected" not in str(exc).lower() or attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    with SessionLocal() as db:
        case = db.query(__import__("app.db.models", fromlist=["Case"]).Case).first()
        return {"status": "reset", "database": db.get_bind().engine.url.database,
                "case_id": case.id if case else None}


@router.post("/reset-shorts-flow")
def reset_shorts_flow() -> dict:
    if os.environ.get("TC_TEST_NO_NETWORK") != "1":
        raise HTTPException(status_code=404, detail="E2E fixtures are disabled")
    from scripts.seed_approval_e2e import main

    main(include_short_languages=True)
    with SessionLocal() as db:
        case = db.query(__import__("app.db.models", fromlist=["Case"]).Case).first()
        return {"status": "reset", "database": db.get_bind().engine.url.database,
                "case_id": case.id if case else None}
