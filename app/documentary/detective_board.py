"""The detective board: a growing road of short-title cards, one per key
narrated fact, pasted onto the board as the film reaches it and never
forgotten afterwards. Selection is deterministic (this module); the
short native-language titles come from one writer-agent call per
language (app/agents/detective_board.py). No picture, no paid provider
call besides that one text call — the board is drawn by our own code
(app/documentary/render/board_cards.py) from facts the script already
contains.

A card is only made from a fact the blueprint itself states (a dated
event the story tells, a beat that introduces a person or place, a
contradiction, or a question the story opens and never answers) — never
a new claim. The selection is language-neutral and stored once per
blueprint (BoardPlan); only the titles vary per language.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import BoardPlan

# A beat not already a dated event and not an unanswered open question
# gets a card only when its purpose maps to one of the owner's kinds;
# "transition"/"recovery"/"orientation" without a place focus and so on
# stay un-carded (not every sentence is a turning point).
KIND_BY_PURPOSE = {
    "human_introduction": "person",
    "orientation": "place",
    "evidence": "object",
    "contradiction": "contradiction",
    "investigation": "event",
    "reveal": "event",
    "chapter_end": "event",
}


def latest_board_plan(db: Session, blueprint_id: int | None) -> BoardPlan | None:
    if not blueprint_id:
        return None
    return (db.query(BoardPlan).filter(BoardPlan.blueprint_id == blueprint_id)
            .order_by(BoardPlan.id.desc()).first())


def plan_covers(row: BoardPlan | None, languages: list[str]) -> bool:
    if row is None:
        return False
    have = json.loads(row.languages_json or "[]")
    return set(languages) <= set(have)


def _beat_order(blueprint: dict) -> list[str]:
    return [b["id"] for b in blueprint.get("beats") or []]


def _open_questions_never_answered(blueprint: dict) -> dict[str, str]:
    """question id -> the beat that opened it, for questions no beat ever
    answers or marks unresolved (the story simply never returns to them —
    still worth a card: "open question" is an honest status)."""
    opened: dict[str, str] = {}
    closed: set[str] = set()
    for b in blueprint.get("beats") or []:
        for qid in b.get("opens") or []:
            opened.setdefault(qid, b["id"])
        closed |= set(b.get("answers") or []) | set(b.get("unresolved") or [])
    return {qid: beat for qid, beat in opened.items() if qid not in closed}


def select_cards(blueprint: dict, told_events: list[dict],
                 card_overrides: dict[str, dict] | None = None) -> list[dict]:
    """The language-neutral selection: [{id, kind, source_fact, beat_id,
    status, links}], in the order the story tells them. One card per
    beat at most (a beat that both reveals a dated event and is a
    contradiction gets the dated-event card — dates are the stronger,
    more concrete claim, UNLESS `card_overrides` names a different kind
    for that beat: Section D, obstacle 6 — the Detective Director decided
    what that beat's card should show). Honors config min/max cards and
    the minimum beat gap between two selected beats."""
    card_overrides = card_overrides or {}
    cfg = ai_config.detective_board
    order = _beat_order(blueprint)
    pos = {b: i for i, b in enumerate(order)}
    purposes = {b["id"]: b.get("purpose") for b in blueprint.get("beats") or []}
    by_fact_beat = {e["first_beat"]: e for e in told_events}
    unanswered = _open_questions_never_answered(blueprint)
    opened_by_beat: dict[str, list[str]] = {}
    for qid, beat in unanswered.items():
        opened_by_beat.setdefault(beat, []).append(qid)

    candidates: list[dict] = []
    for bid in order:
        if bid in card_overrides:
            candidates.append({**card_overrides[bid], "beat_id": bid})
            continue
        event = by_fact_beat.get(bid)
        if event is not None:
            candidates.append({"kind": "time", "source_fact": event["id"], "beat_id": bid})
            continue
        if bid in opened_by_beat:
            candidates.append({"kind": "open_question", "source_fact": opened_by_beat[bid][0],
                               "beat_id": bid})
            continue
        kind = KIND_BY_PURPOSE.get(str(purposes.get(bid)))
        if kind is not None:
            candidates.append({"kind": kind, "source_fact": bid, "beat_id": bid})

    # minimum beat gap between two selected beats
    picked: list[dict] = []
    last_pos = -(10 ** 9)
    for c in candidates:
        p = pos[c["beat_id"]]
        if picked and p - last_pos < cfg.min_beats_between_cards:
            continue
        picked.append(c)
        last_pos = p
    # cap at max_cards, spreading across the film rather than truncating
    # the tail (every part of the story may earn a card)
    if len(picked) > cfg.max_cards:
        step = len(picked) / cfg.max_cards
        picked = [picked[int(i * step)] for i in range(cfg.max_cards)]
    picked = _reach_the_end(picked, candidates, order, pos, purposes)

    return _linked_cards(picked, blueprint)


def _tail_start(n: int) -> int:
    """The first beat of the final stretch (at latest the last beat that still has room)."""
    return min(int(n * (1.0 - ai_config.detective_board.final_stretch_share)), max(n - 2, 0))


def _reach_the_end(picked: list[dict], candidates: list[dict], order: list[str], pos: dict[str, int],
                   purposes: dict[str, str | None]) -> list[dict]:
    """The board is planned to the end of the case: a card lies in the last share of the beats.
    When the selection stops earlier, the latest card-worthy beat of the tail is added (replacing the
    last card when the maximum is reached), else the last beat that still has room, as an event note
    of what that beat tells."""
    cfg = ai_config.detective_board
    n = len(order)
    if n < 3 or not picked:
        return picked
    last_room = n - 2
    tail_start = _tail_start(n)
    if any(tail_start <= pos[c["beat_id"]] <= last_room for c in picked):
        return picked
    tail = [c for c in candidates if tail_start <= pos[c["beat_id"]] <= last_room]
    extra = max(tail, key=lambda c: pos[c["beat_id"]]) if tail else {
        "kind": "event", "source_fact": order[last_room], "beat_id": order[last_room]}
    if len(picked) >= cfg.max_cards:
        picked = picked[:cfg.max_cards - 1]
    return sorted([*picked, extra], key=lambda c: pos[c["beat_id"]])


def covers_end(cards: list[dict], order: list[str]) -> bool:
    """Whether the cards reach the last share of the beats."""
    pos = {b: i for i, b in enumerate(order)}
    return any(pos.get(c["beat_id"], -1) >= _tail_start(len(order)) for c in cards)


def _linked_cards(picked: list[dict], blueprint: dict) -> list[dict]:
    """Number the picked cards and connect each to the earlier cards it is
    really related to — only relations the blueprint itself states, never
    a guess:
      contradicts  — a contradiction beat that relies on a dated event
      builds_on    — a beat that relies on a fact an earlier card's beat revealed
      same_person  — two beats with the same human focus
    """
    beats = {b["id"]: b for b in blueprint.get("beats") or []}
    card_of_beat = {c["beat_id"]: n for n, c in enumerate(picked)}
    first_reveal: dict[str, str] = {}
    for b in blueprint.get("beats") or []:
        for fact in b.get("reveals") or []:
            first_reveal.setdefault(fact, b["id"])

    def focus(beat_id: str) -> str:
        value = beats.get(beat_id, {}).get("human_focus")
        return json.dumps(value, sort_keys=True) if value else ""

    cards: list[dict] = []
    for n, c in enumerate(picked):
        links: dict[int, str] = {}
        beat = beats.get(c["beat_id"], {})
        for fact in beat.get("relies_on") or []:
            earlier = card_of_beat.get(first_reveal.get(fact, ""))
            if earlier is not None and earlier < n:
                links[earlier] = "contradicts" if c["kind"] == "contradiction" else "builds_on"
        who = focus(c["beat_id"])
        if who:
            previous = next((m for m in range(n - 1, -1, -1)
                             if focus(picked[m]["beat_id"]) == who), None)
            if previous is not None:
                links.setdefault(previous, "same_person")
        cards.append({"id": f"card{n + 1}", "kind": c["kind"], "source_fact": c["source_fact"],
                      "beat_id": c["beat_id"],
                      # Section E: a card_overrides entry may already carry the fact/question's
                      # real current status (e.g. a question replayed as "answered" as of this
                      # beat) or a reuse_reason marking a deliberate return - previously this was
                      # always overwritten to "new" here, so no card could ever show anything but
                      # a fresh introduction.
                      "status": c.get("status", "new"), "reuse_reason": c.get("reuse_reason"),
                      "links": [{"to": f"card{m + 1}", "relation": rel}
                                for m, rel in sorted(links.items())]})
    return cards


def board_cards_for(row: BoardPlan | None, language: str) -> dict:
    """The cards of one language, in story order: [{id, title, kind,
    beat_id, status, links}]. Empty when the board is disabled, the plan
    has no row yet, or the writer failed for every card (status
    no_texts) — the film still renders without the board."""
    if row is None or not ai_config.detective_board.enabled:
        return {"cards": []}
    plan = json.loads(row.plan_json or "{}")
    cards = []
    for c in plan.get("cards") or []:
        title = (c.get("title") or {}).get(language)
        if not title:
            continue  # this card's title never passed validation in this language
        cards.append({"id": c["id"], "kind": c["kind"], "beat_id": c["beat_id"],
                      "status": c.get("status", "new"), "links": c.get("links") or [],
                      "title": title})
    return {"cards": cards}


def reading_hold_seconds(title: str) -> float:
    cfg = ai_config.detective_board
    words = max(len(title.split()), 1)
    seconds = cfg.hold_base_seconds + cfg.hold_per_word_seconds * words
    return max(cfg.hold_min_seconds, min(cfg.hold_max_seconds, seconds))


def _unit(card_id: str, salt: str) -> float:
    """A stable number in [-1, 1) from a card id: a note keeps the same
    drift and tilt in every appearance and every language."""
    digest = hashlib.sha256(f"{card_id}:{salt}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2 ** 31 - 1.0


def board_layout(cards: list[dict], language: str, w: int, h: int,
                 total: int | None = None) -> list[dict]:
    """Cork-board positions for `cards` (the cumulative set shown at one
    appearance). Notes sit on a fixed grid sized for the `total` the whole
    board will ever hold, so a note never moves once pinned; each drifts
    a little off its cell and tilts a few degrees, as pinned paper does.
    Order is left to right in every language (config); only the title
    glyphs are RTL-shaped for fa/ar. Returns [{id, row, col, x, y, w, h,
    angle}] in pixel units of a w x h canvas."""
    cfg = ai_config.detective_board
    per_row = max(2, cfg.cards_per_row)
    rows = max(1, -(-max(total or len(cards), len(cards)) // per_row))
    left, right = int(w * 0.07), int(w * 0.93)
    top, bottom = int(h * 0.10), int(h * 0.92)
    cell_w, cell_h = (right - left) / per_row, (bottom - top) / rows
    cw = min(int(w * cfg.card_width_fraction), int(cell_w * 0.86))
    ch = min(int(h * cfg.card_height_fraction), int(cell_h * 0.84))
    out = []
    for n, c in enumerate(cards):
        row, col = divmod(n, per_row)
        slack_x, slack_y = cell_w - cw, cell_h - ch
        x = left + col * cell_w + slack_x / 2 \
            + _unit(c["id"], "x") * slack_x * cfg.jitter_fraction * 2
        y = top + row * cell_h + slack_y / 2 \
            + _unit(c["id"], "y") * slack_y * cfg.jitter_fraction * 2
        out.append({"id": c["id"], "row": row, "col": col, "x": int(x), "y": int(y),
                    "w": cw, "h": ch,
                    "angle": round(_unit(c["id"], "a") * cfg.max_tilt_degrees, 2)})
    return out


def appearances(cards: list[dict], room: dict[str, float]) -> list[dict]:
    """Where the board appears and what is new each time: cards in story
    order, batched by the next beat (in order) whose room — the seconds
    until the next card is due, or until the film ends — is long enough
    to fly in all cards due since the last appearance and let the viewer
    read them. A card due where there is no such room waits for the next
    one that has: it still joins the board, just not with its own moment.

    Returns [{beat_id, new_card_ids, cumulative_card_ids, hold_seconds}]."""
    cfg = ai_config.detective_board
    out: list[dict] = []
    pending: list[dict] = []
    shown: list[dict] = []
    for c in cards:
        pending.append(c)
        gap = room.get(c["beat_id"], 0.0)
        hold = max((reading_hold_seconds(p.get("title") or "") for p in pending), default=0.0)
        needed = max(cfg.min_card_seconds,
                     hold + cfg.stagger_seconds * (len(pending) - 1))
        if gap >= needed:
            shown = shown + pending
            out.append({"beat_id": c["beat_id"], "new_card_ids": [p["id"] for p in pending],
                       "cumulative_card_ids": [s["id"] for s in shown],
                       "hold_seconds": min(needed, gap)})
            pending = []
    if pending:
        # the last appearance always shows the full road, even if its own
        # beat had no room of its own (B5: the last appearance of the
        # film shows the full road)
        shown = shown + pending
        out.append({"beat_id": pending[-1]["beat_id"],
                   "new_card_ids": [p["id"] for p in pending],
                   "cumulative_card_ids": [s["id"] for s in shown], "hold_seconds": 0.0})
    return out


def board_schedule(cards: list[dict], spans: list[dict]) -> list[dict]:
    """When the board is on screen: each appearance starts where its beat has been spoken and holds long enough to read
    the new notes, within the room until the next one is due and the share of the film the board may take. Pure: the
    production script pins the board where this says, and the toolkit's film timeline keeps its cards out of these
    moments. Returns [{beat_id, start, end, new_card_ids, cumulative_card_ids}]."""
    cfg = ai_config.detective_board
    by = {sp["beat_id"]: sp for sp in spans}
    cards = sorted((c for c in cards if c["beat_id"] in by), key=lambda c: by[c["beat_id"]]["end"])
    if not cards or not spans:
        return []
    film_end = max(sp["end"] for sp in spans)
    due = [by[c["beat_id"]]["end"] for c in cards]
    room = {c["beat_id"]: (due[k + 1] if k + 1 < len(cards) else film_end) - due[k] for k, c in enumerate(cards)}
    budget = cfg.max_share * film_end
    out, shown, used = [], [], 0.0
    for app in appearances(cards, room):
        start = by[app["beat_id"]]["end"]
        hold = min(app["hold_seconds"], room[app["beat_id"]])
        if hold < cfg.min_card_seconds or used + hold > budget:
            continue                       # no usable room yet: the notes join the next appearance
        used += hold
        new_ids = [i for i in app["cumulative_card_ids"] if i not in shown]
        shown = list(app["cumulative_card_ids"])
        out.append({"beat_id": app["beat_id"], "start": round(start, 3), "end": round(start + hold, 3),
                    "new_card_ids": new_ids, "cumulative_card_ids": shown})
    return out
