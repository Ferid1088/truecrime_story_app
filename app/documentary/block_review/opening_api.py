"""The routes of the hand review of the film's opening block (documentary > Voice review > Opening review, and for Persian Farsi review > Opening (hook)): the blocks are its hook and its context text. The review ends with the owner's word: he approves the opening (the voice and the words are final) or takes the approval back; any change reopens it."""

from __future__ import annotations

from app.documentary.block_review import opening_review as OR
from app.documentary.block_review.routes import build_router

BASE = "opening-voice-review"
router = build_router(BASE, OR.OPENING, OR, OR.parts_of, finishable=True)
