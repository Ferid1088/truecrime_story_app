"""The toolkit's cards, made from the film's text.

An analyser agent cuts the narration into parts and says which kinds of card suit each; for every (part, kind) a card maker
agent makes the card from the words of that part only; an inspector agent checks the card against those words and sends the
problems back, the maker fixes, the inspector looks again (a configured number of rounds, then the card is dropped). A memory
of the cards made so far refuses a card that repeats an earlier one. The map is chosen from a stored place, the timeline from
the case's dated events (no model call); the detective board is the board plan's. Every card is stored with its metadata:
the part of the text it belongs to, its kind and the moment of the beat after which it may be shown.

The cards (with the board and the map) are made until they fill the configured minimum share of the film, in at most the
configured number of attempts, and never beyond its maximum."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import DetectiveScene, DetectiveToolAction, NarrationUnit, RevealNode
from app.documentary.detective import buildable as BD
from app.documentary.detective import dispatch as D
from app.documentary.detective.reading import reading_need
from app.documentary.graphics.base import GraphicUnavailable
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.budget import BudgetService

#: the kinds a card maker makes; the timeline, the map and the board are made another way
MAKER_KINDS = ("fact_card", "recap", "conflict", "evidence_check", "forensic")
TEXT_FIELDS = ("text", "claim", "title", "summary", "name", "label")
WORD = re.compile(r"[^\W_]{4,}", re.UNICODE)
LATE_IN_BEAT = 0.6          # a card about what is said later than this share of its beat is shown at the start of the next beat


@dataclass
class Part:
    """One stretch of the narration that tells one thing, and the card kinds that suit it."""

    id: str
    unit_keys: list[str]
    summary: str
    kinds: list[str]


@dataclass
class CardsReport:
    parts: int = 0
    made: dict[str, int] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    repeats: int = 0
    attempts: int = 0
    share: float = 0.0
    stopped: str | None = None
    errors: list[str] = field(default_factory=list)


def texts_of(data: object) -> list[str]:
    """Every statement of a card's data (the words a reader sees), whatever the kind."""
    found: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key in TEXT_FIELDS and isinstance(value, str):
                found.append(value)
            elif isinstance(value, dict | list):
                found.extend(texts_of(value))
    elif isinstance(data, list):
        for item in data:
            found.extend(texts_of(item))
    return found


def words_of(data: object) -> set[str]:
    return {w.casefold() for t in texts_of(data) for w in WORD.findall(t)}


class CardMemory:
    """What the film's cards already show: a card that shares most of its words with an earlier card is a repeat."""

    def __init__(self) -> None:
        self.cards: list[tuple[str, set[str], list[str]]] = []

    @classmethod
    def of_film(cls, db: Session, graph_id: int) -> CardMemory:
        memory = cls()
        for action in (db.query(DetectiveToolAction).join(DetectiveScene, DetectiveScene.id == DetectiveToolAction.scene_id)
                       .filter(DetectiveScene.graph_id == graph_id)):
            data = D.payload_of(action).get("data")
            if data:
                memory.add(action.tool_id, data)
        return memory

    def add(self, kind: str, data: dict) -> None:
        self.cards.append((kind, words_of(data), texts_of(data)))

    def repeats(self, data: dict) -> bool:
        mine = words_of(data)
        if not mine:
            return False
        limit = ai_config.detective_toolkit.repeat_overlap
        return any(len(mine & other) / min(len(mine), len(other)) >= limit for _, other, _ in self.cards if other)

    def count(self, kind: str) -> int:
        return sum(1 for k, _, _ in self.cards if k == kind)

    def shown(self, limit: int = 40) -> list[str]:
        """The latest statements already on cards, short, for the agents to avoid."""
        return [t[:80] for _, _, texts in self.cards[-limit:] for t in texts[:1]]


def _cap_reached(db: Session, case) -> str | None:
    snap = BudgetService(db).snapshot(case.id)
    if snap.spent_eur >= snap.cap_eur - 1e-9:
        return f"case budget EUR {snap.cap_eur:.2f} reached: spent EUR {snap.spent_eur:.2f}"
    return None


def _unit_json(units: list[NarrationUnit]) -> list[dict]:
    return [{"unit_key": u.unit_key, "text": u.text} for u in units]


def beat_chunks(units: list[NarrationUnit], size: int) -> list[list[NarrationUnit]]:
    """Runs of whole beats of about `size` units each, in story order."""
    chunks: list[list[NarrationUnit]] = []
    current: list[NarrationUnit] = []
    for unit in units:
        if current and len(current) >= size and unit.beat_id != current[-1].beat_id:
            chunks.append(current)
            current = []
        current.append(unit)
    return chunks + ([current] if current else [])


def valid_parts(raw: dict, units: list[NarrationUnit], allowed: tuple[str, ...]) -> list[Part]:
    """The analyser's parts that name existing units in order and only the kinds that may be proposed."""
    order = {u.unit_key: n for n, u in enumerate(units)}
    parts: list[Part] = []
    last = -1
    for p in raw.get("parts") or []:
        keys = [k for k in p.get("unit_keys") or [] if k in order]
        beat = next((u.beat_id for u in units if u.unit_key == keys[0]), None) if keys else None
        keys = [k for k in keys if next(u.beat_id for u in units if u.unit_key == k) == beat]   # a part stays in one beat
        if not keys or [order[k] for k in keys] != sorted(order[k] for k in keys) or order[keys[0]] <= last:
            continue
        kinds = [c.get("kind") for c in p.get("cards") or [] if c.get("kind") in allowed][:2]
        if kinds:
            parts.append(Part(str(p.get("id") or f"P{len(parts) + 1:02d}"), keys, str(p.get("summary") or "")[:200], kinds))
            last = order[keys[-1]]
    return parts


async def analyse(db: Session, case, units: list[NarrationUnit], language: str, planned: list[Part],
                  covered: list[str]) -> list[Part]:
    """The film's parts: the analyser reads the narration in runs of whole beats and keeps away from the sentences cards
    already cover."""
    cfg = ai_config.detective_toolkit
    parts: list[Part] = []
    for chunk in beat_chunks(units, cfg.analyser_units_per_call):
        if _cap_reached(db, case):
            break
        payload = {"language": language, "units": _unit_json(chunk),
                   "kinds": {k: cfg.card_kinds[k].purpose for k in MAKER_KINDS if k in cfg.card_kinds},
                   "limits": {"fact_card": cfg.max_fact_cards},
                   "already_planned": [f"{p.id}: {p.summary}" for p in planned + parts][-30:],
                   "covered_units": [k for k in covered if k in {u.unit_key for u in chunk}]}
        raw, _ = await asyncio.wait_for(run_agent("detective.text_analyser", get_generation_provider(),
                                                  json.dumps(payload, ensure_ascii=False)), cfg.call_timeout_s)
        found = valid_parts(raw, chunk, MAKER_KINDS)
        for p in found:                         # part ids stay unique over the whole film
            p.id = f"P{len(parts) + 1:02d}"
            parts.append(p)
    return parts


def release_after(units: list[NarrationUnit], covered: list[str]) -> float:
    """How far into its beat (by the words) the last covered sentence ends: the card is shown after that."""
    beat = next((u.beat_id for u in units if u.unit_key == covered[-1]), None)
    words, spoken = 0, 0
    for u in units:
        if u.beat_id != beat:
            continue
        words += len(u.text.split())
        if u.unit_key in covered or spoken:
            spoken = words if u.unit_key in covered else spoken
    return round(min(spoken / words, 0.9), 3) if words else 0.0


async def _maker(payload: dict) -> dict:
    raw, _ = await asyncio.wait_for(run_agent("detective.card_maker", get_generation_provider(),
                                              json.dumps(payload, ensure_ascii=False)),
                                    ai_config.detective_toolkit.call_timeout_s)
    return raw


async def _inspector(payload: dict) -> dict:
    raw, _ = await asyncio.wait_for(run_agent("detective.card_inspector", get_generation_provider(),
                                              json.dumps(payload, ensure_ascii=False)),
                                    ai_config.detective_toolkit.call_timeout_s)
    return raw


def _problem_of(kind: str, data: object, language: str) -> str | None:
    """Why the renderer would refuse this card, or why its text cannot be read in the time a card may be held, or None."""
    if not isinstance(data, dict):
        return "the card is not an object"
    try:
        problem = BD.render_problem(kind, data)
        if problem is None:
            reading_need(kind, data, language)
        return problem
    except GraphicUnavailable as e:
        return str(e)


def _bad_references(data: dict, keys: list[str]) -> list[str]:
    """Every fact_id of a card must be a unit key of the part it was made from."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if "fact_id" in node and node["fact_id"] not in keys:
                found.append(str(node["fact_id"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return found


async def make_card(case_units: list[NarrationUnit], part: Part, kind: str, language: str, shown: list[str],
                    sem: asyncio.Semaphore) -> tuple[dict | None, str]:
    """One card of one kind from one part: made, checked by the renderer and the inspector, fixed when the problems
    are reported, or none (with the reason)."""
    cfg = ai_config.detective_toolkit
    kind_cfg = cfg.card_kinds[kind]
    own = [u for u in case_units if u.unit_key in set(part.unit_keys)]
    first = next(n for n, u in enumerate(case_units) if u.unit_key == part.unit_keys[0])
    before = [{"unit_key": u.unit_key, "text": u.text} for u in case_units[max(first - 4, 0):first]]
    base = {"kind": kind, "purpose": kind_cfg.purpose, "schema_text": kind_cfg.schema_text, "example": kind_cfg.example,
            "language": language, "part": {"id": part.id, "summary": part.summary, "units": _unit_json(own)},
            "before": before, "already_made": shown}
    inspect_base = {"kind": kind, "purpose": kind_cfg.purpose, "language": language,
                    "part": base["part"], "before": before, "already_made": shown}
    repair = None
    async with sem:
        for round_ in range(cfg.inspector_rounds + 1):
            raw = await _maker({**base, **({"repair": repair} if repair else {})})
            data = raw.get("card")
            if data is None:
                return None, str(raw.get("reason") or "no card")[:200]
            problems = []
            if (problem := _problem_of(kind, data, language)) is not None:
                problems.append({"where": "the card", "what": problem, "fix": "keep every limit of the schema"})
            elif (bad := _bad_references(data, part.unit_keys)):
                problems.append({"where": "fact_id", "what": f"{bad} are not unit keys of this part",
                                 "fix": "use only the unit keys of the part"})
            else:
                verdict = await _inspector({**inspect_base, "card": data})
                if not verdict.get("ok"):
                    problems = [p for p in verdict.get("problems") or [] if isinstance(p, dict)] or [
                        {"where": "the card", "what": "the inspector refused it", "fix": "make it again from the text"}]
            if not problems:
                return data, "ok"
            if round_ == cfg.inspector_rounds:
                return None, "; ".join(str(p.get("what")) for p in problems)[:300]
            repair = {"previous": data, "problems": problems}
    return None, "no card"


def store_card(db: Session, graph_id: int, part: Part, kind: str, data: dict, units: list[NarrationUnit], language: str,
               serial: int, release: float | None = None) -> DetectiveToolAction:
    """One scene of one card with its metadata: the part of the text, the kind, the moment after which it may be shown and
    how long its text needs."""
    try:
        seconds = round(reading_need(kind, data, language).planned, 1)
    except GraphicUnavailable:
        seconds = None
    order = db.query(DetectiveScene).filter_by(graph_id=graph_id).count() + 1
    shown_at = release_after(units, part.unit_keys) if release is None else release
    next_beat = None
    if release is None and shown_at > LATE_IN_BEAT:             # no room after the last sentence: the start of the next beat
        beats = list(dict.fromkeys(u.beat_id for u in units))
        here = beats.index(next(u.beat_id for u in units if u.unit_key == part.unit_keys[-1]))
        next_beat = beats[here + 1] if here + 1 < len(beats) else None
        shown_at = 0.0 if next_beat else shown_at
    scene = DetectiveScene(
        graph_id=graph_id, scene_key=f"card-{part.id}-{kind}-{serial}", scene_order=order, mode="direct_scenes",
        covered_unit_keys_json=json.dumps(part.unit_keys), aim=part.summary or f"A {kind} for this part",
        active_question_keys_json="[]", active_entity_keys_json="[]", change_in_understanding="The viewer holds on to this",
        alternatives_considered_json="[]", why_selected=f"{kind} suits this part of the text",
        expected_audience_gain="The point stays in mind while the narration goes on", audio_clarity_findings_json="[]")
    db.add(scene)
    db.flush()
    action = DetectiveToolAction(
        scene_id=scene.id, action_key=f"{scene.scene_key}-a1", order_index=0, tool_id=kind,
        purpose=part.summary or kind, continuity_refs_json="[]", content_bindings_json=json.dumps(part.unit_keys),
        payload_json=json.dumps({"data": data, "part": part.id, "seconds": seconds, "release_fraction": shown_at,
                                 **({"beat": next_beat} if next_beat else {})}, ensure_ascii=False))
    db.add(action)
    db.flush()
    return action


def timeline_events(db: Session, graph_id: int) -> list[dict]:
    """The case's dated events (the graph's timeline nodes) in story order, with their dates and beats."""
    events = []
    for node in db.query(RevealNode).filter_by(graph_id=graph_id, category="timeline").order_by(RevealNode.first_revealed_order):
        date = D._event_date(((json.loads(node.metadata_json or "{}").get("evidence") or {}).get("event_date")))
        if date and node.label:
            events.append({"key": node.node_key, "label": " ".join(node.label.split()), "date": date,
                           "beat": node.first_revealed_beat_id, "order": node.first_revealed_order})
    return events


def ensure_map(db: Session, graph_id: int, units: list[NarrationUnit], language: str) -> bool:
    """The film's map: a stored place (a place or building of the graph) shown after the beat that names it. None is made
    when the film has a map already or the graph holds no place; a place the text never names is shown after the first beat."""
    if (db.query(DetectiveToolAction).join(DetectiveScene, DetectiveScene.id == DetectiveToolAction.scene_id)
            .filter(DetectiveScene.graph_id == graph_id, DetectiveToolAction.tool_id == "map").first()):
        return False
    texts = {u.unit_key: " ".join(u.text.lower().split()) for u in units}
    places = [n for n in db.query(RevealNode).filter(RevealNode.graph_id == graph_id,
                                                     RevealNode.category.in_(("place", "building", "location")))
              .order_by(RevealNode.first_revealed_order) if n.label]
    named = [(n, next((k for k, t in texts.items() if n.label.lower() in t), None)) for n in places]
    node, unit_key = next(((n, k) for n, k in named if k), (places[0], units[0].unit_key) if places else (None, None))
    if node is None:
        return False
    beat = next(u.beat_id for u in units if u.unit_key == unit_key)
    keys = [u.unit_key for u in units if u.beat_id == beat]
    action = store_card(db, graph_id, Part("MAP", keys, f"Where it happened: {node.label}", ["map"]), "map",
                        {"place": node.label}, units, language, 0)
    action.payload_json = json.dumps({"place": node.label, "data": {"place": node.label}, "part": "MAP",
                                      "release_fraction": 0.3})
    db.flush()
    return True


def spread_order(n: int) -> list[int]:
    """The indexes 0..n-1 so that any prefix is spread over the whole range (every 8th, then every 4th, 2nd, the rest)."""
    seen: list[int] = []
    for step in (8, 4, 2, 1):
        seen += [i for i in range(0, n, step) if i not in seen]
    return seen


def _share(db: Session, graph_id: int, language: str, units: list[NarrationUnit], windows: list[dict] | None):
    timeline = BD.planned_timeline(db, graph_id, language, units, windows)
    return timeline.alternative_share, {s["action_key"]: s.get("reason") for s in timeline.skipped}


async def build_cards(db: Session, case, graph_id: int, units: list[NarrationUnit], language: str,
                      windows: list[dict] | None = None) -> CardsReport:
    """Make the film's cards until the cards, the board and the map fill the minimum share, in at most `attempts` attempts."""
    cfg = ai_config.detective_toolkit
    report = CardsReport()
    memory = CardMemory.of_film(db, graph_id)
    ensure_map(db, graph_id, units, language)
    db.commit()
    report.share, _ = _share(db, graph_id, language, units, windows)
    sem = asyncio.Semaphore(cfg.concurrency)
    earlier: list[Part] = []
    serial = db.query(DetectiveScene).filter_by(graph_id=graph_id).count()
    for attempt in range(cfg.attempts):
        if report.share >= cfg.min_alternative_share:
            break
        report.stopped = _cap_reached(db, case)
        if report.stopped:
            break
        report.attempts += 1
        covered = [k for p in earlier for k in p.unit_keys]
        fresh = await analyse(db, case, units, language, earlier, covered)
        report.parts += len(fresh)
        wanted = [(p, p.kinds[0]) for p in fresh] + [(p, p.kinds[1]) for p in earlier if len(p.kinds) > 1 and attempt == 1]
        earlier += fresh
        wanted = [wanted[i] for i in spread_order(len(wanted))]
        wave = max(cfg.concurrency * 2, 1)
        for at in range(0, len(wanted), wave):
            if report.share >= cfg.max_alternative_share:
                break
            batch = [(p, k) for p, k in wanted[at:at + wave]
                     if not (k == "fact_card" and memory.count("fact_card") >= cfg.max_fact_cards)]
            results = await asyncio.gather(*(make_card(units, p, k, language, memory.shown(), sem) for p, k in batch),
                                           return_exceptions=True)
            for (part, kind), result in zip(batch, results, strict=True):
                if isinstance(result, GenerationError | TimeoutError | asyncio.TimeoutError):
                    report.errors.append(f"{part.id} {kind}: {type(result).__name__}")
                    continue
                if isinstance(result, BaseException):
                    raise result
                data, why = result
                if data is None:
                    report.dropped.append(f"{part.id} {kind}: {why}")
                    continue
                if memory.repeats(data) or (kind == "fact_card" and memory.count("fact_card") >= cfg.max_fact_cards):
                    report.repeats += 1
                    continue
                if report.share >= cfg.max_alternative_share:
                    break
                serial += 1
                action = store_card(db, graph_id, part, kind, data, units, language, serial)
                share, skipped = _share(db, graph_id, language, units, windows)
                if action.action_key in skipped:                    # the film's timeline has no room for it
                    db.delete(action.scene)
                    db.delete(action)
                    db.flush()
                    report.dropped.append(f"{part.id} {kind}: no room ({skipped[action.action_key]})")
                    continue
                memory.add(kind, data)
                report.made[kind] = report.made.get(kind, 0) + 1
                report.share = share
            db.commit()
    db.commit()
    return report
