"""TrueCrime Story Studio API: app, lifespan, CORS and the routers."""

import logging
import os

if os.environ.get("TC_TEST_NO_NETWORK") == "1":
    from app.core.network_guard import install as _install_test_network_guard

    _install_test_network_guard()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.policy import PolicyError, policy_exception_handler
from app.db.schema import init_schema

init_schema()

from app.core import observability  # noqa: E402

observability.setup()


@asynccontextmanager
async def _lifespan(_app):
    """Recover interrupted work on startup and run the lifecycle scheduler."""
    from app.db.base import SessionLocal
    from app.documentary.recovery import recover_after_restart
    from app.lifecycle import scheduler
    from app.shortform.export import ffmpeg_caption_capability

    caption_capability = ffmpeg_caption_capability()
    if not caption_capability["available"]:
        logging.getLogger(__name__).warning(
            "Shorts disabled for production renders: %s", caption_capability["reason"]
        )

    db = SessionLocal()
    try:
        from app.core.config import settings
        from app.services.background_jobs import recover_after_restart

        recover_after_restart(db, include_documentary_jobs=settings.job_runner != "external")
    except Exception as e:  # never block the app from starting
        logging.getLogger(__name__).error("startup recovery failed: %s", e)
    finally:
        db.close()
    observability.purge_old_calls()
    from app import backup as backup_service

    db = SessionLocal()
    try:
        backup_service.recover_interrupted(db)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).error("backup recovery failed: %s", e)
    finally:
        db.close()
    scheduler.start()
    backup_service.start()
    try:
        yield
    finally:
        backup_service.stop()
        scheduler.stop()


app = FastAPI(
    lifespan=_lifespan,
    title="TrueCrime Story Studio",
    version="1.0.0",
    description="Research -> Facts -> Contradictions -> Story Direction -> Writing -> Critique",
)
app.add_exception_handler(PolicyError, policy_exception_handler)  # type: ignore[arg-type]

from app.api import (  # noqa: E402
    background_jobs,
    cases,
    costs,
    discovery,
    masters,
    ops,
    research,
    stories,
    system,
)
from app.api import backup as backup_api  # noqa: E402

if os.environ.get("TC_TEST_NO_NETWORK") == "1":
    from app.api import e2e  # noqa: E402
from app.documentary.api import router as documentary_router  # noqa: E402
from app.documentary.studio_api import router as studio_router  # noqa: E402
from app.lifecycle.api import router as lifecycle_router  # noqa: E402
from app.longform.api import router as longform_router  # noqa: E402
from app.naming.api import router as naming_router  # noqa: E402
from app.shortform.api import router as shortform_router  # noqa: E402
from app.thumbnails.api import router as thumbnail_router  # noqa: E402

# The stage routers first (same precedence as before the split), then the case-level API.
for _router in (
    *( (e2e.router,) if os.environ.get("TC_TEST_NO_NETWORK") == "1" else () ),
    background_jobs.router,
    documentary_router,
        lifecycle_router,
        longform_router,
        naming_router,
    thumbnail_router,
    studio_router,
    shortform_router,
    system.router,
    costs.router,
    discovery.router,
    cases.router,
    research.router,
    stories.router,
    masters.router,
    ops.router,
    backup_api.router,
):
    app.include_router(_router)

from app.core import security  # noqa: E402

app.include_router(security.router)
# Added first = inside CORS, so even a 401 carries the CORS headers the browser needs to read it.
app.add_middleware(security.SecurityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(security.allowed_origins()),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
# Last added = outermost: the request id and the access log cover everything, security answers included.
app.add_middleware(observability.RequestContextMiddleware)
