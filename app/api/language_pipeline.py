"""The language pipeline: its state, the cost estimate, a run (one language or all) and the
owner's approval of a language's final text."""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.api.deps import get_case_or_404
from app.core.ai_config import ai_config
from app.db.base import get_db
from app.db.models import StoryVersion
from app.language_pipeline import calibration
from app.language_pipeline import estimate as estimator
from app.language_pipeline.glossary import GlossaryProposer, GlossaryStore, entities_of
from app.language_pipeline.lengths import check_requested_minutes
from app.language_pipeline.materials import load_material
from app.language_pipeline.state import case_state, stage_dict
from app.language_pipeline.store import FINAL, StageStore
from app.services import background_jobs
from app.services.policy_events import record_override
from app.services.readiness import build_readiness
from app.services.story_plan_service import StoryPlanService
from app.utils import utc_now

router = APIRouter()


class RunRequest(BaseModel):
    languages: list[str] | None = None
    requested_minutes: float | None = Field(default=None, gt=0)
    tone: str = "documentary"
    # Set aside the chain of each language that was started from its current base and did not end
    # approved, and write a new one (the old rows stay as history). An approved chain is never set aside.
    new_attempt: bool = False


class ImproveRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=4000)
    requested_minutes: float | None = Field(default=None, gt=0)


class ReviseRequest(BaseModel):
    requested_minutes: float | None = Field(default=None, gt=0)


class GlossaryEdit(BaseModel):
    entity: str = Field(min_length=1, max_length=300)
    spellings: list[str] = Field(min_length=1, max_length=10)


class GlossaryApproval(BaseModel):
    approved_by: str = Field(default="operator", min_length=1, max_length=200)


class ApprovalRequest(BaseModel):
    approved_by: str = Field(default="operator", min_length=1, max_length=200)


def _languages(requested: list[str] | None) -> list[str]:
    produced = ai_config.multilingual.production_languages
    chosen = requested or produced
    unknown = [lg for lg in chosen if lg not in produced]
    if unknown:
        raise HTTPException(status_code=422, detail=f"not a production language: {unknown}")
    return chosen


def _minutes(requested: float | None) -> float:
    minutes = float(requested or ai_config.language_pipeline.requested_minutes_default)
    try:
        check_requested_minutes(minutes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return minutes


@router.get("/api/cases/{case_id}/language-pipeline")
def get_state(case_id: int, text: bool = False, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    return case_state(db, case_id, with_text=text)


@router.get("/api/cases/{case_id}/language-pipeline/estimate")
def get_estimate(case_id: int, requested_minutes: float | None = None,
                 languages: str | None = None, db: Session = Depends(get_db)):
    """Tokens and price per stage for a run, printed before it. An estimate, never a spend."""
    case = get_case_or_404(db, case_id)
    minutes = _minutes(requested_minutes)
    chosen = _languages(languages.split(",") if languages else None)
    cfg = ai_config.language_pipeline
    plan = StoryPlanService(db).usable_approved(case, cfg.draft_language, cfg.long_draft_minutes)
    if plan is None:
        plan = StoryPlanService(db).latest(case.id, cfg.draft_language)
    material = load_material(db, case, plan)
    result = estimator.estimate(material, chosen, minutes, _existing_stages(db, material),
                                calibration.measure_cache_share(db))
    result["vs_actual"] = calibration.compare(db, case.id, result)
    return result


def _existing_stages(db: Session, material) -> frozenset[str]:
    """The shared stages that already have their row for this plan and evidence."""
    draft = StageStore(db).draft_for(material)
    if draft is None:
        return frozenset()
    notes = json.loads(draft.critic_notes or "{}")
    done = {name for key, name in (("evidence_audit", "audit"), ("draft_review", "draft_review"))
            if (notes.get(key) or {}).get("status") == "ok"}
    return frozenset({"draft"} | done)


@router.post("/api/cases/{case_id}/language-pipeline/run")
async def run_pipeline(case_id: int, payload: RunRequest, db: Session = Depends(get_db)):
    """Start or resume: stages that already have their row are not run again."""
    case = get_case_or_404(db, case_id)
    languages = _languages(payload.languages)
    minutes = _minutes(payload.requested_minutes)
    provider_status = await deps.require_generation_authorized()
    report = build_readiness(db, case.id, minutes, provider_status=provider_status or {})
    readiness = report["master_readiness"]
    if readiness["status"] in ("incomplete_evidence", "failed"):
        raise HTTPException(status_code=409, detail={
            "code": "evidence_incomplete",
            "message": f"Evidence extraction is incomplete ({readiness['reason']}).",
            "evidence_readiness": report["evidence_readiness"],
            "capacity": report["narrative_capacity"]})
    if readiness["status"] == "insufficient_research":
        capacity = report["narrative_capacity"]
        raise HTTPException(status_code=409, detail={
            "code": "insufficient_research",
            "message": ("Evidence supports approximately "
                        f"{capacity['estimated_supported_minutes']} minutes; more research is recommended."),
            "capacity": capacity, "source_readiness": report["source_readiness"]})
    job = background_jobs.create_job(
        db, case_id=case.id, kind="language_pipeline",
        input_data={"languages": languages, "requested_minutes": minutes, "tone": payload.tone,
                    "new_attempt": payload.new_attempt},
        timeout_seconds=ai_config.language_pipeline.job_timeout_seconds)
    background_jobs.launch(job.id)
    return {"job_id": job.id, "status": job.status, "languages": languages,
            "requested_minutes": minutes}


@router.post("/api/cases/{case_id}/language-pipeline/{language}/improve")
async def improve_final(case_id: int, language: str, payload: ImproveRequest,
                        db: Session = Depends(get_db)):
    """The owner's instruction for one language's final text: stage 4 again, through the gates."""
    case = get_case_or_404(db, case_id)
    _languages([language])
    _final(db, case_id, language)
    await deps.require_generation_authorized()
    job = background_jobs.create_job(
        db, case_id=case.id, kind="language_improve",
        input_data={"language": language, "instruction": payload.instruction,
                    "requested_minutes": _minutes(payload.requested_minutes)},
        timeout_seconds=ai_config.language_pipeline.job_timeout_seconds)
    background_jobs.launch(job.id)
    return {"job_id": job.id, "status": job.status}


@router.post("/api/cases/{case_id}/language-pipeline/{language}/audit")
async def audit_final(case_id: int, language: str, db: Session = Depends(get_db)):
    """The one-pass evidence audit on a language's current final text: a report, nothing changes."""
    case = get_case_or_404(db, case_id)
    _languages([language])
    _final(db, case_id, language)
    await deps.require_generation_authorized()
    job = background_jobs.create_job(
        db, case_id=case.id, kind="language_final_audit", input_data={"language": language},
        timeout_seconds=ai_config.language_pipeline.job_timeout_seconds)
    background_jobs.launch(job.id)
    return {"job_id": job.id, "status": job.status}


@router.post("/api/cases/{case_id}/language-pipeline/{language}/revise")
async def revise_final(case_id: int, language: str, payload: ReviseRequest, db: Session = Depends(get_db)):
    """The one revision round of a language's delivered text, from the items of its review: once per version."""
    case = get_case_or_404(db, case_id)
    _languages([language])
    _final(db, case_id, language)
    await deps.require_generation_authorized()
    job = background_jobs.create_job(
        db, case_id=case.id, kind="language_revise",
        input_data={"language": language, "requested_minutes": _minutes(payload.requested_minutes)},
        timeout_seconds=ai_config.language_pipeline.job_timeout_seconds)
    background_jobs.launch(job.id)
    return {"job_id": job.id, "status": job.status}


@router.get("/api/cases/{case_id}/language-pipeline/{language}/final")
def get_final(case_id: int, language: str, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    row = _final(db, case_id, language)
    return stage_dict(row, with_text=True)


@router.post("/api/cases/{case_id}/language-pipeline/{language}/approve")
def approve_final(case_id: int, language: str, payload: ApprovalRequest,
                  db: Session = Depends(get_db)):
    """The owner approves the final version of ONE language; nothing downstream starts for a
    language before this, and no language waits for another. A final whose gates failed can be
    approved as is; the failures stay on the version and the override is recorded."""
    get_case_or_404(db, case_id)
    row = _final(db, case_id, language)
    if row.approved_at is not None:
        raise HTTPException(status_code=409, detail="This final version is already approved.")
    overridden = row.status != "ready"
    row.approved_by, row.approved_at = payload.approved_by, utc_now()
    db.commit()
    if overridden:
        record_override(db, actor=payload.approved_by, scope_type="story_version",
                        scope_id=str(row.id), field="gates", old_value=row.status,
                        new_value="approved_as_is",
                        reason="Final version approved over failed gates.")
    return stage_dict(row)


def _final(db: Session, case_id: int, language: str) -> StoryVersion:
    row = (db.query(StoryVersion).filter(
        StoryVersion.case_id == case_id, StoryVersion.kind == FINAL,
        StoryVersion.language == language).order_by(StoryVersion.id.desc()).first())
    if row is None:
        raise HTTPException(status_code=404, detail=f"no final version for {language} yet")
    return row



# ---------------------------------------------------------------- glossary of names and places
def _glossary_view(db: Session, case_id: int) -> dict:
    glossary = GlossaryStore(db, case_id).load()
    languages = ai_config.multilingual.production_languages
    entities: dict[str, dict] = {}
    for row in glossary.entries:
        item = entities.setdefault(row.entity, {"entity": row.entity, "kind": row.kind,
                                                "evidence_ids": json.loads(row.evidence_ids or "[]"),
                                                "languages": {}})
        item["languages"][row.language] = {"spellings": glossary.spellings_of(row), "status": row.status,
                                           "origin": row.origin}
    return {"languages": languages, "approved": {lg: glossary.approved(lg) for lg in languages},
            "entities": list(entities.values())}


@router.get("/api/cases/{case_id}/language-pipeline/glossary")
def get_glossary(case_id: int, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    return _glossary_view(db, case_id)


@router.post("/api/cases/{case_id}/language-pipeline/glossary/propose")
async def propose_glossary(case_id: int, db: Session = Depends(get_db)):
    """Creates the glossary rows of the current evidence and asks the glossary agent for the
    spellings no language has yet."""
    case = get_case_or_404(db, case_id)
    cfg = ai_config.language_pipeline
    plan = StoryPlanService(db).usable_approved(case, cfg.draft_language, cfg.long_draft_minutes)
    if plan is None:
        raise HTTPException(status_code=409, detail="there is no approved plan; run the pipeline first")
    await deps.require_generation_authorized()
    store = GlossaryStore(db, case.id)
    languages = ai_config.multilingual.production_languages
    store.sync(entities_of(load_material(db, case, plan)), languages)
    proposer = GlossaryProposer()
    for language in languages:
        await proposer.propose(db, case, language)
    return _glossary_view(db, case_id)


@router.put("/api/cases/{case_id}/language-pipeline/glossary/{language}")
def edit_glossary(case_id: int, language: str, payload: GlossaryEdit, db: Session = Depends(get_db)):
    """The owner's spellings for one entity; the entry is a proposal again until approved."""
    get_case_or_404(db, case_id)
    _languages([language])
    try:
        GlossaryStore(db, case_id).set_spellings(language, payload.entity, payload.spellings, "owner")
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return _glossary_view(db, case_id)


@router.post("/api/cases/{case_id}/language-pipeline/glossary/{language}/approve")
def approve_glossary(case_id: int, language: str, payload: GlossaryApproval,
                     db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    _languages([language])
    GlossaryStore(db, case_id).approve(language, payload.approved_by)
    return _glossary_view(db, case_id)
