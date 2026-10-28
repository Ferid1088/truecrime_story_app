"""The `detective_toolkit` call boundary (architecture rule, 2026-10-28).

The Main Visual Planner is the only caller of the Detective Director (Section C). This module is
the single, observable place that call happens: `app.documentary.detective.director` must never be
imported or called directly from pipeline code (`app/documentary/mvp/*`) — only from here.

    result = await invoke(db, case, blueprint_row, graph)
    result.called        # True iff a new paid Detective Director call was made this time
    result.scene_count   # DetectiveScene rows now available for this graph

Off by default (`config/parameters/detective_toolkit.json`, `ai_config.detective_toolkit.enabled`)
so no real case's pipeline behavior or cost changes until the owner turns it on. When enabled and
no current detective plan exists for this graph, it maps the whole film once, then directs every
narrated beat, through the exact same Section C code path (`director.map_film` / `direct_scenes`)
that already persists via `app.documentary.detective.persist`. When a plan already exists for this
graph, it is returned without a new call — the toolkit never re-pays for the same graph.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import DetectiveScene, EditorialBlueprint, NarrationUnit, RevealGraph, StoryVersion
from app.documentary.detective import director as DD
from app.documentary.detective.dispatch import has_detective_plan


@dataclass
class ToolkitResult:
    enabled: bool
    called: bool
    reason: str
    scene_count: int


def _language_and_text_version(db: Session, case, blueprint_row: EditorialBlueprint) -> tuple[str, int]:
    story_version = db.get(StoryVersion, blueprint_row.story_version_id)
    text_version = story_version.version if story_version is not None else 1
    return case.language, text_version


async def invoke(db: Session, case, blueprint_row: EditorialBlueprint, graph: RevealGraph) -> ToolkitResult:
    """The single observable call boundary the Main Visual Planner uses to get a detective plan
    for `graph`. See module docstring for the gating rules."""
    if not ai_config.detective_toolkit.enabled:
        return ToolkitResult(enabled=False, called=False,
                             reason="detective_toolkit.enabled is False", scene_count=0)
    if has_detective_plan(db, graph.id):
        existing = db.query(DetectiveScene).filter_by(graph_id=graph.id).count()
        return ToolkitResult(enabled=True, called=False,
                             reason="a current detective plan already exists for this graph",
                             scene_count=existing)
    unit_keys = [r.unit_key for r in db.query(NarrationUnit).filter_by(graph_id=graph.id)
                .order_by(NarrationUnit.story_order).all()]
    if not unit_keys:
        return ToolkitResult(enabled=True, called=False,
                             reason="no narration units recorded for this graph yet", scene_count=0)
    language, text_version = _language_and_text_version(db, case, blueprint_row)
    await DD.map_film(db, graph.id, language=language, text_version=text_version)
    await DD.direct_scenes(db, graph.id, scene_unit_keys=unit_keys,
                           language=language, text_version=text_version)
    scene_count = db.query(DetectiveScene).filter_by(graph_id=graph.id).count()
    return ToolkitResult(enabled=True, called=True,
                         reason="mapped and directed the whole film for this graph",
                         scene_count=scene_count)
