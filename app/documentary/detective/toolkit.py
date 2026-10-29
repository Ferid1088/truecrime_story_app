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
from app.documentary.detective import buildable as BD
from app.documentary.detective import director as DD
from app.documentary.detective.narration import ingest_narration
from app.longform import service as LF
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
    alternative_share: float = 0.0
    target_met: bool = False
    fill_passes: int = 0


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


FILL_INSTRUCTIONS = (
    "ADD CARDS: these beats are already directed, but their alternatives fill too little of the film. Add new scenes "
    "(scene ids starting with '{prefix}-', never an existing id) that cover these units with alternative cards the "
    "sourced claims can really build: fact_card, key_facts, evidence_check, unknowns, recap, timeline, conflict, "
    "quote, courtroom or another catalog tool. Bind every claim, relation or question the card shows. A card the "
    "claims cannot build is rejected, so choose by what the claims support.")


async def _fill(db: Session, case, graph: RevealGraph, units: list[NarrationUnit], language: str,
                text_version: int) -> tuple[float, int, str | None]:
    """Extra Director calls for the weakest beats until the alternatives fill the configured share of the film."""
    cfg = ai_config.detective_toolkit
    share = BD.alternative_share(db, graph.id, language, units)
    passes, stopped = 0, None
    while share < cfg.min_alternative_share and passes < cfg.max_fill_passes:
        stopped = _cap_reached(db, case)
        if stopped:
            break
        beats = set(BD.weakest_beats(db, graph.id, language, units, cfg.beats_per_call))
        keys = [u.unit_key for u in units if u.beat_id in beats]
        passes += 1
        await DD.direct_scenes(db, graph.id, scene_unit_keys=keys, language=language, text_version=text_version,
                               run_id=f"fill-{passes}", owner_instructions=FILL_INSTRUCTIONS.format(prefix=f"fill{passes}"))
        share = BD.alternative_share(db, graph.id, language, units)
    return share, passes, stopped


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
    share, fill_passes = 0.0, 0
    if not stopped and not uncovered:
        share, fill_passes, stopped = await _fill(db, case, graph, units, language, text_version)
    else:
        share = BD.alternative_share(db, graph.id, language, units)
    return ToolkitResult(
        enabled=True, called=called > 0 or fill_passes > 0, chunks_total=len(chunks), chunks_called=called,
        units_registered=ingested.registered, uncovered_beats=uncovered, stopped=stopped,
        alternative_share=share, fill_passes=fill_passes,
        target_met=share >= ai_config.detective_toolkit.min_alternative_share,
        scene_count=db.query(DetectiveScene).filter_by(graph_id=graph.id).count(),
        reason=("a current detective plan already covers every beat of this graph" if not pending
                else f"planned {called} of {len(pending)} pending chunk(s)" if not stopped
                else f"stopped after {called} of {len(pending)} pending chunk(s): {stopped}")
        + f"; alternatives fill {share:.1%} of the film after {fill_passes} fill pass(es)")


def graph_of(db: Session, blueprint_row: EditorialBlueprint) -> RevealGraph:
    """The reveal graph the toolkit made for this blueprint: the Main Visual Planner reads it and never makes one."""
    graph = (db.query(RevealGraph).filter_by(case_id=blueprint_row.case_id, blueprint_id=blueprint_row.id,
                                             status="validated").order_by(RevealGraph.version.desc()).first())
    if graph is None:
        raise LookupError("The Detective Toolkit has not made the reveal graph for this blueprint yet")
    return graph


def ensure_graph(db: Session, case, blueprint_row: EditorialBlueprint) -> RevealGraph:
    """The case's validated reveal graph for this blueprint: reused when present, built from the blueprint
    otherwise, and never silently replaced by an invalid one."""
    try:
        return graph_of(db, blueprint_row)
    except LookupError:
        payload = LF.build_reveal_graph_payload(db, case.id, blueprint_row.id)
        count = db.query(RevealGraph).filter_by(case_id=case.id, blueprint_id=blueprint_row.id).count()
        return LF.create_reveal_graph(db, case.id, blueprint_row.id, {**payload, "version": count + 1})


async def stage_toolkit(db: Session, case, blueprint_row: EditorialBlueprint) -> tuple[RevealGraph, ToolkitResult]:
    """The toolkit's stage, run before the Main Visual Planner: it makes the reveal graph and, when enabled,
    plans the film's alternatives on it."""
    graph = ensure_graph(db, case, blueprint_row)
    return graph, await invoke(db, case, blueprint_row, graph)
