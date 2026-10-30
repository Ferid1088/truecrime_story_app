"""The film's timeline cards, made from the case's dated events (the graph's timeline nodes) with no model call.

Events of the same date become one time point with all their texts; the time points of a card run strictly later. The events
are taken in the order the film tells them, a card per run of `timeline_events` dates, and a card is shown after the beat that
tells its last event; when that beat has no room, in the next beats (never before: what it shows has been told by then)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import DetectiveScene, DetectiveToolAction, NarrationUnit
from app.documentary.detective import buildable as BD
from app.documentary.detective import cards as CD
from app.documentary.graphics.timeline import date_key, merge_points

LATER_BEATS = 4


def groups_of(events: list[dict], dates_per_card: int) -> list[list[dict]]:
    """Runs of events (in the order the film tells them) of at most `dates_per_card` different dates each."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    seen: set[tuple[int, int, int]] = set()
    for e in events:
        key = date_key(e["date"])
        if key not in seen and len(seen) >= dates_per_card:
            groups.append(current)
            current, seen = [], set()
        current.append(e)
        seen.add(key)
    return groups + ([current] if current else [])


def build_timeline_cards(db: Session, graph_id: int, units: list[NarrationUnit], language: str) -> int:
    """Make the timeline cards once; returns how many the film has. A film with dated events always gets at least one when
    any beat from its last event on has room for it."""
    if (db.query(DetectiveToolAction).join(DetectiveScene, DetectiveScene.id == DetectiveToolAction.scene_id)
            .filter(DetectiveScene.graph_id == graph_id, DetectiveToolAction.tool_id == "timeline").first()):
        return 0
    cfg = ai_config.detective_toolkit
    events = [e for e in CD.timeline_events(db, graph_id) if e["beat"] in {u.beat_id for u in units}]
    groups = [g for g in groups_of(events, cfg.timeline_events) if len({date_key(e["date"]) for e in g}) >= 2]
    chosen = [groups[i] for i in sorted(CD.spread_order(len(groups))[:cfg.timeline_cards])]
    order = list(dict.fromkeys(u.beat_id for u in units))
    made = 0
    for n, group in enumerate(chosen, 1):
        data = {"points": merge_points(group)}
        last = max(order.index(e["beat"]) for e in group)
        for beat in order[last:last + 1 + LATER_BEATS]:
            keys = [u.unit_key for u in units if u.beat_id == beat]
            part = CD.Part(f"T{n}", keys, f"The dates of {len(data['points'])} events", ["timeline"])
            action = CD.store_card(db, graph_id, part, "timeline", data, units, language, 0, release=0.5)
            skipped = {s["action_key"] for s in BD.planned_timeline(db, graph_id, language, units).skipped}
            if BD.issue_of(db, action) is None and action.action_key not in skipped:
                made += 1
                break
            db.delete(action.scene)
            db.delete(action)
            db.flush()
    db.commit()
    return made
