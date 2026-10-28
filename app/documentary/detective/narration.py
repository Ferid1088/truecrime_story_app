"""The film's text, handed from the Main Visual Planner to the Detective Toolkit.

The planner owns the text: the blueprint's beats say which paragraphs of which act each beat tells.
This turns that into the narration units the toolkit reads (one per sentence, stable keys
`<beat>-<n>`), in story order, split the way the voice pipeline splits them so a unit lines up with
a spoken sentence. Registering is idempotent: a graph that already has units is left alone, because a
graph belongs to one blueprint and a blueprint to one text, so its units cannot be stale; changed
text for an existing key is refused by `add_narration_unit`, never silently rewritten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.story import _paragraphs, strip_section_markers  # _paragraphs: the blueprint validated its ranges with this exact split
from app.db.models import EditorialBlueprint, NarrationUnit, RevealGraph, StoryVersion
from app.documentary.blueprint import version_sections
from app.documentary.memory import add_narration_unit
from app.documentary.voice_blocks import split_sentences


@dataclass(frozen=True)
class IngestResult:
    registered: int
    existing: int
    beats_without_text: list[str]


def blueprint_units(db: Session, blueprint_row: EditorialBlueprint) -> tuple[list[dict], list[str], str]:
    """(units, beat ids that resolved to no text, language) for the blueprint's own story version."""
    story = db.get(StoryVersion, blueprint_row.story_version_id)
    if story is None:
        raise ValueError(f"blueprint {blueprint_row.id} has no story version")
    language = story.language or "en"
    acts = {s["id"]: _paragraphs(s["text"]) for s in version_sections(story)}
    units: list[dict] = []
    empty: list[str] = []
    for beat in json.loads(blueprint_row.blueprint_json or "{}").get("beats") or []:
        paragraphs = acts.get(beat.get("act_id") or "full") or []
        first, last = (beat.get("paragraphs") or [0, -1])[0], (beat.get("paragraphs") or [0, -1])[-1]
        sentences = [s for p in paragraphs[first - 1:last]
                     for s in split_sentences(strip_section_markers(p), language)]
        if not sentences:
            empty.append(beat["id"])
        units.extend({"unit_key": f"{beat['id']}-{n}", "beat_id": beat["id"], "text": sentence}
                     for n, sentence in enumerate(sentences, 1))
    for order, unit in enumerate(units):
        unit["story_order"] = order
    return units, empty, language


def ingest_narration(db: Session, blueprint_row: EditorialBlueprint, graph: RevealGraph) -> IngestResult:
    """Register the blueprint's text as the graph's narration units unless the graph already has them."""
    existing = db.query(NarrationUnit).filter_by(graph_id=graph.id).count()
    if existing:
        return IngestResult(registered=0, existing=existing, beats_without_text=[])
    units, empty, language = blueprint_units(db, blueprint_row)
    for unit in units:
        add_narration_unit(db, graph.id, unit["unit_key"], unit["beat_id"], unit["story_order"],
                           language, unit["text"])
    db.flush()
    return IngestResult(registered=len(units), existing=0, beats_without_text=empty)
