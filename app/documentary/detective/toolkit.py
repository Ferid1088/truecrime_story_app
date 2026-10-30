"""The `detective_toolkit` call boundary: the toolkit makes the film's cards from its text and hands the Main Visual
Planner the cards with their metadata.

    result = await invoke(db, case, blueprint_row, graph)
    result.parts            # parts of the text the analyser cut the narration into
    result.made             # cards made, by kind
    result.alternative_share # the share of the film the cards, the board and the map fill

Switched by `ai_config.detective_toolkit.enabled` (`config/parameters/detective_toolkit.json`). When enabled it registers the
blueprint's text as the graph's narration units (the planner owns the text), then (`app.documentary.detective.cards`)
makes the map, the timeline and the cards: an analyser cuts the text into parts, a card maker makes the card of a part, an
inspector checks it against the part's words and sends problems back, and a memory refuses repeats. Cards made earlier stay,
so an interrupted or budget-stopped run goes on where it stopped; a case whose own cap is reached stops cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import DetectiveScene, EditorialBlueprint, NarrationUnit, RevealGraph
from app.documentary.detective import buildable as BD
from app.documentary.detective import cards as CD
from app.documentary.detective import timeline_cards as TC
from app.documentary.detective.narration import ingest_narration
from app.longform import service as LF


@dataclass
class ToolkitResult:
    enabled: bool
    called: bool
    reason: str
    scene_count: int
    units_registered: int = 0
    parts: int = 0
    made: dict[str, int] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    repeats: int = 0
    attempts: int = 0
    stopped: str | None = None
    alternative_share: float = 0.0
    target_met: bool = False
    kinds_used: list[str] = field(default_factory=list)
    missing_kinds: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def missing_kinds(kinds: set[str]) -> list[str]:
    """The kinds every film has that the film does not have yet (the map, the board and the timeline)."""
    return [k for k in ai_config.detective_toolkit.required_kinds if k not in kinds]


async def invoke(db: Session, case, blueprint_row: EditorialBlueprint, graph: RevealGraph) -> ToolkitResult:
    """The single observable call boundary the Main Visual Planner uses to get the film's cards."""
    if not ai_config.detective_toolkit.enabled:
        return ToolkitResult(enabled=False, called=False, reason="detective_toolkit.enabled is False", scene_count=0)
    ingested = ingest_narration(db, blueprint_row, graph)
    units = db.query(NarrationUnit).filter_by(graph_id=graph.id).order_by(NarrationUnit.story_order).all()
    if not units:
        return ToolkitResult(enabled=True, called=False, scene_count=0,
                             reason="the blueprint's text has no narration to plan from")
    language = units[0].language
    TC.build_timeline_cards(db, graph.id, units, language)
    report = await CD.build_cards(db, case, graph.id, units, language)
    final = BD.planned_timeline(db, graph.id, language, units)
    share, missing = final.alternative_share, missing_kinds(final.kinds)
    reason = (f"{sum(report.made.values())} card(s) made from {report.parts} part(s) in {report.attempts} attempt(s); "
              f"cards, board and map fill {share:.1%} of the film; {len(final.kinds)} card kinds"
              + (f", missing {', '.join(missing)}" if missing else ""))
    return ToolkitResult(
        enabled=True, called=report.attempts > 0, units_registered=ingested.registered, parts=report.parts, made=report.made,
        dropped=report.dropped, repeats=report.repeats, attempts=report.attempts, stopped=report.stopped,
        alternative_share=share, target_met=share >= ai_config.detective_toolkit.min_alternative_share,
        kinds_used=sorted(final.kinds), missing_kinds=missing, errors=report.errors,
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
    """The toolkit's stage, run before the Main Visual Planner's needs stage: it makes the reveal graph and, when enabled,
    the film's cards."""
    graph = ensure_graph(db, case, blueprint_row)
    return graph, await invoke(db, case, blueprint_row, graph)


def reset_plan(db: Session, graph_id: int) -> dict[str, int]:
    """The toolkit's own plan of a graph (its scenes, cards, claims, questions, relations, events and findings)
    removed so that it is made again from the text. The graph itself, the narration, and everything the Main Visual
    Planner found (the pictures and clips, their verdicts and the owner's approvals) are not touched. Returns how many
    rows of each kind were removed."""
    from app.db.models import (DetectiveClaim, DetectiveFinding, DetectiveKnowledgeEvent, DetectiveQuestion,
                               DetectiveRelation, DetectiveToolAction)

    scene_ids = [i for (i,) in db.query(DetectiveScene.id).filter_by(graph_id=graph_id)]
    removed = {"actions": db.query(DetectiveToolAction).filter(DetectiveToolAction.scene_id.in_(scene_ids))
               .delete(synchronize_session=False) if scene_ids else 0}
    for name, model in (("scenes", DetectiveScene), ("events", DetectiveKnowledgeEvent), ("relations", DetectiveRelation),
                        ("questions", DetectiveQuestion), ("claims", DetectiveClaim), ("findings", DetectiveFinding)):
        removed[name] = db.query(model).filter_by(graph_id=graph_id).delete(synchronize_session=False)
    db.commit()
    return removed
