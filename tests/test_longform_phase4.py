"""Phase 4 gates: visual reveal links and spoiler checks — fixture-driven,
no snapshot database file."""

from __future__ import annotations

import json

from app.db.models import (
    Case,
    EditorialBlueprint,
    OriginalMediaSegment,
    RevealAssetLink,
    RevealGraph,
    RevealNode,
    StoryVersion,
    VisualAsset,
)
from app.longform.service import check_spoiler_visual_assets


def _graph(db) -> tuple[Case, RevealGraph, dict[str, RevealNode], dict[str, VisualAsset]]:
    """Blueprint beats B10 < B15 < B20; F005 revealed at B15, F011/F012 at B20.
    Asset A1 links F005, asset A2 links F011 and F012."""
    case = Case(canonical_title="spoiler check", slug="spoiler-check")
    db.add(case)
    db.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="direct",
                         narrative_angle="angle", story_text="text")
    db.add(story)
    db.flush()
    blueprint = EditorialBlueprint(
        case_id=case.id, story_version_id=story.id, version=1, status="valid",
        blueprint_json=json.dumps({"beats": [
            {"id": "B10", "reveals": []},
            {"id": "B15", "reveals": ["F005"]},
            {"id": "B20", "reveals": ["F011", "F012"]},
        ]}))
    db.add(blueprint)
    db.flush()
    graph = RevealGraph(case_id=case.id, blueprint_id=blueprint.id, status="validated")
    db.add(graph)
    db.flush()
    nodes = {}
    for key, order, beat in (("F005", 1, "B15"), ("F011", 2, "B20"), ("F012", 2, "B20")):
        node = RevealNode(graph_id=graph.id, node_key=key, label=key, category="fact",
                          first_revealed_beat_id=beat, first_revealed_order=order)
        db.add(node)
        nodes[key] = node
    db.flush()
    assets = {}
    for code in ("A1", "A2"):
        asset = VisualAsset(case_id=case.id, asset_code=f"VIS_{code}",
                            rights_status="editorial_review_required",
                            verification_status="verified")
        db.add(asset)
        assets[code] = asset
    db.flush()
    db.add(RevealAssetLink(graph_id=graph.id, node_id=nodes["F005"].id,
                           visual_asset_id=assets["A1"].id, asset_kind="visual"))
    for key in ("F011", "F012"):
        db.add(RevealAssetLink(graph_id=graph.id, node_id=nodes[key].id,
                               visual_asset_id=assets["A2"].id, asset_kind="visual"))
    db.commit()
    return case, graph, nodes, assets


def test_spoiler_check_allows_a_reveal_only_after_its_beat(db_session):
    case, graph, nodes, assets = _graph(db_session)

    assert db_session.query(OriginalMediaSegment).filter_by(case_id=case.id).count() == 0

    # before B15 the F005 reveal is still forbidden → the linked asset may not appear
    early = check_spoiler_visual_assets(db_session, graph.id, [assets["A1"].id], beat_id="B10")
    assert early["asset_nodes"] == {str(assets["A1"].id): ["F005"]}
    assert early["forbidden_visual_asset_ids"] == [assets["A1"].id]
    assert early["allowed_visual_asset_ids"] == []

    at_b15 = check_spoiler_visual_assets(db_session, graph.id, [assets["A1"].id], beat_id="B15")
    assert at_b15["forbidden_visual_asset_ids"] == []
    assert at_b15["allowed_visual_asset_ids"] == [assets["A1"].id]

    # the two-node asset stays forbidden until both nodes are revealed (B20)
    late = check_spoiler_visual_assets(db_session, graph.id, [assets["A2"].id], beat_id="B15")
    assert late["asset_nodes"] == {str(assets["A2"].id): ["F011", "F012"]}
    assert late["forbidden_visual_asset_ids"] == [assets["A2"].id]

    b20 = check_spoiler_visual_assets(db_session, graph.id, [assets["A2"].id], beat_id="B20")
    assert b20["forbidden_visual_asset_ids"] == []
    assert b20["allowed_visual_asset_ids"] == [assets["A2"].id]
