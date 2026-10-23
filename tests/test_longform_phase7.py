"""Phase 7 gate: per-case rollout readiness reporting — fixture-driven, no
snapshot database file."""

from __future__ import annotations

from app.db.models import (
    Case,
    EditorialBlueprint,
    EpistemicContractSet,
    RevealGraph,
    StoryVersion,
    VisualAsset,
)
from app.longform.rollout import assess_all_cases


def _ready_case(db, slug: str) -> Case:
    """A case with everything readiness wants: a valid blueprint on a story version,
    visual assets, a validated graph and an approved contract set."""
    case = Case(canonical_title=f"case {slug}", slug=slug)
    db.add(case)
    db.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="direct",
                         narrative_angle="angle", story_text="text")
    db.add(story)
    db.flush()
    blueprint = EditorialBlueprint(case_id=case.id, story_version_id=story.id,
                                   version=1, status="valid")
    db.add(blueprint)
    db.flush()
    for n in range(3):
        db.add(VisualAsset(case_id=case.id, asset_code=f"VIS_{n:06d}"))
    db.add(RevealGraph(case_id=case.id, blueprint_id=blueprint.id, status="validated"))
    db.add(EpistemicContractSet(case_id=case.id, blueprint_id=blueprint.id,
                                story_version_id=story.id, status="approved"))
    db.commit()
    return case


def _bare_case(db, slug: str) -> Case:
    case = Case(canonical_title=f"case {slug}", slug=slug)
    db.add(case)
    db.commit()
    return case


def test_readiness_reports_bare_cases_as_not_candidate_and_built_case_ready(db_session):
    ready = _ready_case(db_session, "ready")
    bare = [_bare_case(db_session, f"bare-{i}") for i in range(2)]

    rows = assess_all_cases(db_session)
    by_id = {row.case_id: row for row in rows}

    for case in bare:
        row = by_id[case.id]
        assert row.status == "NOT_CANDIDATE"
        assert row.blueprint_count == 0
        assert row.visual_asset_count == 0
        assert "no EditorialBlueprint exists" in row.reasons
        assert "no VisualAsset rows exist" in row.reasons

    row = by_id[ready.id]
    assert row.status == "READY"
    assert len(row.usable_blueprint_ids) == 1
    assert row.visual_asset_count == 3
    assert row.original_media_segment_count == 0
    assert row.validated_graph_count == 1
    assert row.contract_set_count == 1
    assert row.approved_contract_set_count == 1
