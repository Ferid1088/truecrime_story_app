"""Readiness reporting for rolling long-form infrastructure across cases."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from app.db.models import (
    Case,
    EditorialBlueprint,
    EpistemicContractSet,
    OriginalMediaSegment,
    RevealGraph,
    StoryVersion,
    VisualAsset,
)


@dataclass(frozen=True)
class CaseReadiness:
    case_id: int
    case_uid: str | None
    title: str
    status: str
    reasons: tuple[str, ...]
    blueprint_count: int
    usable_blueprint_ids: tuple[int, ...]
    story_version_count: int
    visual_asset_count: int
    original_media_segment_count: int
    validated_graph_count: int
    contract_set_count: int
    approved_contract_set_count: int

    def to_dict(self) -> dict:
        result = asdict(self)
        result["reasons"] = list(self.reasons)
        result["usable_blueprint_ids"] = list(self.usable_blueprint_ids)
        return result


def assess_case_readiness(db: Session, case: Case) -> CaseReadiness:
    blueprints = db.query(EditorialBlueprint).filter_by(case_id=case.id).all()
    usable = [blueprint for blueprint in blueprints if blueprint.status == "valid"]
    stories = db.query(StoryVersion).filter_by(case_id=case.id).count()
    assets = db.query(VisualAsset).filter_by(case_id=case.id).count()
    segments = db.query(OriginalMediaSegment).filter_by(case_id=case.id).count()
    graphs = db.query(RevealGraph).filter_by(case_id=case.id, status="validated").count()
    contracts = db.query(EpistemicContractSet).filter_by(case_id=case.id).all()
    approved_contracts = [contract for contract in contracts if contract.status == "approved"]

    reasons: list[str] = []
    if not blueprints:
        reasons.append("no EditorialBlueprint exists")
    elif not usable:
        reasons.append("no valid EditorialBlueprint exists")
    if assets == 0:
        reasons.append("no VisualAsset rows exist")
    if not stories:
        reasons.append("no StoryVersion exists")

    if not blueprints or not usable or assets == 0:
        status = "NOT_CANDIDATE"
    elif graphs == 0:
        status = "BLOCKED_GRAPH"
        reasons.append("no validated RevealGraph exists")
    elif not contracts:
        status = "BLOCKED_CONTRACTS"
        reasons.append("no EpistemicContractSet exists")
    elif not approved_contracts:
        status = "REVIEW_REQUIRED"
        reasons.append("EpistemicContractSet exists but none is approved")
    else:
        status = "READY"

    if segments == 0 and usable:
        reasons.append("no OriginalMediaSegment rows; audio tagging has no source rows")

    return CaseReadiness(
        case_id=case.id,
        case_uid=case.case_uid,
        title=case.canonical_title,
        status=status,
        reasons=tuple(reasons),
        blueprint_count=len(blueprints),
        usable_blueprint_ids=tuple(blueprint.id for blueprint in usable),
        story_version_count=stories,
        visual_asset_count=assets,
        original_media_segment_count=segments,
        validated_graph_count=graphs,
        contract_set_count=len(contracts),
        approved_contract_set_count=len(approved_contracts),
    )


def assess_all_cases(db: Session) -> list[CaseReadiness]:
    return [assess_case_readiness(db, case) for case in db.query(Case).order_by(Case.id)]
