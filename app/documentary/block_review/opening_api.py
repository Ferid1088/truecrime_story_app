"""The routes of the hand review of the film's opening block (documentary > Voice review > Opening review): the blocks are its hook and its context text."""

from __future__ import annotations

from app.documentary.block_review import opening_review as OR
from app.documentary.block_review.routes import build_router

BASE = "opening-voice-review"
router = build_router(BASE, OR.OPENING, OR, OR.parts_of)
