"""Whether the card of a directed action can really be made, and how much of the film the made cards fill.

The Director chooses a tool and binds claims; the card is built from those claims (`dispatch.BUILDERS`). An action
whose builder finds nothing to show is not a plan: it goes back to the Director with what the card needs. Cards
the toolkit has made are summed against the film's length, so the owner sees the share of the film that the
alternatives fill."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import DetectiveScene, DetectiveToolAction
from app.documentary.detective import dispatch as D
from app.documentary.detective.reading import reading_need
from app.documentary.graphics import shots as GS
from app.documentary.graphics.base import GraphicUnavailable

NEEDS = {
    "conflict": "bind a relation of type conflict between exactly two claims, and bind both claims",
    "quote": "bind an attributed claim (with its attribution, at most 220 characters) from a listed newspaper "
             "or TV broadcaster",
    "courtroom": "bind claims whose attribute is issue, prosecution, defense and finding",
    "sentencing": "bind the claims the sentencing card shows (the court's finding and the terms)",
    "interrogation": "bind the claims the interrogation card shows (who asked, what was answered)",
    "evidence_check": "bind at least one established_in_text claim about the item",
    "recap": "bind claims of at most 38 characters, by basis (established, disputed, unknown)",
    "map": "bind a claim about a stored place or building",
}
DEFAULT_NEED = "bind the claims, questions or relations the card shows"


def issue_of(db: Session, action: DetectiveToolAction) -> str | None:
    """Why this action's card cannot be built, or None when it can (or when the tool is not a card)."""
    if action.tool_id == "map":
        return None if D._map_place_of(db, action) else f"the map has no stored place: {NEEDS['map']}"
    build = D.BUILDERS.get(action.tool_id)
    if build is None or action.tool_id == "detective_board":
        return None
    if build(db, action) is None:
        return (f"action {action.action_key} ({action.tool_id}) cannot be built from what it binds: "
                f"{NEEDS.get(action.tool_id, DEFAULT_NEED)}; or choose a tool whose card can be built")
    return None


def unbuildable(db: Session, graph_id: int, action_keys: set[str] | None = None) -> list[str]:
    query = (db.query(DetectiveToolAction).join(DetectiveScene, DetectiveScene.id == DetectiveToolAction.scene_id)
             .filter(DetectiveScene.graph_id == graph_id))
    return [issue for action in query if (action_keys is None or action.action_key in action_keys)
            for issue in [issue_of(db, action)] if issue]


def card_seconds(db: Session, action: DetectiveToolAction, language: str) -> float:
    """Seconds the action's card is held: the time its text needs to be read; a board or map its fixed hold."""
    if action.tool_id == "map":
        return GS.hold_seconds("map") if D._map_place_of(db, action) else 0.0
    build = D.BUILDERS.get(action.tool_id)
    if build is None or action.tool_id == "detective_board":      # a board is timed by its own placement
        return 0.0
    data = build(db, action)
    if data is None:
        return 0.0
    try:
        return reading_need(action.tool_id, data, language).planned
    except GraphicUnavailable:
        return GS.hold_seconds(action.tool_id)


def alternative_seconds(db: Session, graph_id: int, language: str) -> float:
    query = (db.query(DetectiveToolAction).join(DetectiveScene, DetectiveScene.id == DetectiveToolAction.scene_id)
             .filter(DetectiveScene.graph_id == graph_id))
    return round(sum(card_seconds(db, a, language) for a in query), 1)


def narration_seconds(units) -> dict[str, float]:
    """Seconds of narration per beat, from its words at the speech rate the reading rules use."""
    rate = ai_config.detective_toolkit.reading.dense_speech_words_per_second
    seconds: dict[str, float] = {}
    for unit in units:
        seconds[unit.beat_id] = seconds.get(unit.beat_id, 0.0) + len(unit.text.split()) / rate
    return seconds


def card_seconds_by_beat(db: Session, graph_id: int, language: str) -> dict[str, float]:
    query = (db.query(DetectiveToolAction).join(DetectiveScene, DetectiveScene.id == DetectiveToolAction.scene_id)
             .filter(DetectiveScene.graph_id == graph_id))
    seconds: dict[str, float] = {}
    for action in query:
        beat = D._beat_id_of(db, action)
        if beat is not None:
            seconds[beat] = seconds.get(beat, 0.0) + card_seconds(db, action, language)
    return seconds


def alternative_share(db: Session, graph_id: int, language: str, units) -> float:
    film = sum(narration_seconds(units).values())
    return round(sum(card_seconds_by_beat(db, graph_id, language).values()) / film, 3) if film else 0.0


def weakest_beats(db: Session, graph_id: int, language: str, units, count: int) -> list[str]:
    """The beats whose cards fill the smallest share of their own narration, weakest first."""
    narration, cards = narration_seconds(units), card_seconds_by_beat(db, graph_id, language)
    return sorted(narration, key=lambda b: (cards.get(b, 0.0) / narration[b], b))[:count]
