"""The toolkit's strategy for a whole film, made from its text before any scene is directed.

Two agents plan it. The film strategist decides the investigative strategy: the film's acts, what each card kind is for
and how the kinds help each other. The card strategist then plans, for each kind, where in the film it is used and what
it shows there, keeping that strategy. Both answers are checked (known kinds, known beats, the required kinds, the
minimum number of kinds), stored once per graph and handed to the Detective Director as `film_strategy` and
`card_plan`, so every scene it directs serves the plan."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import DetectiveStrategy, NarrationUnit
from app.documentary.detective.buildable import NEEDS, DEFAULT_NEED
from app.documentary.detective.tool_catalog import TOOL_CATALOG, build_tool_catalog
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

NOT_CARDS = ("photo", "host")


class StrategyInvalid(ValueError):
    """The strategist's answer is still not valid after its one repair."""


@dataclass
class Strategy:
    film: dict
    cards: dict

    @property
    def plans(self) -> dict[str, list[dict]]:
        return self.cards.get("plans") or {}


def card_kinds() -> list[str]:
    """The kinds of card the film may use: every tool of the catalog that is a card (not a photo, not the Host)."""
    return [k for k in TOOL_CATALOG if k not in NOT_CARDS]


def rules() -> dict:
    cfg = ai_config.detective_toolkit
    return {"required": list(cfg.required_kinds), "min_kinds": cfg.min_card_kinds, "kinds": card_kinds()}


def card_catalog() -> list[dict]:
    """What the strategists are told of each card kind: its job and what its content must be made from."""
    return [{"tool_id": t["tool_id"], "purpose": t["purpose"], "needs": NEEDS.get(t["tool_id"], DEFAULT_NEED)}
            for t in build_tool_catalog() if t["tool_id"] in card_kinds()]


def beat_texts(units: list[NarrationUnit]) -> list[dict]:
    """The whole text, beat by beat, in story order."""
    beats: list[dict] = []
    for unit in units:
        if not beats or beats[-1]["beat"] != unit.beat_id:
            beats.append({"beat": unit.beat_id, "text": ""})
        beats[-1]["text"] = (beats[-1]["text"] + " " + unit.text).strip()
    return beats


def validate_film(data, kinds: list[str], rule: dict) -> list[str]:
    if not isinstance(data, dict):
        return ["the answer is not an object"]
    issues = []
    if not str(data.get("thesis") or "").strip():
        issues.append("thesis is missing")
    arc = data.get("arc")
    if not isinstance(arc, list) or not arc or not all(
            isinstance(a, dict) and a.get("name") and a.get("question") and a.get("aim")
            and isinstance(a.get("beats"), list) and a["beats"] for a in arc):
        issues.append("arc must list acts, each with name, beats, question and aim")
    roles, used = data.get("kind_roles"), data.get("must_use")
    if not isinstance(roles, dict) or not isinstance(used, list):
        return issues + ["kind_roles must be an object and must_use a list"]
    for kind in [*roles, *used]:
        if kind not in kinds:
            issues.append(f"{kind!r} is not a card kind (use only: {', '.join(kinds)})")
    for kind in used:
        if kind not in roles:
            issues.append(f"{kind!r} is in must_use but has no kind_roles entry")
    for kind in rule["required"]:
        if kind not in used:
            issues.append(f"the required kind {kind!r} is not in must_use")
    others = {k for k in used if k != "map" and k in kinds}
    if len(others) < rule["min_kinds"]:
        issues.append(f"must_use has {len(others)} kinds other than the map; at least {rule['min_kinds']} are required")
    return issues


def validate_cards(data, film: dict, beats: list[str], kinds: list[str]) -> list[str]:
    if not isinstance(data, dict) or not isinstance(data.get("plans"), dict):
        return ["the answer must be an object with `plans`"]
    issues = []
    plans = data["plans"]
    for kind, placements in plans.items():
        if kind not in kinds:
            issues.append(f"{kind!r} is not a card kind")
            continue
        if not isinstance(placements, list):
            issues.append(f"{kind}: placements must be a list")
            continue
        for p in placements:
            if not isinstance(p, dict) or p.get("beat") not in beats or not p.get("shows") or not p.get("aim"):
                issues.append(f"{kind}: every placement needs a known beat, `shows` and `aim` ({p!r})"[:160])
                break
    for kind in film.get("must_use") or []:
        if not plans.get(kind):
            issues.append(f"{kind!r} is in the film strategy's must_use but has no placement")
    return issues


async def _ask(db: Session, case_id: int, agent: str, label: str, payload: dict):
    gen, body = get_generation_provider(), json.dumps(payload, ensure_ascii=False)
    calls = {"detective.film_strategist": lambda: run_agent("detective.film_strategist", gen, body),
             "detective.card_strategist": lambda: run_agent("detective.card_strategist", gen, body)}
    with track_run(db, case_id, f"Detective strategy: {label}", input_summary=label) as run:
        data, res = await asyncio.wait_for(calls[agent](), ai_config.detective_toolkit.call_timeout_s * 2)
        stamp_run(run, res, ai_config.agents[agent].role)
        return data, run.id


async def _checked(db: Session, case_id: int, agent: str, label: str, payload: dict, check) -> tuple[dict, int]:
    """One call and, when the answer is not valid, one more with the exact issues; still invalid raises."""
    data, run_id = await _ask(db, case_id, agent, label, payload)
    issues = check(data)
    if issues:
        data, run_id = await _ask(db, case_id, agent, f"{label} (repair)",
                                  {**payload, "previous_output": data, "issues": issues})
        issues = check(data)
    if issues:
        raise StrategyInvalid("; ".join(issues)[:600])
    return data, run_id


def _store(db: Session, graph_id: int, kind: str, language: str, data: dict, run_id: int | None) -> None:
    row = db.query(DetectiveStrategy).filter_by(graph_id=graph_id, kind=kind).first()
    if row is None:
        row = DetectiveStrategy(graph_id=graph_id, kind=kind)
        db.add(row)
    row.language, row.strategy_json, row.agent_run_id = language, json.dumps(data, ensure_ascii=False), run_id
    db.commit()


def load(db: Session, graph_id: int) -> Strategy | None:
    rows = {r.kind: json.loads(r.strategy_json) for r in db.query(DetectiveStrategy).filter_by(graph_id=graph_id)}
    return Strategy(rows["film"], rows["cards"]) if "film" in rows and "cards" in rows else None


async def ensure(db: Session, case, graph_id: int, units: list[NarrationUnit], language: str) -> Strategy | None:
    """The film's strategy: the stored one, or the two strategists' plan made from the text now. None when the
    strategists cannot give a valid plan (the film is then directed without one, and the result says so)."""
    known = load(db, graph_id)
    if known is not None or not units:
        return known
    kinds, rule, beats = card_kinds(), rules(), beat_texts(units)
    try:
        film, film_run = await _checked(
            db, case.id, "detective.film_strategist", "film",
            {"case": case.canonical_title, "language": language, "narration": beats, "cards": card_catalog(),
             "rules": rule}, lambda d: validate_film(d, kinds, rule))
        beat_ids = [b["beat"] for b in beats]
        cards, cards_run = await _checked(
            db, case.id, "detective.card_strategist", "cards",
            {"film_strategy": film, "beats": [{"beat": b["beat"], "summary": b["text"][:400]} for b in beats],
             "cards": card_catalog(), "rules": rule}, lambda d: validate_cards(d, film, beat_ids, kinds))
    except StrategyInvalid:
        return None
    _store(db, graph_id, "film", language, film, film_run)
    _store(db, graph_id, "cards", language, cards, cards_run)
    return Strategy(film, cards)


def brief(strategy: Strategy | None, beats: set[str]) -> dict:
    """What the Director is told for a stretch of the film: the whole film strategy (it is the same for every call)
    and the placements the card strategist planned in these beats."""
    if strategy is None:
        return {}
    plan = {kind: [p for p in placements if p.get("beat") in beats] for kind, placements in strategy.plans.items()}
    return {"film_strategy": strategy.film, "card_plan": {k: v for k, v in plan.items() if v}}
