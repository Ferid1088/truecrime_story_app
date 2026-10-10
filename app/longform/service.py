"""CRUD and deterministic validation for RevealGraphs and EpistemicContracts."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from typing import Iterable

from sqlalchemy.orm import Session

from app.db.models import (
    Case,
    Contradiction,
    EditorialBlueprint,
    EpistemicClaim,
    EpistemicContractSet,
    Fact,
    OriginalMediaSegment,
    RevealAssetLink,
    RevealEdge,
    RevealExposure,
    RevealGraph,
    RevealNode,
    Source,
    StoryVersion,
    VisualAsset,
)
from app.agents.story import build_evidence_pack


class LongformValidationError(ValueError):
    """A graph or contract payload cannot be persisted safely."""


def build_reveal_graph_payload(db: Session, case_id: int, blueprint_id: int) -> dict:
    """Build a RevealGraph payload from one persisted editorial blueprint.

    `relies_on` is the authoritative narrative prerequisite ledger. The
    blueprint's `viewer_knows` list is used only to confirm that each
    dependency is already known before the beat; it never creates an edge.
    """
    blueprint, beat_ids, beat_order = _blueprint_context(db, case_id, blueprint_id)
    facts = db.query(Fact).filter_by(case_id=case_id).order_by(Fact.id).all()
    contradictions = db.query(Contradiction).filter_by(case_id=case_id).order_by(Contradiction.id).all()
    sources = db.query(Source).filter_by(case_id=case_id).order_by(Source.id).all()
    pack = build_evidence_pack(facts, contradictions, sources)
    evidence = {
        item["id"]: item
        for key in ("facts", "timeline", "contradictions")
        for item in pack.get(key) or []
    }
    raw = _loads(blueprint.blueprint_json, {})
    first: dict[str, tuple[int, str]] = {}
    for order, beat in enumerate(raw.get("beats") or []):
        beat_id = str(beat["id"])
        for node_key in beat.get("reveals") or []:
            key = str(node_key)
            if key in first:
                raise LongformValidationError(f"Reveal node {key} is revealed more than once")
            first[key] = (order, beat_id)
    refs = _blueprint_references(blueprint)
    missing_evidence = sorted(refs - set(evidence))
    if missing_evidence:
        raise LongformValidationError(
            f"Blueprint references evidence absent from the case pack: {missing_evidence}"
        )
    missing_reveals = sorted(refs - set(first))
    if missing_reveals:
        raise LongformValidationError(
            f"Blueprint references nodes without a first reveal: {missing_reveals}"
        )

    nodes = []
    for key, (order, beat_id) in first.items():
        item = evidence[key]
        category = "fact" if key.startswith("F") else "timeline" if key.startswith("T") else "contradiction"
        label = item.get("claim") or item.get("topic") or key
        nodes.append({
            "node_key": key,
            "label": str(label),
            "category": category,
            "first_revealed_beat_id": beat_id,
            "first_revealed_order": order,
            "description": item.get("description") or item.get("claim") or item.get("topic"),
            "metadata": {"evidence_id": key, "evidence": item},
        })

    edges: set[tuple[str, str]] = set()
    for order, beat in enumerate(raw.get("beats") or []):
        reveals = {str(key) for key in beat.get("reveals") or []}
        known = {str(key) for key in beat.get("viewer_knows") or []}
        for prerequisite in {str(key) for key in beat.get("relies_on") or []}:
            if prerequisite not in known or first[prerequisite][0] >= order:
                raise LongformValidationError(
                    f"Blueprint relies_on dependency {prerequisite} is not viewer-known earlier "
                    f"at beat {beat['id']}"
                )
            for dependent in reveals:
                if prerequisite != dependent:
                    edges.add((prerequisite, dependent))

    exposures = []
    for beat in raw.get("beats") or []:
        beat_id = str(beat["id"])
        for key in beat.get("reveals") or []:
            exposures.append({"beat_id": beat_id, "node_key": str(key),
                              "exposure_type": "exposed", "source_field": "reveals"})
        for key in beat.get("do_not_reveal") or []:
            exposures.append({"beat_id": beat_id, "node_key": str(key),
                              "exposure_type": "withheld", "source_field": "do_not_reveal"})

    payload = {
        "version": 1,
        "nodes": nodes,
        "edges": [{"prerequisite": a, "dependent": b} for a, b in sorted(edges)],
        "exposures": exposures,
        "asset_links": [],
    }
    validate_reveal_graph_payload(db, case_id, blueprint_id,
                                  payload["nodes"], payload["edges"], payload["exposures"])
    return payload


def _loads(value: str | None, default):
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default
    return parsed


def _blueprint_context(db: Session, case_id: int, blueprint_id: int) -> tuple[EditorialBlueprint, set[str], dict[str, int]]:
    blueprint = db.get(EditorialBlueprint, blueprint_id)
    if not blueprint or blueprint.case_id != case_id:
        raise LongformValidationError("Blueprint does not belong to the case")
    raw = _loads(blueprint.blueprint_json, {})
    beats = raw.get("beats") or []
    beat_ids = {str(b.get("id")) for b in beats if isinstance(b, dict) and b.get("id")}
    if not beat_ids:
        raise LongformValidationError("Blueprint has no beat ids")
    beat_order = {str(b["id"]): i for i, b in enumerate(beats)}
    return blueprint, beat_ids, beat_order


def spoiler_horizon(
    db: Session,
    graph_id: int,
    *,
    beat_id: str | None = None,
    timestamp_seconds: float | None = None,
) -> dict:
    """Return the exact safe and forbidden reveal sets at a story point."""
    if (beat_id is None) == (timestamp_seconds is None):
        raise LongformValidationError("Provide exactly one of beat_id or timestamp_seconds")
    graph = db.get(RevealGraph, graph_id)
    if not graph:
        raise LongformValidationError("Reveal graph not found")
    _, _, beat_order = _blueprint_context(db, graph.case_id, graph.blueprint_id)
    nodes = list(graph.nodes)
    if beat_id is not None:
        beat_key = str(beat_id)
        if beat_key not in beat_order:
            raise LongformValidationError(f"Unknown blueprint beat: {beat_key}")
        allowed = {node.node_key for node in nodes
                   if node.first_revealed_order <= beat_order[beat_key]}
        point = {"beat_id": beat_key, "beat_order": beat_order[beat_key]}
    else:
        if any(node.first_revealed_at_seconds is None for node in nodes):
            raise LongformValidationError(
                "Timestamp horizon unavailable: graph nodes lack first-reveal timestamps"
            )
        allowed = {node.node_key for node in nodes
                   if node.first_revealed_at_seconds <= timestamp_seconds}
        point = {"timestamp_seconds": timestamp_seconds}
    all_nodes = {node.node_key for node in nodes}
    return {
        "graph_id": graph.id,
        "point": point,
        "allowed": sorted(allowed),
        "forbidden": sorted(all_nodes - allowed),
    }


def check_spoiler_references(
    db: Session,
    graph_id: int,
    references: list[str],
    *,
    beat_id: str | None = None,
    timestamp_seconds: float | None = None,
) -> dict:
    """Classify requested reveal references against a spoiler horizon."""
    horizon = spoiler_horizon(db, graph_id, beat_id=beat_id,
                              timestamp_seconds=timestamp_seconds)
    allowed = set(horizon["allowed"])
    forbidden = set(horizon["forbidden"])
    requested = [str(reference) for reference in references]
    unknown = sorted(set(requested) - allowed - forbidden)
    if unknown:
        raise LongformValidationError(f"Unknown reveal references: {unknown}")
    return {
        **horizon,
        "requested": requested,
        "allowed_references": [key for key in requested if key in allowed],
        "forbidden_references": [key for key in requested if key in forbidden],
    }


def add_reveal_asset_link(
    db: Session,
    graph_id: int,
    node_key: str,
    *,
    visual_asset_id: int | None = None,
    original_media_segment_id: int | None = None,
    asset_kind: str = "visual",
    note: str | None = None,
) -> RevealAssetLink:
    """Persist a case-owned visual/audio artifact that exposes a node."""
    graph = db.get(RevealGraph, graph_id)
    if not graph:
        raise LongformValidationError("Reveal graph not found")
    node = next((row for row in graph.nodes if row.node_key == str(node_key)), None)
    if not node:
        raise LongformValidationError(f"Reveal node not found: {node_key}")
    if visual_asset_id is None and original_media_segment_id is None:
        raise LongformValidationError("Reveal asset link needs a visual or media segment id")
    if visual_asset_id is not None:
        asset = db.get(VisualAsset, int(visual_asset_id))
        if not asset or asset.case_id != graph.case_id:
            raise LongformValidationError("Visual asset does not belong to the graph case")
    if original_media_segment_id is not None:
        segment = db.get(OriginalMediaSegment, int(original_media_segment_id))
        if not segment or segment.case_id != graph.case_id:
            raise LongformValidationError("Original media segment does not belong to the graph case")
    existing = (db.query(RevealAssetLink)
                .filter_by(graph_id=graph_id, node_id=node.id,
                           visual_asset_id=visual_asset_id,
                           original_media_segment_id=original_media_segment_id)
                .first())
    if existing:
        return existing
    row = RevealAssetLink(
        graph_id=graph_id, node_id=node.id, visual_asset_id=visual_asset_id,
        original_media_segment_id=original_media_segment_id,
        asset_kind=asset_kind, note=note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def check_spoiler_visual_assets(
    db: Session,
    graph_id: int,
    visual_asset_ids: list[int],
    *,
    beat_id: str | None = None,
    timestamp_seconds: float | None = None,
) -> dict:
    """Flag visual assets whose linked reveal is forbidden at a story point."""
    horizon = spoiler_horizon(db, graph_id, beat_id=beat_id,
                              timestamp_seconds=timestamp_seconds)
    allowed = set(horizon["allowed"])
    forbidden = set(horizon["forbidden"])
    graph = db.get(RevealGraph, graph_id)
    requested = [int(asset_id) for asset_id in visual_asset_ids]
    links = [link for link in graph.asset_links if link.visual_asset_id in requested]
    linked_ids = {link.visual_asset_id for link in links}
    unknown = sorted(set(requested) - linked_ids)
    if unknown:
        raise LongformValidationError(f"Visual assets have no reveal links: {unknown}")
    asset_nodes: dict[str, list[str]] = {str(asset_id): [] for asset_id in requested}
    for link in links:
        asset_nodes[str(link.visual_asset_id)].append(link.node.node_key)
    forbidden_assets = sorted(
        int(asset_id) for asset_id, node_keys in asset_nodes.items()
        if any(node_key in forbidden for node_key in node_keys)
    )
    return {
        **horizon,
        "requested_visual_asset_ids": requested,
        "asset_nodes": asset_nodes,
        "forbidden_visual_asset_ids": forbidden_assets,
        "allowed_visual_asset_ids": sorted(set(requested) - set(forbidden_assets)),
    }


def _json(value, default):
    return json.dumps(value if value is not None else default, ensure_ascii=False, sort_keys=True)


def _validate_acyclic(node_keys: set[str], edges: list[tuple[str, str]]) -> None:
    outgoing: dict[str, set[str]] = defaultdict(set)
    indegree = {key: 0 for key in node_keys}
    for prerequisite, dependent in edges:
        if prerequisite not in node_keys or dependent not in node_keys:
            raise LongformValidationError("Reveal edge references an unknown node")
        if prerequisite == dependent:
            raise LongformValidationError("Reveal graph cannot contain a self-edge")
        if dependent not in outgoing[prerequisite]:
            outgoing[prerequisite].add(dependent)
            indegree[dependent] += 1
    queue = deque(key for key, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        key = queue.popleft()
        visited += 1
        for dependent in outgoing[key]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
    if visited != len(node_keys):
        raise LongformValidationError("Reveal graph must be acyclic")


def _validate_reachability(nodes: list[dict], edges: list[tuple[str, str]]) -> None:
    if not nodes:
        raise LongformValidationError("Reveal graph must contain at least one node")
    outgoing: dict[str, set[str]] = defaultdict(set)
    indegree = {node["node_key"]: 0 for node in nodes}
    for prerequisite, dependent in edges:
        outgoing[prerequisite].add(dependent)
        indegree[dependent] += 1
    # A reveal with no explicit relies_on is a documentary-start root. The
    # blueprint may introduce several independent threads; connecting those
    # roots artificially would invent narrative prerequisites.
    roots = {key for key, degree in indegree.items() if degree == 0}
    reached = set(roots)
    queue = deque(roots)
    while queue:
        key = queue.popleft()
        for dependent in outgoing[key]:
            if dependent not in reached:
                reached.add(dependent)
                queue.append(dependent)
    if reached != {node["node_key"] for node in nodes}:
        missing = sorted({node["node_key"] for node in nodes} - reached)
        raise LongformValidationError(f"Reveal nodes are disconnected from the documentary start: {missing}")


def _validate_reveal_order(nodes: list[dict], edges: list[tuple[str, str]], beat_order: dict[str, int]) -> None:
    first = {node["node_key"]: int(node["first_revealed_order"]) for node in nodes}
    for node in nodes:
        beat_id = str(node["first_revealed_beat_id"])
        if beat_id not in beat_order:
            raise LongformValidationError(f"Reveal node {node['node_key']} uses an unknown beat")
        if first[node["node_key"]] != beat_order[beat_id]:
            raise LongformValidationError(
                f"Reveal node {node['node_key']} first_revealed_order does not match its beat"
            )
    for prerequisite, dependent in edges:
        if first[prerequisite] >= first[dependent]:
            raise LongformValidationError(
                f"Reveal dependency {prerequisite} -> {dependent} is revealed too late"
            )


def _blueprint_references(blueprint: EditorialBlueprint) -> set[str]:
    raw = _loads(blueprint.blueprint_json, {})
    refs: set[str] = set()
    for beat in raw.get("beats") or []:
        if not isinstance(beat, dict):
            continue
        for field in ("reveals", "relies_on", "viewer_knows", "do_not_reveal"):
            value = beat.get(field) or []
            refs.update(str(item) for item in value)
        reveal_map = beat.get("reveal_map") or {}
        if isinstance(reveal_map, dict):
            refs.update(str(key) for key in reveal_map)
            for value in reveal_map.values():
                if isinstance(value, list):
                    refs.update(str(item) for item in value)
    return refs


def validate_reveal_graph_payload(
    db: Session,
    case_id: int,
    blueprint_id: int,
    nodes: list[dict],
    edges: list[dict],
    exposures: list[dict],
) -> dict:
    blueprint, beat_ids, beat_order = _blueprint_context(db, case_id, blueprint_id)
    node_keys = [str(node.get("node_key") or "") for node in nodes]
    if any(not key for key in node_keys) or len(set(node_keys)) != len(node_keys):
        raise LongformValidationError("Reveal node keys must be non-empty and unique")
    normalized_nodes = [
        {**node, "node_key": str(node["node_key"]), "first_revealed_beat_id": str(node["first_revealed_beat_id"])}
        for node in nodes
    ]
    blueprint_refs = _blueprint_references(blueprint)
    orphan_refs = sorted(blueprint_refs - set(node_keys))
    if orphan_refs:
        raise LongformValidationError(f"Blueprint references unknown reveal nodes: {orphan_refs}")
    normalized_edges = [(str(edge.get("prerequisite")), str(edge.get("dependent"))) for edge in edges]
    _validate_acyclic(set(node_keys), normalized_edges)
    _validate_reveal_order(normalized_nodes, normalized_edges, beat_order)
    _validate_reachability(normalized_nodes, normalized_edges)

    for exposure in exposures:
        if str(exposure.get("beat_id")) not in beat_ids:
            raise LongformValidationError("Reveal exposure references an unknown beat")
        if str(exposure.get("node_key")) not in set(node_keys):
            raise LongformValidationError("Reveal exposure references an unknown node")
        if exposure.get("exposure_type") not in {"exposed", "withheld"}:
            raise LongformValidationError("Reveal exposure type must be exposed or withheld")
    return {"status": "valid", "nodes": len(nodes), "edges": len(edges), "exposures": len(exposures)}


def reveal_graph_dict(graph: RevealGraph) -> dict:
    node_ids = {node.node_key: node.id for node in graph.nodes}
    return {
        "id": graph.id,
        "case_id": graph.case_id,
        "blueprint_id": graph.blueprint_id,
        "version": graph.version,
        "status": graph.status,
        "validation": _loads(graph.validation_json, {}),
        "nodes": [
            {"id": node.id, "node_key": node.node_key, "label": node.label,
             "category": node.category, "first_revealed_beat_id": node.first_revealed_beat_id,
             "first_revealed_order": node.first_revealed_order,
             "first_revealed_at_seconds": node.first_revealed_at_seconds,
             "description": node.description, "metadata": _loads(node.metadata_json, {})}
            for node in graph.nodes
        ],
        "edges": [{"prerequisite": edge.prerequisite_node.node_key,
                   "dependent": edge.dependent_node.node_key} for edge in graph.edges],
        "exposures": [{"beat_id": exposure.beat_id, "node_key": exposure.node.node_key,
                       "exposure_type": exposure.exposure_type, "source_field": exposure.source_field}
                      for exposure in graph.exposures],
        "asset_links": [
            {"node_key": link.node.node_key, "visual_asset_id": link.visual_asset_id,
             "original_media_segment_id": link.original_media_segment_id,
             "asset_kind": link.asset_kind, "note": link.note}
            for link in graph.asset_links
        ],
    }


def create_reveal_graph(db: Session, case_id: int, blueprint_id: int, payload: dict) -> RevealGraph:
    nodes = payload.get("nodes") or []
    edges = payload.get("edges") or []
    exposures = payload.get("exposures") or []
    validation = validate_reveal_graph_payload(db, case_id, blueprint_id, nodes, edges, exposures)
    if db.query(RevealGraph).filter_by(case_id=case_id, blueprint_id=blueprint_id,
                                       version=int(payload.get("version", 1))).first():
        raise LongformValidationError("Reveal graph version already exists")
    graph = RevealGraph(case_id=case_id, blueprint_id=blueprint_id,
                        version=int(payload.get("version", 1)), status="validated",
                        validation_json=_json(validation, {}))
    db.add(graph)
    db.flush()
    node_rows = {}
    for raw in nodes:
        row = RevealNode(
            graph_id=graph.id, node_key=str(raw["node_key"]), label=str(raw["label"]),
            category=str(raw["category"]), first_revealed_beat_id=str(raw["first_revealed_beat_id"]),
            first_revealed_order=int(raw["first_revealed_order"]),
            first_revealed_at_seconds=raw.get("first_revealed_at_seconds"),
            description=raw.get("description"), metadata_json=_json(raw.get("metadata"), {}),
        )
        db.add(row)
        node_rows[row.node_key] = row
    db.flush()
    for raw in edges:
        db.add(RevealEdge(graph_id=graph.id,
                          prerequisite_node_id=node_rows[str(raw["prerequisite"])].id,
                          dependent_node_id=node_rows[str(raw["dependent"])].id))
    for raw in exposures:
        db.add(RevealExposure(graph_id=graph.id, beat_id=str(raw["beat_id"]),
                              node_id=node_rows[str(raw["node_key"])].id,
                              exposure_type=raw["exposure_type"],
                              source_field=str(raw.get("source_field") or "manual")))
    for raw in payload.get("asset_links") or []:
        visual_id = raw.get("visual_asset_id")
        media_id = raw.get("original_media_segment_id")
        if visual_id is None and media_id is None:
            raise LongformValidationError("Reveal asset link needs a visual or media segment id")
        if visual_id is not None:
            asset = db.get(VisualAsset, int(visual_id))
            if not asset or asset.case_id != case_id:
                raise LongformValidationError("Visual asset does not belong to the case")
        if media_id is not None:
            segment = db.get(OriginalMediaSegment, int(media_id))
            if not segment or segment.case_id != case_id:
                raise LongformValidationError("Original media segment does not belong to the case")
        db.add(RevealAssetLink(graph_id=graph.id, node_id=node_rows[str(raw["node_key"])].id,
                               visual_asset_id=visual_id, original_media_segment_id=media_id,
                               asset_kind=str(raw.get("asset_kind") or "visual"), note=raw.get("note")))
    db.commit()
    db.refresh(graph)
    return graph


def get_reveal_graph(db: Session, graph_id: int) -> RevealGraph | None:
    return db.get(RevealGraph, graph_id)


def update_reveal_graph_status(db: Session, graph_id: int, status: str) -> RevealGraph:
    if status not in {"draft", "validated", "invalid"}:
        raise LongformValidationError("Invalid reveal graph status")
    graph = db.get(RevealGraph, graph_id)
    if not graph:
        raise LongformValidationError("Reveal graph not found")
    graph.status = status
    db.commit()
    db.refresh(graph)
    return graph


def delete_reveal_graph(db: Session, graph_id: int) -> None:
    graph = db.get(RevealGraph, graph_id)
    if not graph:
        raise LongformValidationError("Reveal graph not found")
    db.delete(graph)
    db.commit()


def contract_set_dict(contract_set: EpistemicContractSet) -> dict:
    return {
        "id": contract_set.id, "case_id": contract_set.case_id,
        "blueprint_id": contract_set.blueprint_id, "story_version_id": contract_set.story_version_id,
        "version": contract_set.version, "status": contract_set.status,
        "coverage": _loads(contract_set.coverage_json, {}),
        "validation": _loads(contract_set.validation_json, {}),
        "claims": [claim_dict(claim) for claim in contract_set.claims],
    }


def claim_dict(claim: EpistemicClaim) -> dict:
    return {"id": claim.id, "contract_set_id": claim.contract_set_id, "claim_key": claim.claim_key,
            "claim_text": claim.claim_text, "source_sentence_text": claim.source_sentence_text,
            "span_start": claim.span_start, "span_end": claim.span_end,
            "beat_id": claim.beat_id, "modality": claim.modality,
            "assertion_role": claim.assertion_role, "speaker": claim.speaker,
            "parent_claim_key": claim.parent_claim_key,
            "source_refs": _loads(claim.source_refs_json, []),
            "evidence_refs": _loads(claim.evidence_refs_json, []),
            "review_status": claim.review_status, "reviewer_notes": claim.reviewer_notes,
            "origin": claim.origin}


def create_contract_set(db: Session, case_id: int, payload: dict) -> EpistemicContractSet:
    blueprint_id = int(payload["blueprint_id"])
    story_version_id = int(payload["story_version_id"])
    _, beat_ids, _ = _blueprint_context(db, case_id, blueprint_id)
    story = db.get(StoryVersion, story_version_id)
    if not story or story.case_id != case_id:
        raise LongformValidationError("Story version does not belong to the case")
    if db.get(Case, case_id) is None:
        raise LongformValidationError("Case not found")
    claims = payload.get("claims") or []
    if len({str(c.get("claim_key")) for c in claims}) != len(claims):
        raise LongformValidationError("Claim keys must be unique")
    for claim in claims:
        if str(claim.get("beat_id")) not in beat_ids:
            raise LongformValidationError("Epistemic claim references an unknown beat")
    row = EpistemicContractSet(case_id=case_id, blueprint_id=blueprint_id,
                               story_version_id=story_version_id,
                               version=int(payload.get("version", 1)),
                               status=str(payload.get("status") or "draft"),
                               coverage_json=_json(payload.get("coverage"), {}),
                               validation_json=_json(payload.get("validation"), {}))
    db.add(row)
    db.flush()
    for claim in claims:
        db.add(EpistemicClaim(
            contract_set_id=row.id, claim_key=str(claim["claim_key"]),
            claim_text=str(claim["claim_text"]), span_start=claim.get("span_start"),
            span_end=claim.get("span_end"), beat_id=str(claim["beat_id"]),
            modality=str(claim["modality"]), source_refs_json=_json(claim.get("source_refs"), []),
            evidence_refs_json=_json(claim.get("evidence_refs"), []),
            source_sentence_text=claim.get("source_sentence_text"),
            assertion_role=str(claim.get("assertion_role") or "NARRATOR_ASSERTION"),
            speaker=claim.get("speaker"), parent_claim_key=claim.get("parent_claim_key"),
            review_status=str(claim.get("review_status") or "proposed"),
            reviewer_notes=claim.get("reviewer_notes"), origin=str(claim.get("origin") or "extracted"),
        ))
    db.commit()
    db.refresh(row)
    return row


def get_contract_set(db: Session, contract_id: int) -> EpistemicContractSet | None:
    return db.get(EpistemicContractSet, contract_id)


def update_contract_set_status(db: Session, contract_id: int, status: str) -> EpistemicContractSet:
    if status not in {"draft", "in_review", "approved", "invalid"}:
        raise LongformValidationError("Invalid contract set status")
    row = db.get(EpistemicContractSet, contract_id)
    if not row:
        raise LongformValidationError("Epistemic contract set not found")
    row.status = status
    db.commit()
    db.refresh(row)
    return row


def approve_contract_set(db: Session, contract_id: int) -> EpistemicContractSet:
    """Persist a human approval for the complete claim ledger atomically."""
    row = db.get(EpistemicContractSet, contract_id)
    if not row:
        raise LongformValidationError("Epistemic contract set not found")
    if row.status not in {"in_review", "approved"}:
        raise LongformValidationError(
            f"Only an in-review contract set can be approved (current status: {row.status})"
        )
    if not row.claims:
        raise LongformValidationError("Cannot approve an empty epistemic contract set")
    for claim in row.claims:
        claim.review_status = "approved"
    row.status = "approved"
    db.commit()
    db.refresh(row)
    return row


def delete_contract_set(db: Session, contract_id: int) -> None:
    row = db.get(EpistemicContractSet, contract_id)
    if not row:
        raise LongformValidationError("Epistemic contract set not found")
    db.delete(row)
    db.commit()


def update_claim(db: Session, claim_id: int, payload: dict) -> EpistemicClaim:
    claim = db.get(EpistemicClaim, claim_id)
    if not claim:
        raise LongformValidationError("Epistemic claim not found")
    for field in ("claim_text", "beat_id", "modality", "review_status", "reviewer_notes",
                  "source_sentence_text", "assertion_role", "speaker", "parent_claim_key"):
        if field in payload:
            setattr(claim, field, payload[field])
    if "source_refs" in payload:
        claim.source_refs_json = _json(payload["source_refs"], [])
    if "evidence_refs" in payload:
        claim.evidence_refs_json = _json(payload["evidence_refs"], [])
    db.commit()
    db.refresh(claim)
    return claim


def create_claim(db: Session, contract_id: int, payload: dict) -> EpistemicClaim:
    contract = db.get(EpistemicContractSet, contract_id)
    if not contract:
        raise LongformValidationError("Epistemic contract set not found")
    _, beat_ids, _ = _blueprint_context(db, contract.case_id, contract.blueprint_id)
    if str(payload.get("beat_id")) not in beat_ids:
        raise LongformValidationError("Epistemic claim references an unknown beat")
    claim = EpistemicClaim(
        contract_set_id=contract.id, claim_key=str(payload["claim_key"]),
        claim_text=str(payload["claim_text"]), span_start=payload.get("span_start"),
        span_end=payload.get("span_end"), beat_id=str(payload["beat_id"]),
        modality=str(payload["modality"]), source_refs_json=_json(payload.get("source_refs"), []),
        evidence_refs_json=_json(payload.get("evidence_refs"), []),
        source_sentence_text=payload.get("source_sentence_text"),
        assertion_role=str(payload.get("assertion_role") or "NARRATOR_ASSERTION"),
        speaker=payload.get("speaker"), parent_claim_key=payload.get("parent_claim_key"),
        review_status=str(payload.get("review_status") or "proposed"),
        reviewer_notes=payload.get("reviewer_notes"), origin=str(payload.get("origin") or "manual"),
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return claim


def delete_claim(db: Session, claim_id: int) -> None:
    claim = db.get(EpistemicClaim, claim_id)
    if not claim:
        raise LongformValidationError("Epistemic claim not found")
    db.delete(claim)
    db.commit()
