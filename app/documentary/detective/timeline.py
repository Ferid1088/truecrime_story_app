"""The film timeline the toolkit hands to the Main Visual Planner.

The toolkit has built every alternative card; here each card is given its place in the film: inside its own beat, after
the point where the narration has released the claim it shows, held for the time its text needs to be read. What is
left of every beat is a free slot. The Main Visual Planner fills the free slots with photos and videos and may not move,
shorten or remove a card.

    slots = film_timeline(windows, resolved, language)
    slots.cards_by_beat["B05"]       # fixed card slots of the beat: start, end, the shot fields
    slots.free_by_beat["B05"]        # (start, end) stretches the planner fills
    slots.alternative_share          # seconds the cards fill over the film's length
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.ai_config import ai_config
from app.documentary.detective import reading as R
from app.documentary.graphics import shots as GS
from app.documentary.graphics.base import GraphicUnavailable

MIN_FREE_SECONDS = 0.04     # a stretch shorter than this is not a slot (a rounding sliver)


@dataclass
class FilmTimeline:
    cards_by_beat: dict[str, list[dict]] = field(default_factory=dict)
    free_by_beat: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    skipped: list[dict] = field(default_factory=list)
    film_seconds: float = 0.0
    card_seconds: float = 0.0
    board: list[dict] = field(default_factory=list)       # the moments the detective board is on screen

    @property
    def board_seconds(self) -> float:
        return sum(b["end"] - b["start"] for b in self.board)

    @property
    def alternative_share(self) -> float:
        """The share of the film its cards, maps and the detective board fill."""
        return round((self.card_seconds + self.board_seconds) / self.film_seconds, 3) if self.film_seconds else 0.0

    @property
    def kinds(self) -> set[str]:
        """The kinds of card on the film: every tool placed, and the detective board when it is on screen."""
        used = {c["tool_id"] for cards in self.cards_by_beat.values() for c in cards}
        return used | ({"detective_board"} if self.board else set())

    @property
    def free_seconds(self) -> float:
        return round(sum(b - a for stretches in self.free_by_beat.values() for a, b in stretches), 1)


def _speech(window: dict) -> list[dict]:
    return [{"start": s["start"], "end": s["end"], "said": s.get("speech", "")} for s in window["sentences"]]


def _clear_of(start: float, hold: float, board: list[dict]) -> float:
    """`start` moved after any board appearance the card would overlap: the board is never drawn over a card."""
    for b in sorted(board, key=lambda b: b["start"]):
        if start < b["end"] and start + hold > b["start"]:
            start = b["end"]
    return start


def _place(window: dict, record: dict, cursor: float, language: str,
           board: list[dict]) -> tuple[dict | None, dict | None]:
    """(card slot, skip record) for one action in its beat, starting no earlier than `cursor`."""
    length = window["end"] - window["start"]
    start = max(cursor, window["start"] + length * (record.get("min_release_fraction") or 0.0))
    if record["tool_id"] == "map":
        from app.documentary.mvp.direct import map_seconds

        hold = map_seconds()
        start = _clear_of(start, hold, board)
        if window["end"] - start < hold:
            return None, {"action_key": record["action_key"], "reason": "insufficient_reading_time",
                          "needed_seconds": round(hold, 2), "available_seconds": round(window["end"] - start, 2)}
        return ({"kind": "map", "beat_id": record["beat_id"], "start": round(start, 3), "end": round(start + hold, 3),
                 "tool_id": "map", "action_key": record["action_key"], "why": record["purpose"],
                 "place": record["data"]["place"]}, None)
    try:
        need = R.reading_need(record["tool_id"], record["data"], language)
    except GraphicUnavailable as e:
        return None, {"action_key": record["action_key"], "reason": f"unrenderable: {e}"}
    start = _clear_of(start, min(need.planned, window["end"] - start), board)
    remaining = window["end"] - start
    if remaining < need.fastest:
        return None, {"action_key": record["action_key"], "reason": "insufficient_reading_time",
                      "needed_seconds": round(need.fastest, 2), "available_seconds": round(remaining, 2)}
    hold = min(need.planned, remaining)
    competing = R.competing_speech(need.words, record["data"], _speech(window), start, start + hold)
    slot = {"kind": "card", "beat_id": record["beat_id"], "start": round(start, 3), "end": round(start + hold, 3),
            "tool_id": record["tool_id"], "action_key": record["action_key"], "why": record["purpose"],
            "reuse_reason": record.get("reuse_reason"), **GS.plan_fields(record["tool_id"], record["data"])}
    slot["graphic"]["language"] = language
    if hold < need.planned:
        slot["compressed"] = {"planned_seconds": round(need.planned, 2), "held_seconds": round(hold, 2)}
    if competing:
        slot["competing_speech"] = competing
    return slot, None


def film_timeline(windows: list[dict], resolved: list[dict], language: str,
                  board: list[dict] | None = None) -> FilmTimeline:
    """Place every buildable card of `resolved` (the dispatchable actions, in the toolkit's order) in its beat window.
    Cards of a beat are placed one after the other and never overlap; a card with no room is reported, not squeezed."""
    timeline = FilmTimeline(board=list(board or []))
    by_beat = {w["beat_id"]: w for w in windows}
    cursor: dict[str, float] = {}
    for record in resolved:
        if record.get("tool_id") == "detective_board":      # a note for the board, which is scheduled on its own
            continue
        window = by_beat.get(record.get("beat_id"))
        if "skip" in record or window is None:
            if "skip" in record or record.get("beat_id") is not None:
                timeline.skipped.append({"action_key": record.get("action_key"),
                                         "reason": record.get("skip") or "beat_not_in_timeline"})
            continue
        slot, skip = _place(window, record, cursor.get(window["beat_id"], window["start"]), language, timeline.board)
        if skip:
            timeline.skipped.append(skip)
            continue
        timeline.cards_by_beat.setdefault(window["beat_id"], []).append(slot)
        cursor[window["beat_id"]] = slot["end"]
    for window in windows:
        timeline.film_seconds += window["end"] - window["start"]
        edge, free = window["start"], []
        for slot in timeline.cards_by_beat.get(window["beat_id"], []):
            if slot["start"] - edge > MIN_FREE_SECONDS:
                free.append((round(edge, 3), slot["start"]))
            edge = slot["end"]
            timeline.card_seconds += slot["end"] - slot["start"]
        if window["end"] - edge > MIN_FREE_SECONDS:
            free.append((round(edge, 3), window["end"]))
        timeline.free_by_beat[window["beat_id"]] = free
    return timeline


def timeline_for(db, graph_id: int, manifest: dict, language: str, top_up: bool = False) -> FilmTimeline:
    """The film timeline of a graph over this language's real beat windows: the toolkit's cards where it has planned
    the graph, otherwise every beat free. With `top_up` (the reference language, whose narration now exists) the
    toolkit first adds fact cards until its cards fill the configured share of the film on these real windows: the
    text alone only estimated them. The other languages reuse the cards and never add any."""
    from app.db.models import NarrationUnit
    from app.documentary.detective import filler as FL
    from app.documentary.detective.dispatch import has_detective_plan, resolve_dispatchable_actions
    from app.documentary.mvp.direct import beat_windows

    windows = beat_windows(manifest)
    planned = has_detective_plan(db, graph_id)
    if planned and top_up and ai_config.detective_toolkit.enabled:
        units = db.query(NarrationUnit).filter_by(graph_id=graph_id).order_by(NarrationUnit.story_order).all()
        FL.fill_with_fact_cards(db, graph_id, language, units, windows)
    return film_timeline(windows, resolve_dispatchable_actions(db, graph_id) if planned else [], language,
                         board_windows(db, graph_id, windows, language))


def board_windows(db, graph_id: int, windows: list[dict], language: str) -> list[dict]:
    """The moments the detective board is on screen in this film (the blueprint's board plan, scheduled like the
    production script pins it); none when the blueprint has no board plan with cards."""
    from app.db.models import RevealGraph
    from app.documentary.detective_board import board_cards_for, board_schedule, latest_board_plan

    graph = db.get(RevealGraph, graph_id)
    plan = latest_board_plan(db, graph.blueprint_id) if graph is not None else None
    cards = (board_cards_for(plan, language) or {}).get("cards") if plan is not None else None
    if not cards:
        return []
    spans = [{"beat_id": w["beat_id"], "start": w["start"], "end": w["end"]} for w in windows]
    return board_schedule(cards, spans)
