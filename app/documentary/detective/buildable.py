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
from app.documentary.detective import timeline as TL
from app.documentary.detective.reading import reading_need
from app.documentary.graphics import shots as GS
from app.documentary.graphics.base import GraphicUnavailable
from app.documentary.graphics.engines.evidence import EXAMS
from app.documentary.graphics.registry import graphic

NEEDS = {
    "fact_card": "bind at least one claim; each bound claim becomes one fact",
    "detective_board": "bind at least one claim; each bound claim becomes a card",
    "conflict": "bind a relation of type conflict between exactly two claims, and bind both claims",
    "quote": "bind an attributed claim (with its attribution, at most 220 characters) from a listed newspaper "
             "or TV broadcaster",
    "evidence_check": "bind at least one established_in_text claim about the item",
    "forensic": "bind claims with attribute exam, sample, method, result and limits; the exam claim's text is exactly one "
                "of: " + ", ".join(EXAMS),
    "recap": "bind claims of at most 38 characters, by basis (established, disputed, unknown)",
    "timeline": "bind at least two claims linked to facts that have a real event date",
    "document": "bind a claim whose text stands word for word in the stored text of an official source",
    "headline": "bind claims linked to a listed newspaper source that has a full date",
    "map": "bind a claim about a stored place or building",
}
DEFAULT_NEED = "bind the claims, questions or relations the card shows"


def issue_of(db: Session, action: DetectiveToolAction) -> str | None:
    """Why this action's card cannot be built, or None when it can (or when the tool is not a card)."""
    if action.tool_id == "map":
        return None if D._map_place_of(db, action) else f"the map has no stored place: {NEEDS['map']}"
    if D.BUILDERS.get(action.tool_id) is None or action.tool_id == "detective_board":
        return None
    data = D.build_data(db, action)
    if data is None:
        return (f"action {action.action_key} ({action.tool_id}) cannot be built from what it binds: "
                f"{NEEDS.get(action.tool_id, DEFAULT_NEED)}; or choose a tool whose card can be built")
    problem = render_problem(action.tool_id, data)
    if problem:
        return f"action {action.action_key} ({action.tool_id}) cannot be rendered: {problem}"
    try:
        reading_need(action.tool_id, data, "en")
    except GraphicUnavailable as e:
        return f"action {action.action_key} ({action.tool_id}) cannot be laid out to be read: {e}"
    return None


def render_problem(tool_id: str, data: dict) -> str | None:
    """What the card's renderer refuses in this data (too many facts, a text too long), or None. A tool with no
    renderer of its own (the board) has nothing to refuse."""
    try:
        return graphic(tool_id).can_render(data)
    except GraphicUnavailable:
        return None


def unbuildable(db: Session, graph_id: int, action_keys: set[str] | None = None) -> list[tuple[str, str]]:
    """(action key, why its card cannot be built) for every action of the graph that cannot be made."""
    query = (db.query(DetectiveToolAction).join(DetectiveScene, DetectiveScene.id == DetectiveToolAction.scene_id)
             .filter(DetectiveScene.graph_id == graph_id))
    return [(action.action_key, issue) for action in query if action_keys is None or action.action_key in action_keys
            for issue in [issue_of(db, action)] if issue]


def card_seconds(db: Session, action: DetectiveToolAction, language: str) -> float:
    """Seconds the action's card is held: the time its text needs to be read; a board or map its fixed hold."""
    if action.tool_id == "map":
        from app.documentary.mvp.direct import map_seconds

        return map_seconds() if D._map_place_of(db, action) else 0.0
    build = D.BUILDERS.get(action.tool_id)
    if build is None or action.tool_id == "detective_board":      # a board is timed by its own placement
        return 0.0
    data = build(db, action)
    if data is None or render_problem(action.tool_id, data):
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


def estimated_windows(units) -> list[dict]:
    """The film's beat windows as the narration alone gives them: every sentence lasts its words at the speech rate,
    one after the other, a beat runs from its first sentence to its last."""
    rate = ai_config.detective_toolkit.reading.dense_speech_words_per_second
    windows: list[dict] = []
    at = 0.0
    for unit in units:
        if not windows or windows[-1]["beat_id"] != unit.beat_id:
            windows.append({"beat_id": unit.beat_id, "start": at, "end": at, "sentences": []})
        seconds = len(unit.text.split()) / rate
        windows[-1]["sentences"].append({"start": at, "end": at + seconds, "speech": unit.text})
        windows[-1]["end"] = at + seconds
        at += seconds
    return windows


def planned_timeline(db: Session, graph_id: int, language: str, units, windows: list[dict] | None = None) -> TL.FilmTimeline:
    """The film timeline the cards would get: over the real beat windows of the narration when it exists, otherwise over
    the windows the text alone gives, with the detective board's moments kept free. It is the placement the Main Visual
    Planner will see (release points, reading times), not a sum of card seconds."""
    windows = windows or estimated_windows(units)
    return TL.film_timeline(windows, D.resolve_dispatchable_actions(db, graph_id), language,
                            TL.board_windows(db, graph_id, windows, language))


def placed_card_seconds_by_beat(timeline: TL.FilmTimeline) -> dict[str, float]:
    return {beat: sum(c["end"] - c["start"] for c in cards) for beat, cards in timeline.cards_by_beat.items()}


def alternative_share(db: Session, graph_id: int, language: str, units, windows: list[dict] | None = None) -> float:
    """The share of the film its cards fill once placed."""
    return planned_timeline(db, graph_id, language, units, windows).alternative_share


def weakest_beats(db: Session, graph_id: int, language: str, units, count: int) -> list[str]:
    """The beats whose cards fill the smallest share of their own narration, weakest first."""
    narration = narration_seconds(units)
    cards = placed_card_seconds_by_beat(planned_timeline(db, graph_id, language, units))
    return sorted(narration, key=lambda b: (cards.get(b, 0.0) / narration[b], b))[:count]
