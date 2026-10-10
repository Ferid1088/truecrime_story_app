"""TrueCrime Story Studio API: app, lifespan, CORS and the routers."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.schema import init_schema

init_schema()


@asynccontextmanager
async def _lifespan(_app):
    """Recover interrupted work on startup and run the lifecycle scheduler."""
    from app.documentary.recovery import recover_after_restart
    from app.lifecycle import scheduler

    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        recover_after_restart(db)
    except Exception as e:  # never block the app from starting
        logging.getLogger(__name__).error("startup recovery failed: %s", e)
    finally:
        db.close()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()


app = FastAPI(
    lifespan=_lifespan,
    title="TrueCrime Story Studio",
    version="1.0.0",
    description="Research -> Facts -> Contradictions -> Story Direction -> Writing -> Critique",
)

from app.api import cases, discovery, masters, research, stories, system  # noqa: E402
from app.documentary.api import router as documentary_router  # noqa: E402
from app.documentary.studio_api import router as studio_router  # noqa: E402
from app.lifecycle.api import router as lifecycle_router  # noqa: E402
from app.longform.api import router as longform_router  # noqa: E402
from app.naming.api import router as naming_router  # noqa: E402
from app.shortform.api import router as shortform_router  # noqa: E402
from app.thumbnails.api import router as thumbnail_router  # noqa: E402

# The stage routers first (same precedence as before the split), then the case-level API.
for _router in (
    documentary_router,
        lifecycle_router,
        longform_router,
        naming_router,
    thumbnail_router,
    studio_router,
    shortform_router,
    system.router,
    discovery.router,
    cases.router,
    research.router,
    stories.router,
    masters.router,
):
    app.include_router(_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
