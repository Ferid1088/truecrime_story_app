"""The `detective_toolkit` call boundary (architecture rule, 2026-10-28).

The Main Visual Planner is the only caller of the Detective Director (Section C). This module is
the single, observable place that call happens: `app.documentary.detective.director` must never be
imported or called directly from pipeline code (`app/documentary/mvp/*`) — only from here.

    result = await invoke(db, case, blueprint_row, graph)
    result.called          # True iff a paid Detective Director call was made this time
    result.scene_count     # DetectiveScene rows now available for this graph
    result.uncovered_beats # beats no directed scene covers yet (the planner gap-fills these)

Off by default (`config/parameters/detective_toolkit.json`, `ai_config.detective_toolkit.enabled`)
so no real case's pipeline behavior or cost changes until the owner turns it on. When enabled it
first registers the blueprint's text as the graph's narration units (the planner owns the text),
then maps and directs the film in chunks of `beats_per_call` consecutive beats through the exact
Section C code path (`director.map_film` / `direct_scenes`). A chunk whose beats are all covered by
directed scenes is never paid for again, so an interrupted or budget-stopped run resumes where it
stopped. A case whose own cap is reached stops cleanly between chunks; the beats left over are
reported, not failed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import DetectiveScene, EditorialBlueprint, NarrationUnit, RevealGraph, StoryVersion
from app.documentary.detective import director as DD
from app.documentary.detective.narration import ingest_narration
from app.services.budget import BudgetService


@dataclass
class ToolkitResult:
    enabled: bool
    called: bool
    reason: str
    scene_count: int
    units_registered: int = 0
    chunks_total: int = 0
    chunks_called: int = 0
    uncovered_beats: list[str] = field(default_factory=list)
    stopped: str | None = None


def _language_and_text_version(db: Session, case, blueprint_row: EditorialBlueprint) -> tuple[str, int]:
    story_version = db.get(StoryVersion, blueprint_row.story_version_id)
    text_version = story_version.version if story_version is not None else 1
    return case.language, text_version


def _beat_chunks(units: list[NarrationUnit], beats_per_call: int) -> list[tuple[list[str], list[str]]]:
    """[(beat ids, unit keys)] per run of `beats_per_call` consecutive beats, in story order."""
    beats: list[tuple[str, list[str]]] = []
    for unit in units:
        if not beats or beats[-1][0] != unit.beat_id:
            beats.append((unit.beat_id, []))
        beats[-1][1].append(unit.unit_key)
    return [([b for b, _ in beats[i:i + beats_per_call]], [k for _, keys in beats[i:i + beats_per_call] for k in keys])
            for i in range(0, len(beats), beats_per_call)]


def _directed_beats(db: Session, graph_id: int, units: list[NarrationUnit]) -> set[str]:
    """Beats that at least one directed scene (not a map stub) covers."""
    beat_of = {u.unit_key: u.beat_id for u in units}
    covered: set[str] = set()
    for scene in db.query(DetectiveScene).filter_by(graph_id=graph_id, mode="direct_scenes"):
        covered.update(beat_of[k] for k in json.loads(scene.covered_unit_keys_json or "[]") if k in beat_of)
    return covered


def _cap_reached(db: Session, case) -> str | None:
    """The case's own cap, read without `BudgetService.check`'s side effect of stopping the case."""
    snap = BudgetService(db).snapshot(case.id)
    if snap.spent_eur >= snap.cap_eur - 1e-9:
        return f"case budget €{snap.cap_eur:.2f} reached: spent €{snap.spent_eur:.2f}"
    return None


async def invoke(db: Session, case, blueprint_row: EditorialBlueprint, graph: RevealGraph) -> ToolkitResult:
    """The single observable call boundary the Main Visual Planner uses to get a detective plan
    for `graph`. See module docstring for the gating rules."""
    if not ai_config.detective_toolkit.enabled:
        return ToolkitResult(enabled=False, called=False,
                             reason="detective_toolkit.enabled is False", scene_count=0)
    ingested = ingest_narration(db, blueprint_row, graph)
    units = db.query(NarrationUnit).filter_by(graph_id=graph.id).order_by(NarrationUnit.story_order).all()
    scenes = db.query(DetectiveScene).filter_by(graph_id=graph.id).count()
    if not units:
        return ToolkitResult(enabled=True, called=False, scene_count=scenes,
                             reason="the blueprint's text has no narration to plan from")
    chunks = _beat_chunks(units, ai_config.detective_toolkit.beats_per_call)
    covered = _directed_beats(db, graph.id, units)
    pending = [c for c in chunks if not set(c[0]) <= covered]
    if not pending:
        return ToolkitResult(enabled=True, called=False, scene_count=scenes, chunks_total=len(chunks),
                             units_registered=ingested.registered,
                             reason="a current detective plan already covers every beat of this graph")
    language, text_version = _language_and_text_version(db, case, blueprint_row)
    called, stopped = 0, None
    for beats, unit_keys in pending:
        stopped = _cap_reached(db, case)
        if stopped:
            break
        await DD.map_film(db, graph.id, language=language, text_version=text_version,
                          scene_unit_keys=unit_keys)
        await DD.direct_scenes(db, graph.id, scene_unit_keys=unit_keys,
                               language=language, text_version=text_version)
        called += 1
    uncovered = sorted(set(u.beat_id for u in units) - _directed_beats(db, graph.id, units))
    return ToolkitResult(
        enabled=True, called=called > 0, chunks_total=len(chunks), chunks_called=called,
        units_registered=ingested.registered, uncovered_beats=uncovered, stopped=stopped,
        scene_count=db.query(DetectiveScene).filter_by(graph_id=graph.id).count(),
        reason=(f"planned {called} of {len(pending)} pending chunk(s)" if not stopped
                else f"stopped after {called} of {len(pending)} pending chunk(s): {stopped}"))
