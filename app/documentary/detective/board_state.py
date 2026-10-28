"""Section E: surface Section B's knowledge-event state onto the detective board.

A board card for an open question should show the question's actual current status (open,
partial, answered, challenged, explicitly_unresolved) as of the beat where the card appears, not
a permanent "new" label - the question may already have been answered or reframed by an earlier
beat's knowledge event. `replay_state` only ever reads events up to a given order_seq, so a later
answer can never leak into an earlier card's status.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import DetectiveKnowledgeEvent, NarrationUnit, ReleaseAnchor
from app.documentary.memory import replay_state

DEFAULT_QUESTION_STATUS = "open"


def order_seq_as_of_story_order(db: Session, graph_id: int, story_order: int) -> int | None:
    """The latest knowledge-event order_seq anchored at or before `story_order` narration order.
    None when no qualifying event exists yet."""
    row = (
        db.query(DetectiveKnowledgeEvent.order_seq)
        .join(ReleaseAnchor, DetectiveKnowledgeEvent.anchor_id == ReleaseAnchor.id)
        .join(NarrationUnit, ReleaseAnchor.narration_unit_id == NarrationUnit.id)
        .filter(DetectiveKnowledgeEvent.graph_id == graph_id, NarrationUnit.story_order <= story_order)
        .order_by(DetectiveKnowledgeEvent.order_seq.desc())
        .first()
    )
    return row[0] if row else None


def question_status_as_of(db: Session, graph_id: int, question_key: str, story_order: int) -> str:
    """The question's displayed status as of this narration position - open until a knowledge
    event anchored at or before `story_order` says otherwise."""
    up_to = order_seq_as_of_story_order(db, graph_id, story_order)
    state = replay_state(db, graph_id, up_to_order_seq=up_to)
    record = state["questions"].get(question_key)
    if record is None:
        return DEFAULT_QUESTION_STATUS
    return record["state"].get("status", DEFAULT_QUESTION_STATUS)
