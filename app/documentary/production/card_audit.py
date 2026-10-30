"""The planner's check of every card's time: a card (a graphic or the detective board) is held for its own length, within the
limits of its kind, and no other shot cuts into it. The render shows, at every moment, the shot that started last, so a shot that
starts inside a card cuts the card short (the board showed for 0.4 s) and a card stretched far beyond its limit hides the
pictures under it. This runs on the finished timeline (and again after the critic's fixes): a card too long is shortened to its
limit, a shot that starts inside a card starts when the card ends, a card that cannot have its minimum is taken out, and the
gaps this leaves are closed by the picture before them."""

from __future__ import annotations

from app.core.ai_config import ai_config
from app.documentary.graphics import theme as T

CARD_KINDS = ("graphic", "detective_board")
EPS = 1e-3


def limits(shot: dict) -> tuple[float, float]:
    """The shortest and the longest a card of this kind is held."""
    if shot["kind"] == "detective_board":
        board = ai_config.detective_board
        return board.min_card_seconds, board.hold_max_seconds
    timing = T.config().card_timing
    return timing["min_seconds"], timing["max_seconds"] + timing["extension_seconds"]


def _is_card(shot: dict) -> bool:
    return shot.get("kind") in CARD_KINDS


def _start_at(shot: dict, start: float) -> None:
    """The shot begins at `start` (a clip's own start moves with it)."""
    if shot.get("kind") == "video" and shot.get("clip_start") is not None:
        shot["clip_start"] = round(float(shot["clip_start"]) + max(start - float(shot["start"]), 0.0), 3)
    shot["start"] = round(start, 3)


def _restore_board_windows(shots: list[dict], board: list[dict]) -> list[dict]:
    """A detective board shot goes back to the window the planner gave its appearance (a later edit may have moved it)."""
    done = []
    left = [dict(b) for b in board]
    for shot in sorted((s for s in shots if s.get("kind") == "detective_board"), key=lambda s: s["start"]):
        match = next((b for b in left if b["beat_id"] == shot.get("beat_id")), None)
        if match is None:
            continue
        left.remove(match)
        if abs(shot["start"] - match["start"]) > EPS or abs(shot["end"] - match["end"]) > EPS:
            done.append({"fix": "board_window_restored", "kind": "detective_board", "start": match["start"],
                         "was": [round(shot["start"], 1), round(shot["end"], 1)]})
            shot["start"], shot["end"] = match["start"], match["end"]
    return done


def audit(shots: list[dict], board: list[dict] | None = None) -> list[dict]:
    """Make every card's time logical, in place. `board` is the planner's board appearances ({beat_id, start, end}).
    Returns what was changed: {"fix", "kind", "start", ...}."""
    done = _restore_board_windows(shots, board or [])
    shots.sort(key=lambda s: (s["start"], s["end"]))
    for card in [s for s in shots if _is_card(s)]:
        lo, hi = limits(card)
        if card["end"] - card["start"] > hi + EPS:
            done.append({"fix": "shortened", "kind": card["kind"], "start": card["start"],
                         "from": round(card["end"] - card["start"], 1), "to": hi})
            card["end"] = round(card["start"] + hi, 3)
    for card in [s for s in shots if _is_card(s)]:
        if card not in shots:
            continue
        for other in [o for o in shots if o is not card and card["start"] - EPS <= o["start"] < card["end"] - EPS
                      and (o["start"] > card["start"] + EPS or shots.index(o) > shots.index(card))]:
            if _is_card(other):                       # the render shows the card that started last: the first is cut at its start
                done.append({"fix": "cut_by_the_next_card", "kind": card["kind"], "start": card["start"],
                             "to": round(other["start"] - card["start"], 1)})
                card["end"] = round(other["start"], 3)
                break
            if other["end"] <= card["end"] + EPS:
                shots.remove(other)
                done.append({"fix": "removed_under_a_card", "kind": other["kind"], "start": other["start"],
                             "card": card["kind"]})
            else:
                _start_at(other, card["end"])
                done.append({"fix": "starts_after_the_card", "kind": other["kind"], "card": card["kind"],
                             "at": card["end"]})
    for card in [s for s in shots if _is_card(s)]:
        for earlier in [e for e in shots if not _is_card(e) and e["start"] < card["start"] - EPS
                        and card["start"] + EPS < e["end"]]:
            earlier["end"] = round(card["start"], 3)                  # a picture ends where the card begins
    for card in [s for s in shots if _is_card(s)]:
        if card["end"] - card["start"] < limits(card)[0] - EPS:
            shots.remove(card)
            done.append({"fix": "removed_too_short", "kind": card["kind"], "start": card["start"],
                         "seconds": round(card["end"] - card["start"], 1)})
    shots.sort(key=lambda s: (s["start"], s["end"]))
    for a, b in zip(shots, shots[1:], strict=False):
        if a["end"] < b["start"] - 0.01:
            if not _is_card(a):
                a["end"] = b["start"]
            elif not _is_card(b):
                _start_at(b, a["end"])
            else:                                          # two cards with a hole between them: the first is held longer
                a["end"] = round(min(b["start"], a["start"] + limits(a)[1]), 3)
                if a["end"] < b["start"] - 0.01:
                    done.append({"fix": "gap_between_cards", "start": a["end"], "seconds": round(b["start"] - a["end"], 1)})
    return done
