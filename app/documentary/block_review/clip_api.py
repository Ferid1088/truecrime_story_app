"""The routes of the hand review of the short clips (documentary > Voice review > Clips review): the blocks are the parts of the clips of a version."""

from __future__ import annotations

from app.documentary.block_review import clip_review as CR
from app.documentary.block_review.routes import build_router

BASE = "clip-voice-review"
router = build_router(BASE, CR.CLIPS, CR, CR.parts_of, finishable=True)
