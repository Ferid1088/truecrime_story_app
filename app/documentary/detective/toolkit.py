"""The `detective_toolkit` call boundary (architecture rule, 2026-10-28).

The Main Visual Planner is the only caller of the Detective Director (Section C). This module is
the single, observable place that call happens: `app.documentary.detective.director` must never be
imported or called directly from pipeline code (`app/documentary/mvp/*`) — only from here.

    result = await invoke(db, case, blueprint_row, graph)
    result.called          # True iff a paid Detective Director call was made this time
    result.scene_count     # DetectiveScene rows now available for this graph
    result.uncovered_beats # beats no directed scene covers yet (the planner gap-fills these)

Switched by `ai_config.detective_toolkit.enabled` (`config/parameters/detective_toolkit.json`). When enabled it
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
from app.core.prompts import prompt
from app.db.models import DetectiveScene, EditorialBlueprint, NarrationUnit, RevealGraph, StoryVersion
from app.documentary.detective import buildable as BD
from app.documentary.detective import director as DD
from app.documentary.detective import filler as FL
from app.documentary.detective import graph_cards as GC
from app.documentary.detective import strategy as ST
from app.documentary.detective.narration import ingest_narration
from app.longform import service as LF
from app.providers.generation.base import GenerationError
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
    filler_cards: int = 0
    pruned_actions: int = 0
    strategy_used: bool = False
    kinds_used: list[str] = field(default_factory=list)
    missing_kinds: list[str] = field(default_factory=list)
    failed_chunks: list[str] = field(default_factory=list)


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


async def _fill(db: Session, case, graph: RevealGraph, units: list[NarrationUnit], language: str,
                text_version: int) -> tuple[float, int, str | None, list[str]]:
    """Extra Director calls for the weakest beats until the alternatives fill the configured share of the film.
    Beats whose call the Director could not get right are reported and left out of the next picks."""
    cfg = ai_config.detective_toolkit
    share = BD.alternative_share(db, graph.id, language, units)
    passes, stopped, skipped, errors = 0, None, set(), []
    while share < cfg.min_alternative_share and passes < cfg.max_fill_passes:
        stopped = _cap_reached(db, case)
        if stopped:
            break
        beats = [b for b in BD.weakest_beats(db, graph.id, language, units, cfg.beats_per_call + len(skipped))
                 if b not in skipped][:cfg.beats_per_call]
        if not beats:
            break
        keys = [u.unit_key for u in units if u.beat_id in beats]
        passes += 1
        try:
            await DD.direct_scenes(db, graph.id, scene_unit_keys=keys, language=language, text_version=text_version,
                                   run_id=f"fill-{passes}",
                                   owner_instructions=prompt("documentary/detective/fill_instructions").format(prefix=f"fill{passes}"))
        except (DD.DirectorValidationFailed, GenerationError) as e:
            if isinstance(e, GenerationError) and e.kind != "invalid_output":
                raise
            skipped.update(beats)
            errors.append(f"fill {beats[0]}-{beats[-1]}: {e}"[:300])
        share = BD.alternative_share(db, graph.id, language, units)
    return share, passes, stopped, errors


async def _plan_chunk(db: Session, graph_id: int, language: str, text_version: int, beats: list[str],
                      unit_keys: list[str], units: list[NarrationUnit],
                      mapped: bool = False) -> tuple[bool, list[str], int]:
    """Map and direct one chunk. A chunk the model cannot answer (an answer that overruns, or one that stays invalid
    after its repairs) is split in half and each half planned on its own, down to one beat; what still fails is
    reported and stays uncovered for the next run. Also returns how many times a chunk had to be split, so that the
    next chunks are cut smaller. The memory of a chunk is made once: its halves are directed from it, never mapped
    again."""
    try:
        if not mapped:
            await DD.map_film(db, graph_id, language=language, text_version=text_version, scene_unit_keys=unit_keys)
            mapped = True
        await DD.direct_scenes(db, graph_id, scene_unit_keys=unit_keys, language=language, text_version=text_version)
        return True, [], 0
    except (DD.DirectorValidationFailed, GenerationError) as e:
        if isinstance(e, GenerationError) and e.kind != "invalid_output":
            raise                  # credentials, limits and timeouts are not cured by a smaller chunk
        if len(beats) == 1:
            return False, [f"{beats[0]}: {e}"[:300]], 1
        middle = len(beats) // 2
        done, errors, splits = False, [], 1
        for half in (beats[:middle], beats[middle:]):
            ok, more, inner = await _plan_chunk(db, graph_id, language, text_version, half,
                                                [u.unit_key for u in units if u.beat_id in half and u.unit_key in unit_keys],
                                                units, mapped)
            done, errors, splits = done or ok, errors + more, splits + inner
        return done, errors, splits


def _cap_reached(db: Session, case) -> str | None:
    """The case's own cap, read without `BudgetService.check`'s side effect of stopping the case."""
    snap = BudgetService(db).snapshot(case.id)
    if snap.spent_eur >= snap.cap_eur - 1e-9:
        return f"case budget €{snap.cap_eur:.2f} reached: spent €{snap.spent_eur:.2f}"
    return None


def missing_kinds(kinds: set[str], strategy: ST.Strategy | None) -> list[str]:
    """The card kinds the film still has to use: the required ones (the map and the detective board) first, then enough
    of the others to reach `min_card_kinds` kinds other than the map (the board counts), those the film strategy
    planned before the rest."""
    cfg = ai_config.detective_toolkit
    missing = [k for k in cfg.required_kinds if k not in kinds]
    have = kinds | {m for m in missing if m != "map"}
    short = cfg.min_card_kinds - len(have - {"map"})
    unmakeable = set(cfg.director_cannot_make)
    planned = [k for k in ((strategy.film.get("must_use") if strategy else None) or []) if k not in kinds
               and k not in missing and k != "map" and k not in unmakeable]
    rest = [k for k in ST.card_kinds() if k not in kinds and k not in missing and k not in planned and k != "map"
            and k not in unmakeable]
    return missing + (planned + rest)[:max(short, 0)]


def _beats_for(kind: str, strategy: ST.Strategy | None, units: list[NarrationUnit]) -> list[str]:
    """The beats a missing kind is asked for: where the card strategist planned it, else beats spread over the film."""
    cfg = ai_config.detective_toolkit
    planned = list(dict.fromkeys(p["beat"] for p in (strategy.plans.get(kind) if strategy else None) or []))
    if planned:
        return planned[:cfg.beats_per_call]
    every = list(dict.fromkeys(u.beat_id for u in units))
    step = max(len(every) // cfg.beats_per_call, 1)
    return every[::step][:cfg.beats_per_call]


async def _diversify(db: Session, case, graph: RevealGraph, units: list[NarrationUnit], language: str,
                     text_version: int, strategy: ST.Strategy | None) -> tuple[list[str], list[str]]:
    """One Director call for each kind the film is missing (a kind at most twice), until the film uses the required
    kinds and enough others or the passes are used. The detective board is the board plan's, not a Director card.
    Returns (the kinds still missing, what failed)."""
    cfg = ai_config.detective_toolkit
    tried: dict[str, int] = {}
    errors: list[str] = []
    for _ in range(cfg.max_diversity_passes):
        kinds = BD.planned_timeline(db, graph.id, language, units).kinds
        todo = [k for k in missing_kinds(kinds, strategy) if k != "detective_board" and tried.get(k, 0) < 2]
        if not todo or _cap_reached(db, case):
            break
        kind = todo[0]
        tried[kind] = tried.get(kind, 0) + 1
        beats = set(_beats_for(kind, strategy, units))
        keys = [u.unit_key for u in units if u.beat_id in beats]
        plan = json.dumps((strategy.plans.get(kind) if strategy else None) or "no placement planned", ensure_ascii=False)
        brief = prompt("documentary/detective/diversify_instructions").format(
            kind=kind, prefix=f"kind-{kind}-{tried[kind]}", needs=BD.NEEDS.get(kind, BD.DEFAULT_NEED), plan=plan[:600])
        try:
            await DD.direct_scenes(db, graph.id, scene_unit_keys=keys, language=language, text_version=text_version,
                                   run_id=f"kind-{kind}-{tried[kind]}", owner_instructions=brief)
        except (DD.DirectorValidationFailed, GenerationError) as e:
            if isinstance(e, GenerationError) and e.kind != "invalid_output":
                raise
            errors.append(f"kind {kind}: {e}"[:300])
    if cfg.require_buildable:
        BD.prune_unbuildable(db, graph.id)
    return missing_kinds(BD.planned_timeline(db, graph.id, language, units).kinds, strategy), errors


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
    waiting = [c for c in _beat_chunks(units, 1) if not set(c[0]) <= covered]
    language, text_version = _language_and_text_version(db, case, blueprint_row)
    strategy = await ST.ensure(db, case, graph.id, units, language) if ai_config.detective_toolkit.strategy_enabled else None
    called, stopped, failed = 0, None, []
    size = ai_config.detective_toolkit.beats_per_call
    while waiting:
        stopped = _cap_reached(db, case)
        if stopped:
            break
        taken, waiting = waiting[:size], waiting[size:]
        beats, unit_keys = [b for c in taken for b in c[0]], [k for c in taken for k in c[1]]
        done, errors, splits = await _plan_chunk(db, graph.id, language, text_version, beats, unit_keys, units)
        failed.extend(errors)
        size = max(size // 2, 1) if splits else min(size + 1, ai_config.detective_toolkit.beats_per_call)
        if done:
            called += 1
    uncovered = sorted(set(u.beat_id for u in units) - _directed_beats(db, graph.id, units))
    share, fill_passes = 0.0, 0
    FL.remove_filler_cards(db, graph.id)                       # made again below, after the richer cards
    GC.add_graph_cards(db, graph.id, language, units)          # the cards the stored data fills, with no model call
    if not stopped:
        _, diversity_errors = await _diversify(db, case, graph, units, language, text_version, strategy)
        failed.extend(diversity_errors)
        share, fill_passes, stopped, fill_errors = await _fill(db, case, graph, units, language, text_version)
        failed.extend(fill_errors)
    else:
        share = BD.alternative_share(db, graph.id, language, units)
    pruned = BD.prune_unbuildable(db, graph.id) if ai_config.detective_toolkit.require_buildable else 0
    filler_cards = FL.fill_with_fact_cards(db, graph.id, language, units)["cards"]
    final = BD.planned_timeline(db, graph.id, language, units)
    share, missing = final.alternative_share, missing_kinds(final.kinds, strategy)
    reason = ("a current detective plan already covers every beat of this graph" if not pending
              else f"planned {called} of {len(pending)} pending chunk(s)" if not stopped
              else f"stopped after {called} of {len(pending)} pending chunk(s): {stopped}")
    reason += (f"; alternatives fill {share:.1%} of the film after {fill_passes} fill pass(es); "
               f"{len(final.kinds)} card kinds" + (f", missing {', '.join(missing)}" if missing else ""))
    return ToolkitResult(
        enabled=True, called=called > 0 or fill_passes > 0, chunks_total=len(chunks), chunks_called=called,
        units_registered=ingested.registered, uncovered_beats=uncovered, stopped=stopped, alternative_share=share,
        fill_passes=fill_passes, filler_cards=filler_cards, pruned_actions=pruned, failed_chunks=failed,
        strategy_used=strategy is not None, kinds_used=sorted(final.kinds), missing_kinds=missing,
        target_met=share >= ai_config.detective_toolkit.min_alternative_share,
        scene_count=db.query(DetectiveScene).filter_by(graph_id=graph.id).count(), reason=reason)


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


def reset_plan(db: Session, graph_id: int) -> dict[str, int]:
    """The toolkit's own plan of a graph (its strategy, scenes, cards, claims, questions, relations, events and findings)
    removed so that it is made again from the text. The graph itself, the narration, and everything the Main Visual
    Planner found (the pictures and clips, their verdicts and the owner's approvals) are not touched. Returns how many
    rows of each kind were removed."""
    from app.db.models import (DetectiveClaim, DetectiveFinding, DetectiveKnowledgeEvent, DetectiveQuestion,
                               DetectiveRelation, DetectiveStrategy, DetectiveToolAction)

    scene_ids = [i for (i,) in db.query(DetectiveScene.id).filter_by(graph_id=graph_id)]
    removed = {"actions": db.query(DetectiveToolAction).filter(DetectiveToolAction.scene_id.in_(scene_ids))
               .delete(synchronize_session=False) if scene_ids else 0}
    for name, model in (("scenes", DetectiveScene), ("events", DetectiveKnowledgeEvent), ("relations", DetectiveRelation),
                        ("questions", DetectiveQuestion), ("claims", DetectiveClaim), ("findings", DetectiveFinding),
                        ("strategies", DetectiveStrategy)):
        removed[name] = db.query(model).filter_by(graph_id=graph_id).delete(synchronize_session=False)
    db.commit()
    return removed
