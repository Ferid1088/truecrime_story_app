"""The Detective Director (Section C): real call path for map_film / direct_scenes / repair.

    data = await map_film(db, graph_id, language="en", text_version=1)
    data = await direct_scenes(db, graph_id, scene_unit_keys=["B01-1", "B01-2"], language="en", text_version=1)

Builds the doc's exact INPUT envelope from Section B's memory (app.documentary.memory) and the
provisional Section-A-derived tool catalog, calls the real agent through app.agents.runner, then
validates (app.documentary.detective.schema) before persisting
(app.documentary.detective.persist). A validation violation raises rather than persisting a
semantically broken plan.
"""

from __future__ import annotations

import json
import os

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import DetectiveScene, DetectiveToolAction, ReleaseAnchor, RevealGraph
from app.documentary import memory as MEM
from app.documentary.memory import MemoryValidationError
from app.documentary.detective import buildable as BD
from app.documentary.detective.persist import persist_director_output
from app.documentary.detective.schema import DirectorOutputError, NarrationIndex, validate_director_output
from app.documentary.detective.tool_catalog import build_tool_catalog
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run


class DirectorValidationFailed(ValueError):
    """The agent's structured output failed semantic validation; nothing was persisted."""

    def __init__(self, violations: list[str]):
        super().__init__("; ".join(violations))
        self.violations = violations


class UnbuildableCards(DirectorValidationFailed):
    """Actions whose card cannot be made from the claims they bind: the output is rolled back and repaired."""

    def __init__(self, found: list[tuple[str, str]]):
        super().__init__([message for _, message in found])
        self.keys = {key for key, _ in found}


def _without(raw: dict, keys: set[str]) -> dict:
    """The output without the actions that cannot be built; a scene left with no action stays, so the beats it covers
    count as directed."""
    return {**raw, "scenes": [{**scene, "actions": [a for a in scene.get("actions", []) if a.get("id") not in keys]}
                              for scene in raw.get("scenes", [])]}


def _case_id_for_graph(db: Session, graph_id: int) -> int:
    graph = db.get(RevealGraph, graph_id)
    if graph is None:
        raise ValueError(f"reveal graph {graph_id} not found")
    return graph.case_id


def all_narration(db: Session, graph_id: int) -> list[dict]:
    return MEM.get_narration_span(db, graph_id, beat_id=None, unit_keys=all_unit_keys(db, graph_id))


def all_unit_keys(db: Session, graph_id: int) -> list[str]:
    from app.db.models import NarrationUnit
    rows = db.query(NarrationUnit).filter_by(graph_id=graph_id).order_by(NarrationUnit.story_order).all()
    return [r.unit_key for r in rows]


def _all_anchors(db: Session, graph_id: int) -> list[dict]:
    rows = db.query(ReleaseAnchor).filter_by(graph_id=graph_id).all()
    out = []
    for a in rows:
        out.append({
            "anchor_key": a.anchor_key, "unit_key": a.narration_unit.unit_key,
            "char_start": a.char_start, "char_end": a.char_end,
        })
    return out


def _already_introduced(db: Session, graph_id: int) -> list[str]:
    """`subject_id`s that already have an 'introduce' knowledge event for this graph — a real live
    call (Section K) showed `direct_scenes` re-proposing 'introduce' for something `map_film` had
    already introduced, even though `existing_state` already lists it; a prose instruction alone
    was not reliable, so this list and the rule below are stated explicitly (same reasoning as the
    other `schema_hints` entries)."""
    from app.db.models import DetectiveKnowledgeEvent
    rows = (db.query(DetectiveKnowledgeEvent.subject_key)
           .filter_by(graph_id=graph_id, operation="introduce").all())
    return sorted({r[0] for r in rows})


def _existing_state(db: Session, graph_id: int) -> dict:
    replay = MEM.replay_state(db, graph_id)
    scenes = db.query(DetectiveScene).filter_by(graph_id=graph_id).order_by(DetectiveScene.scene_order).all()
    scene_summaries = []
    for s in scenes:
        actions = (
            db.query(DetectiveToolAction).filter_by(scene_id=s.id)
            .order_by(DetectiveToolAction.order_index).all()
        )
        scene_summaries.append({
            "id": s.scene_key, "aim": s.aim,
            "actions": [{"id": a.action_key, "tool_id": a.tool_id} for a in actions],
        })
    return {
        "entities_introduced": replay["entities_introduced"], "claims": replay["claims"],
        "questions": replay["questions"], "relations": replay["relations"],
        "previous_scenes": scene_summaries,
    }


def _build_input(db: Session, graph_id: int, mode: str, language: str, text_version: int,
                  scene_scope: list[str] | None = None, run_id: str = "run-1",
                  film_id: str | None = None, repair_target: dict | None = None,
                  owner_instructions: str = "") -> dict:
    graph = db.get(RevealGraph, graph_id)
    payload = {
        "mode": mode, "run_id": run_id, "film_id": film_id or f"case-{graph.case_id}",
        "language": language, "text_version": text_version, "schema_version": "1.0",
        "narration": all_narration(db, graph_id),
        "existing_state": _existing_state(db, graph_id),
        "scene_scope": scene_scope or all_unit_keys(db, graph_id),
        "tool_catalog": build_tool_catalog(),
        "asset_catalog": [],
        "channel_preferences": {}, "owner_instructions": owner_instructions,
        "schema_hints": {
            "claim_basis_values": sorted(MEM.CLAIM_BASES),
            "question_kind_values": sorted(MEM.QUESTION_KINDS),
            "knowledge_event_subject_types": sorted(MEM.KNOWLEDGE_SUBJECT_TYPES),
            "claim_fields": ["id", "entity_ids", "attribute", "text", "basis", "attribution",
                             "supporting_span_ids", "release_anchor", "evidence_ids"],
            "question_fields": ["id", "entity_ids", "question_text", "kind", "supporting_span_ids",
                                 "opening_anchor"],
            "relation_fields": ["id", "endpoint_ids", "relation_type", "label", "supporting_span_ids",
                                 "release_anchor"],
            "knowledge_event_fields": ["id", "anchor", "subject_type", "subject_id", "operation",
                                       "new_state", "supporting_claim_ids"],
            "scene_fields": ["id", "covered_span_ids", "aim", "active_question_ids", "active_thread_ids",
                             "change_in_understanding", "alternatives_considered", "why_selected",
                             "expected_audience_gain", "audio_clarity_findings", "actions"],
            "action_fields": ["id", "tool_id", "purpose", "payload", "content_bindings", "continuity_refs",
                              "board_operations", "reading_requirements", "relationship_to_neighbors",
                              "fallback", "reuse_reason"],
            "request_fields": ["id", "type", "scene_id", "unit_key", "passage", "problem",
                               "proposed_repair"],
            "text_revision_rule": prompt("documentary/detective/text_revision_rule"),
            "content_bindings_format": "a flat JSON list of claim/question/relation stable ID strings "
                                        "(the exact `id` values used above) — not objects, not nested.",
            "reuse_reason_rule": prompt("documentary/detective/reuse_reason_rule"),
            "question_status_values": sorted(MEM.QUESTION_STATUSES),
            "question_state_rule": prompt("documentary/detective/question_state_rule"),
            "already_introduced_subject_keys": _already_introduced(db, graph_id),
            "already_introduced_rule": prompt("documentary/detective/already_introduced_rule"),
        },
    }
    if repair_target is not None:
        payload["repair_target"] = repair_target
    return payload


STABLE_FIRST = ("schema_version", "language", "text_version", "film_id", "tool_catalog", "asset_catalog",
                "channel_preferences", "narration")


def cache_ordered(payload: dict) -> dict:
    """The payload with what is the same in every call of a film first (the whole narration is most of the input) and
    what changes with every call (mode, scope, the state so far, a repair's issues) last: a provider that caches by
    prompt prefix then reuses the long start for every chunk and every repair."""
    first = {k: payload[k] for k in STABLE_FIRST if k in payload}
    return {**first, **{k: v for k, v in payload.items() if k not in first}}


async def _ask(db: Session, case_id: int, graph_id: int, label: str, payload: dict) -> tuple[dict, int]:
    """One paid agent call, tracked as its own run (a repair attempt's run records the issues it was
    sent). Returns (raw output, agent run id)."""
    issues = (payload.get("repair_target") or {}).get("issues")
    summary = f"graph={graph_id} mode={payload['mode']}" + (f" issues={issues}"[:600] if issues else "")
    with track_run(db, case_id, f"Detective Director: {label}", input_summary=summary) as run:
        raw, res = await run_agent(
            "detective.director", get_generation_provider(), json.dumps(cache_ordered(payload), ensure_ascii=False)
        )
        stamp_run(run, res, "detective_director")
        run_id = run.id

    dump_path = os.environ.get("DETECTIVE_DIRECTOR_RAW_DUMP")
    if dump_path:
        # Opt-in diagnostic: the raw agent response before validation, for inspecting a live
        # call's actual shape without re-spending on a repeat call after a validation failure.
        with open(dump_path, "w") as f:
            json.dump(raw, f, indent=2)
    return raw, run_id


def _valid_part(db: Session, graph_id: int, raw: dict) -> dict:
    """The output without what names an entity the graph does not know (a claim ID where an entity ID belongs):
    such a claim, question or relation is dropped, and so is every binding, event and card that rested on it."""
    from app.db.models import RevealNode

    known = {n for (n,) in db.query(RevealNode.node_key).filter_by(graph_id=graph_id)} | {
        e["id"] for e in raw.get("entities", []) if e.get("id")}
    claims = [{**c, "entity_ids": [i for i in c.get("entity_ids") or [] if i in known]} for c in raw.get("claims", [])
              if c.get("id")]
    claims = [c for c in claims if c["entity_ids"]]
    questions = [{**q, "entity_ids": [i for i in q.get("entity_ids") or [] if i in known]}
                 for q in raw.get("questions", []) if q.get("id")]
    questions = [q for q in questions if q["entity_ids"]]
    relations = [r for r in raw.get("relations", []) if r.get("id") and r.get("endpoint_ids")
                 and all(i in known for i in r["endpoint_ids"])]
    kept = {x["id"] for x in claims + questions + relations}
    events = [e for e in raw.get("knowledge_events", [])
              if (e.get("subject_id") in known or e.get("subject_id") in kept)
              and all(i in kept for i in e.get("supporting_claim_ids") or [])]
    scenes = [{**s, "actions": [{**a, "content_bindings": [b for b in a.get("content_bindings", []) if b in kept]}
                                for a in s.get("actions", []) if a.get("id") and a.get("tool_id")]}
              for s in raw.get("scenes", []) if s.get("id")]
    return {**raw, "claims": claims, "questions": questions, "relations": relations, "knowledge_events": events,
            "scenes": scenes}


def _validated(raw, narration_index, known_tool_ids) -> list[str]:
    """The violations of an output; an output too damaged to be read at all is one violation, never a crash."""
    try:
        return validate_director_output(raw, narration_index, known_tool_ids)
    except DirectorOutputError as e:
        return [str(e)]
    except (KeyError, IndexError, TypeError, AttributeError, ValueError) as e:
        return [f"malformed output: {type(e).__name__} {e}"]


def _persist(db: Session, graph_id: int, run_id: int, mode: str, raw: dict) -> dict:
    """Write the output in a savepoint; one whose cards cannot be built is rolled back, nothing of it is kept."""
    with db.begin_nested():
        try:
            counts = persist_director_output(db, graph_id, run_id, mode, raw)
        except (KeyError, IndexError, TypeError, AttributeError, ValueError, SQLAlchemyError) as e:
            raise MemoryValidationError(f"malformed output: {type(e).__name__} {e}") from e
        if ai_config.detective_toolkit.require_buildable:
            db.flush()
            found = BD.unbuildable(db, graph_id, {a.get("id") for s in raw.get("scenes", []) for a in s.get("actions", [])})
            if found:
                raise UnbuildableCards(found)
    db.commit()
    return counts


async def _call(db: Session, graph_id: int, mode: str, payload: dict) -> dict:
    """Ask, validate, persist. A call whose output breaks an invariant is sent back with the exact
    issues, in the Director's own `repair` mode, up to `detective_toolkit.max_repair_iterations`
    times (each a paid call); only the output that finally passes is persisted, and a call that never
    does persists nothing and raises. Actions whose card cannot be built are repaired the same way; after the last
    repair the buildable ones are kept and the rest dropped, so one stubborn card does not sink a chunk. A real 84-sentence passage showed why: one claim listing a claim
    ID among its entity IDs sank a whole-film map that was otherwise sound."""
    case_id = _case_id_for_graph(db, graph_id)
    attempts = ai_config.detective_toolkit.max_repair_iterations
    issues: list[str] = []
    for attempt in range(attempts + 1):
        label = mode if attempt == 0 else f"{mode} (repair {attempt})"
        raw, run_id = await _ask(db, case_id, graph_id, label, payload)

        narration_index = NarrationIndex.build(payload["narration"], _all_anchors(db, graph_id))
        known_tool_ids = {t["tool_id"] for t in payload["tool_catalog"]}
        issues = _validated(raw, narration_index, known_tool_ids)
        if not issues:
            # Section B's memory functions enforce invariants schema.py cannot see without a DB round
            # trip (e.g. first-introduction uniqueness against *already-persisted* events, not just
            # this payload). A real live call (Section K) surfaced one: `direct_scenes` re-introduced
            # an entity `map_film` had already introduced. Without this savepoint, what this call had
            # already written stayed committed while the run crashed - a partially-persisted, semantically
            # broken plan. A failure here must roll back exactly as cleanly as a schema.py violation.
            try:
                counts = _persist(db, graph_id, run_id, mode, raw)
                return {"agent_run_id": run_id, "output": raw, "counts": counts}
            except UnbuildableCards as e:
                if attempt < attempts:
                    issues = e.violations
                else:        # the last attempt keeps every card that can be built and drops the rest
                    kept = _without(raw, e.keys)
                    return {"agent_run_id": run_id, "output": kept, "counts": _persist(db, graph_id, run_id, mode, kept),
                            "dropped_actions": sorted(e.keys)}
            except MemoryValidationError as e:
                if attempt < attempts:
                    issues = [str(e)]
                else:        # the last attempt keeps everything that names known entities
                    try:
                        kept = _valid_part(db, graph_id, raw)
                        return {"agent_run_id": run_id, "output": kept,
                                "counts": _persist(db, graph_id, run_id, mode, kept), "dropped_reason": str(e)}
                    except (MemoryValidationError, KeyError, IndexError, TypeError, AttributeError, ValueError) as again:
                        issues = [str(again)]
        payload = {**payload, "mode": "repair", "repair_target": {"previous_output": raw, "issues": issues}}
    raise DirectorValidationFailed(issues)


async def map_film(db: Session, graph_id: int, language: str, text_version: int,
                    run_id: str = "run-1", film_id: str | None = None,
                    scene_unit_keys: list[str] | None = None) -> dict:
    payload = _build_input(db, graph_id, "map_film", language, text_version,
                            scene_scope=scene_unit_keys, run_id=run_id, film_id=film_id)
    return await _call(db, graph_id, "map_film", payload)


async def direct_scenes(db: Session, graph_id: int, scene_unit_keys: list[str], language: str,
                         text_version: int, run_id: str = "run-1", film_id: str | None = None,
                         owner_instructions: str = "") -> dict:
    payload = _build_input(db, graph_id, "direct_scenes", language, text_version,
                            scene_scope=scene_unit_keys, run_id=run_id, film_id=film_id,
                            owner_instructions=owner_instructions)
    return await _call(db, graph_id, "direct_scenes", payload)


async def repair(db: Session, graph_id: int, previous_output: dict, issues: list[str], language: str,
                  text_version: int, run_id: str = "run-1", film_id: str | None = None) -> dict:
    payload = _build_input(db, graph_id, "repair", language, text_version, run_id=run_id,
                            film_id=film_id, repair_target={"previous_output": previous_output,
                                                             "issues": issues})
    return await _call(db, graph_id, "repair", payload)
