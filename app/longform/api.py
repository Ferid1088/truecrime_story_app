"""HTTP CRUD for persisted long-form reveal and epistemic artifacts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Case, EpistemicContractSet, RevealGraph
from app.longform import service as S

router = APIRouter(tags=["long-form-integrity"])


class RevealGraphPayload(BaseModel):
    blueprint_id: int
    version: int = Field(default=1, ge=1)
    nodes: list[dict]
    edges: list[dict] = []
    exposures: list[dict] = []
    asset_links: list[dict] = []


class StatusPatch(BaseModel):
    status: str


class SpoilerReferenceQuery(BaseModel):
    references: list[str]


class RevealAssetLinkCreate(BaseModel):
    node_key: str
    visual_asset_id: int | None = None
    original_media_segment_id: int | None = None
    asset_kind: str = "visual"
    note: str | None = None


class SpoilerVisualQuery(BaseModel):
    visual_asset_ids: list[int]


class ContractSetPayload(BaseModel):
    blueprint_id: int
    story_version_id: int
    version: int = Field(default=1, ge=1)
    status: str = "draft"
    coverage: dict = {}
    validation: dict = {}
    claims: list[dict] = []


class ClaimPatch(BaseModel):
    claim_text: str | None = None
    beat_id: str | None = None
    modality: str | None = None
    review_status: str | None = None
    reviewer_notes: str | None = None
    source_refs: list = []
    evidence_refs: list = []
    source_sentence_text: str | None = None
    assertion_role: str | None = None
    speaker: str | None = None
    parent_claim_key: str | None = None


class ClaimCreate(BaseModel):
    claim_key: str
    claim_text: str
    beat_id: str
    modality: str
    span_start: int | None = None
    span_end: int | None = None
    source_refs: list = []
    evidence_refs: list = []
    source_sentence_text: str | None = None
    assertion_role: str = "NARRATOR_ASSERTION"
    speaker: str | None = None
    parent_claim_key: str | None = None
    review_status: str = "proposed"
    reviewer_notes: str | None = None
    origin: str = "manual"


def _case(db: Session, case_id: int) -> Case:
    row = db.get(Case, case_id)
    if not row:
        raise HTTPException(status_code=404, detail="Case not found")
    return row


def _error(exc: S.LongformValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/api/cases/{case_id}/reveal-graphs")
def list_reveal_graphs(case_id: int, db: Session = Depends(get_db)):
    _case(db, case_id)
    rows = db.query(RevealGraph).filter(RevealGraph.case_id == case_id).order_by(RevealGraph.id).all()
    return [S.reveal_graph_dict(row) for row in rows]


@router.post("/api/cases/{case_id}/reveal-graphs", status_code=201)
def create_reveal_graph(case_id: int, payload: RevealGraphPayload, db: Session = Depends(get_db)):
    _case(db, case_id)
    try:
        row = S.create_reveal_graph(db, case_id, payload.blueprint_id, payload.model_dump())
    except S.LongformValidationError as exc:
        raise _error(exc) from exc
    return S.reveal_graph_dict(row)


@router.get("/api/reveal-graphs/{graph_id}")
def get_reveal_graph(graph_id: int, db: Session = Depends(get_db)):
    row = S.get_reveal_graph(db, graph_id)
    if not row:
        raise HTTPException(status_code=404, detail="Reveal graph not found")
    return S.reveal_graph_dict(row)


@router.get("/api/reveal-graphs/{graph_id}/spoiler-horizon")
def get_spoiler_horizon(
    graph_id: int,
    beat_id: str | None = None,
    timestamp_seconds: float | None = None,
    db: Session = Depends(get_db),
):
    try:
        return S.spoiler_horizon(db, graph_id, beat_id=beat_id,
                                 timestamp_seconds=timestamp_seconds)
    except S.LongformValidationError as exc:
        raise _error(exc) from exc


@router.post("/api/reveal-graphs/{graph_id}/spoiler-check")
def check_spoiler_references(
    graph_id: int,
    payload: SpoilerReferenceQuery,
    beat_id: str | None = None,
    timestamp_seconds: float | None = None,
    db: Session = Depends(get_db),
):
    try:
        return S.check_spoiler_references(
            db, graph_id, payload.references, beat_id=beat_id,
            timestamp_seconds=timestamp_seconds,
        )
    except S.LongformValidationError as exc:
        raise _error(exc) from exc


@router.post("/api/reveal-graphs/{graph_id}/asset-links", status_code=201)
def add_asset_link(
    graph_id: int,
    payload: RevealAssetLinkCreate,
    db: Session = Depends(get_db),
):
    try:
        row = S.add_reveal_asset_link(db, graph_id, payload.node_key,
                                      visual_asset_id=payload.visual_asset_id,
                                      original_media_segment_id=payload.original_media_segment_id,
                                      asset_kind=payload.asset_kind, note=payload.note)
        return {"id": row.id, "graph_id": row.graph_id, "node_key": row.node.node_key,
                "visual_asset_id": row.visual_asset_id,
                "original_media_segment_id": row.original_media_segment_id,
                "asset_kind": row.asset_kind, "note": row.note}
    except S.LongformValidationError as exc:
        raise _error(exc) from exc


@router.post("/api/reveal-graphs/{graph_id}/spoiler-visual-check")
def check_spoiler_visuals(
    graph_id: int,
    payload: SpoilerVisualQuery,
    beat_id: str | None = None,
    timestamp_seconds: float | None = None,
    db: Session = Depends(get_db),
):
    try:
        return S.check_spoiler_visual_assets(
            db, graph_id, payload.visual_asset_ids, beat_id=beat_id,
            timestamp_seconds=timestamp_seconds,
        )
    except S.LongformValidationError as exc:
        raise _error(exc) from exc


@router.patch("/api/reveal-graphs/{graph_id}")
def patch_reveal_graph(graph_id: int, payload: StatusPatch, db: Session = Depends(get_db)):
    try:
        return S.reveal_graph_dict(S.update_reveal_graph_status(db, graph_id, payload.status))
    except S.LongformValidationError as exc:
        raise _error(exc) from exc


@router.delete("/api/reveal-graphs/{graph_id}", status_code=204)
def remove_reveal_graph(graph_id: int, db: Session = Depends(get_db)):
    try:
        S.delete_reveal_graph(db, graph_id)
    except S.LongformValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/cases/{case_id}/epistemic-contracts")
def list_contract_sets(case_id: int, db: Session = Depends(get_db)):
    _case(db, case_id)
    rows = db.query(EpistemicContractSet).filter(EpistemicContractSet.case_id == case_id).order_by(EpistemicContractSet.id).all()
    return [S.contract_set_dict(row) for row in rows]


@router.post("/api/cases/{case_id}/epistemic-contracts", status_code=201)
def create_contract_set(case_id: int, payload: ContractSetPayload, db: Session = Depends(get_db)):
    _case(db, case_id)
    try:
        row = S.create_contract_set(db, case_id, payload.model_dump())
    except S.LongformValidationError as exc:
        raise _error(exc) from exc
    return S.contract_set_dict(row)


@router.get("/api/epistemic-contracts/{contract_id}")
def get_contract_set(contract_id: int, db: Session = Depends(get_db)):
    row = S.get_contract_set(db, contract_id)
    if not row:
        raise HTTPException(status_code=404, detail="Epistemic contract set not found")
    return S.contract_set_dict(row)


@router.patch("/api/epistemic-contracts/{contract_id}")
def patch_contract_set(contract_id: int, payload: StatusPatch, db: Session = Depends(get_db)):
    try:
        return S.contract_set_dict(S.update_contract_set_status(db, contract_id, payload.status))
    except S.LongformValidationError as exc:
        raise _error(exc) from exc


@router.post("/api/epistemic-contracts/{contract_id}/approve")
def approve_contract_set(contract_id: int, db: Session = Depends(get_db)):
    try:
        return S.contract_set_dict(S.approve_contract_set(db, contract_id))
    except S.LongformValidationError as exc:
        raise _error(exc) from exc


@router.delete("/api/epistemic-contracts/{contract_id}", status_code=204)
def remove_contract_set(contract_id: int, db: Session = Depends(get_db)):
    try:
        S.delete_contract_set(db, contract_id)
    except S.LongformValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/api/epistemic-claims/{claim_id}")
def patch_claim(claim_id: int, payload: ClaimPatch, db: Session = Depends(get_db)):
    try:
        return S.claim_dict(S.update_claim(db, claim_id, payload.model_dump(exclude_unset=True)))
    except S.LongformValidationError as exc:
        raise _error(exc) from exc


@router.post("/api/epistemic-contracts/{contract_id}/claims", status_code=201)
def create_claim(contract_id: int, payload: ClaimCreate, db: Session = Depends(get_db)):
    try:
        return S.claim_dict(S.create_claim(db, contract_id, payload.model_dump()))
    except S.LongformValidationError as exc:
        raise _error(exc) from exc


@router.delete("/api/epistemic-claims/{claim_id}", status_code=204)
def remove_claim(claim_id: int, db: Session = Depends(get_db)):
    try:
        S.delete_claim(db, claim_id)
    except S.LongformValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
