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
from app.covers import proposals as COVERS
from app.covers.api import cover_view
from app.naming import agent as A
from app.naming import selection as SEL
from app.naming.corpus import get_corpus

router = APIRouter(tags=["naming"])
Lang = Literal["en", "de", "fa", "ar", "es", "it", "fr", "pt", "tr"]


def _gen():
    from app.providers.generation import get_generation_provider

    gen = get_generation_provider()
    if not gen.is_configured():
        raise HTTPException(status_code=503, detail="generation provider is not configured")
    return gen


def _embedder():
    return A.default_embedder()


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
        "recommended": bool(crit.get("recommended")), "angle": crit.get("angle"), "clarity": crit.get("clarity"),
        "reason": crit.get("reason"),
    }


def naming_state(db: Session, case: Case) -> dict:
    rows = (db.query(CaseTitleCandidate).filter(CaseTitleCandidate.case_id == case.id)
            .order_by(CaseTitleCandidate.id).all())
    langs = {}
    for lang in ai_config.multilingual.producible_languages:
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


def title_issues(row: CaseTitleCandidate | None) -> list[str]:
    """What the critics found against the film's chosen title, by the same limits the candidates had to keep."""
    if row is None:
        return ["the title was not written by the title writer (no critic scores)"]
    cfg = ai_config.case_naming
    found = []
    for name, value, limit in (("spoiler risk", row.spoiler_risk, cfg.max_spoiler_risk), ("epistemic risk", row.epistemic_risk, cfg.max_epistemic_risk),
                               ("sensationalism risk", row.sensationalism_risk, cfg.max_sensationalism_risk)):
        if value is not None and value > limit:
            found.append(f"{name} {value:.2f}")
    if row.specificity_score is not None and row.specificity_score < cfg.min_specificity:
        found.append(f"specificity {row.specificity_score:.2f}")
    return found


def titles_state(db: Session, case: Case) -> dict:
    """Per language: the chosen title, the three options, the story hook, the film cover it is paired with and the checks."""
    out = []
    for language in ai_config.multilingual.producible_languages:
        found = SEL.options(db, case, language)
        ident = T.get_identity(db, case.id, language)
        long = COVERS.current(db, case.id, language).get("long")
        cover = cover_view(db, case, long) if long else None
        chosen = ident.editorial_title if ident else None
        row = next((c for c in SEL.eligible(db, case.id, language) if chosen and c.title == chosen), None)
        issues = title_issues(row) if chosen else []
        share = SEL.repeat_share(cover["title"], chosen) if cover and chosen else 0.0
        if share > ai_config.case_naming.cover_max_repeat:
            issues.append("the cover title repeats the title: plan the cover again (no picture is made by itself)")
        out.append({"language": language, "title": chosen, "youtube_title": ident.youtube_title if ident else None, "source": (ident.title_source or ("owner" if ident and ident.title_approved else "")) if ident else "",
                    "approved": bool(ident and ident.title_approved), "published": bool(ident and ident.published), "options": found["options"], "hook": found["hook"],
                    "pairing": (cover or {}).get("validation", {}).get("pairing"),
                    "cover": {"status": cover["status"], "title": cover["title"], "preview_url": cover["preview_url"], "approved_url": cover["approved_url"]} if cover else None,
                    "validation": {"ok": not issues, "issues": issues}})
    return {"case_id": case.id, "languages": out}


class GenerateRequest(BaseModel):
    languages: list[Lang] | None = None


class ManualRequest(BaseModel):
    language: Lang
    title: str = Field(min_length=2, max_length=200)


class ApproveRequest(BaseModel):
    revise: bool = False


@router.get("/api/cases/{case_id}/titles")
def get_titles(case_id: int, db: Session = Depends(get_db)):
    return titles_state(db, _case(db, case_id))


@router.get("/api/cases/{case_id}/naming")
def get_naming(case_id: int, db: Session = Depends(get_db)):
    return naming_state(db, _case(db, case_id))


@router.post("/api/cases/{case_id}/naming/generate")
async def generate_naming(case_id: int, req: GenerateRequest | None = None,
                          db: Session = Depends(get_db)):
    """Queue title generation; provider work never blocks this request."""
    case = _case(db, case_id)
    from app.services import background_jobs

    job = background_jobs.create_job(
        db, case_id=case.id, kind="naming_generate",
        input_data={"languages": list(req.languages) if req and req.languages else None},
        timeout_seconds=20,
    )
    background_jobs.launch(job.id)
    return {"job_id": job.id, "status": job.status}


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
        raise HTTPException(status_code=422, detail=str(e)) from e
    except TitleLocked as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return T.identity_dict(ident)
