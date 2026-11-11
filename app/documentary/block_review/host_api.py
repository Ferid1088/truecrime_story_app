"""The routes of the hand review of the host's voice (documentary > Voice review > Host voice review): the blocks are the host's scenes of a version."""

from __future__ import annotations

from app.documentary.block_review import host_review as HR
from app.documentary.block_review.routes import build_router

BASE = "host-voice-review"
router = build_router(BASE, HR.HOST, HR, HR.scenes_of)
