"""Case Naming API: generate, review, edit, approve titles per language."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.base import get_db
from app.db.models import Case, CaseTitleCandidate
from app.identity import titles as T
from app.identity.titles import TitleLocked
from app.naming import agent as A
from app.naming.corpus import get_corpus

router = APIRouter(tags=["naming"])
Lang = Literal["en", "de", "fa", "ar"]


def _gen():
    from app.providers.generation import get_generation_provider

    gen = get_generation_provider()
    if not gen.is_configured():
        raise HTTPException(status_code=503, detail="generation provider is not configured")
    return gen


def _embedder():
    """The existing bge-m3 client, or None (lexical checks still run)."""
    try:
        from app.providers import get_research_provider

        emb = getattr(get_research_provider(), "embedder", None)
        return emb if emb is not None and emb.is_configured() else None
    except Exception:  # noqa: BLE001
        return None


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def candidate_dict(c: CaseTitleCandidate) -> dict:
    crit = json.loads(c.critic_json or "{}")
    return {
        "id": c.id, "case_id": c.case_id, "language": c.language, "title": c.title,
        "status": c.status, "rejection_reason": c.rejection_reason, "origin": c.origin,
        "generation_round": c.generation_round, "title_family_id": c.title_family_id,
        "exact_collision": c.exact_collision, "near_collision_score": c.near_collision_score,
        "semantic_collision_score": c.semantic_collision_score,
        "collision_with": c.collision_with,
        "collision_status": ("exact" if c.exact_collision else
                             "near" if (c.near_collision_score or 0) >= min(
                                 ai_config.case_naming.near_token_threshold,
                                 ai_config.case_naming.near_char_threshold) else "clear"),
        "memorability": c.memorability_score, "curiosity": c.curiosity_score,
        "specificity": c.specificity_score, "brevity": c.brevity_score,
        "sensationalism_risk": c.sensationalism_risk, "spoiler_risk": c.spoiler_risk,
        "epistemic_risk": c.epistemic_risk,
        "native_quality": crit.get("native_quality"), "rank": crit.get("rank"),
        "recommended": bool(crit.get("recommended")), "angle": crit.get("angle"),
        "reason": crit.get("reason"),
    }


def naming_state(db: Session, case: Case) -> dict:
    rows = (db.query(CaseTitleCandidate).filter(CaseTitleCandidate.case_id == case.id)
            .order_by(CaseTitleCandidate.id).all())
    langs = {}
    for lang in A.ORDER:
        mine = [r for r in rows if r.language == lang]
        live = sorted((r for r in mine if r.status != "rejected"),
                      key=lambda r: json.loads(r.critic_json or "{}").get("rank") or 99)
        ident = T.get_identity(db, case.id, lang)
        langs[lang] = {
            "candidates": [candidate_dict(r) for r in live],
            "rejected": [candidate_dict(r) for r in mine if r.status == "rejected"],
            "target": ai_config.case_naming.candidates_per_language,
            "short_by": max(ai_config.case_naming.candidates_per_language - len(live), 0),
            "identity": T.identity_dict(ident) if ident else None,
        }
    family = next((r for r in rows if r.title_family_id), None)
    return {
        "case_id": case.id, "case_uid": case.case_uid,
        "title_family_id": family.title_family_id if family else None,
        "editorial_concept": family.editorial_concept if family else None,
        "provenance": T.resolution_provenance(db, case),
        "externally_verified": False,
        "corpus": get_corpus(db).counts(), "languages": langs,
    }


class GenerateRequest(BaseModel):
    languages: list[Lang] | None = None


class ManualRequest(BaseModel):
    language: Lang
    title: str = Field(min_length=2, max_length=200)


class ApproveRequest(BaseModel):
    revise: bool = False


@router.get("/api/cases/{case_id}/naming")
def get_naming(case_id: int, db: Session = Depends(get_db)):
    return naming_state(db, _case(db, case_id))


@router.post("/api/cases/{case_id}/naming/generate")
async def generate_naming(case_id: int, req: GenerateRequest | None = None,
                          db: Session = Depends(get_db)):
    """Fill every language up to exactly 7 eligible titles. Stored data
    only: no search backend is called."""
    case = _case(db, case_id)
    result = await A.generate_case_titles(db, case, _gen(), _embedder(),
                                          list(req.languages) if req and req.languages else None)
    return {**result, "state": naming_state(db, case)}


@router.post("/api/cases/{case_id}/naming/manual")
async def manual_title(case_id: int, req: ManualRequest, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    row = await A.evaluate_manual(db, case, req.language, req.title, _gen(), _embedder())
    return candidate_dict(row)


@router.post("/api/cases/{case_id}/naming/{candidate_id}/approve")
def approve_title(case_id: int, candidate_id: int, req: ApproveRequest | None = None,
                  db: Session = Depends(get_db)):
    case = _case(db, case_id)
    try:
        ident = A.approve_candidate(db, case, candidate_id, revise=bool(req and req.revise))
    except A.ApprovalRefused as e:
        raise HTTPException(status_code=422, detail=str(e))
    except TitleLocked as e:
        raise HTTPException(status_code=409, detail=str(e))
    return T.identity_dict(ident)
