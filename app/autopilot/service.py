"""Start, estimate, view and cancel an autopilot run."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.autopilot import state as S
from app.autopilot.runner import approved_final, explain_unapproved
from app.core.ai_config import ai_config
from app.db.models import (
    AutopilotRun,
    Case,
    CaseTitleCandidate,
    DocumentaryJob,
    PublishRequest,
)
from app.documentary import jobs as documentary_jobs
from app.documentary import outfits as documentary_outfits
from app.services.budget import BudgetService
from app.services.cost_model import documentary_estimate
from app.utils import utc_now

ACTIVE_RUN = ("running", "waiting_owner")
FILM_ACTIVE = ("queued", "running", "cancelling")


class StartError(ValueError):
    """The Start button was refused; the message says why in plain words."""


def estimate_for_case(db: Session, case: Case, languages: list[str]) -> dict:
    """What the saved languages would cost, from the same model the documentary dry run uses."""
    result = documentary_estimate(db, case, languages)
    research = sum(line["eur"]["expected"] for line in result["lines"]
                   if line["stage"] in ("visual_search", "visual_check", "visual_second_pass"))
    return {"languages": languages, "cap_eur": float(ai_config.budget.total_cap_eur),
            "estimated_eur": result["totals"]["expected"], "visual_research_eur": round(research, 2),
            "range_eur": {"low": result["totals"]["low"], "high": result["totals"]["high"]},
            "pricing_last_verified": result["pricing_last_verified"],
            "price_warnings": result["price_warnings"]}


def default_languages() -> list[str]:
    """The languages a run covers when none are named: the autopilot's languages that are production languages
    (the optional ones are produced only when asked for)."""
    return [lang for lang in ai_config.autopilot_settings().languages
            if lang in ai_config.multilingual.production_languages]


def check_languages(languages: list[str]) -> list[str]:
    cfg = ai_config.autopilot_settings()
    chosen = list(dict.fromkeys(languages))
    if "en" not in chosen:
        raise StartError("English is required: it is the base every other language is told from.")
    unknown = [lang for lang in chosen if lang not in cfg.languages]
    if unknown:
        raise StartError(f"Not a language of the autopilot: {', '.join(unknown)}.")
    return [lang for lang in cfg.languages if lang in chosen]


def apply_outfit_choices(db: Session, case: Case, languages: list[str],
                          choices: dict[str, str], photo_choices: dict[str, list[int]] | None = None) -> None:
    """Persist the outfit and checked library photos before the worker starts."""
    photo_choices = photo_choices or {}
    missing = [lang for lang in languages if not choices.get(lang)]
    if missing:
        raise StartError("Choose an outfit before starting for: " + ", ".join(missing))
    extra = sorted(set(choices) - set(languages))
    if extra:
        raise StartError("Outfit choices contain languages not in this run: " + ", ".join(extra))
    extra_photos = sorted(set(photo_choices) - set(languages))
    if extra_photos:
        raise StartError("Photo choices contain languages not in this run: " + ", ".join(extra_photos))

    resolved: list[tuple[str, str, list[int]]] = []
    for lang in languages:
        try:
            outfit, ref = documentary_outfits.resolve_choice(db, case.id, lang, choices[lang])
        except documentary_outfits.OutfitError as exc:
            raise StartError(str(exc)) from exc
        resolved.append((lang, choices[lang], list(photo_choices.get(lang, []))))
        if ((outfit is not None and outfit.outfit_ref) or ref) and not resolved[-1][2]:
            raise StartError(f"Choose at least one existing outfit photo for: {lang}")

    for lang, key, item_ids in resolved:
        try:
            documentary_outfits.apply_choice(db, case.id, lang, key, item_ids, "autopilot_start")
        except documentary_outfits.OutfitError as exc:
            raise StartError(str(exc)) from exc


def launch(run_id: int) -> None:
    """With an external worker the API only records the run: the worker picks it up, so
    restarting the API never stops a run."""
    from app.autopilot.runner import AutopilotRunner
    from app.core.config import settings
    from app.core.tasks import spawn

    if settings.job_runner == "external":
        return
    spawn(AutopilotRunner().run(run_id), name=f"autopilot-{run_id}")


def cancel_run(db: Session, run: AutopilotRun) -> AutopilotRun:
    run.status, run.completed_at, run.updated_at = "cancelled", utc_now(), utc_now()
    for job_id in S.film_job_ids(run).values():
        job = db.get(DocumentaryJob, job_id)
        if job is not None and job.status in ("queued", "running"):
            job.status = "cancelling"
    db.commit()
    return run


def retry_phase(db: Session, run: AutopilotRun, phase: str) -> AutopilotRun:
    """Retry a red phase: it gets a fresh start and the run is driven again."""
    if phase not in S.PHASES:
        raise StartError(f"Not a phase of the autopilot: {phase}.")
    if run.status in ("completed", "cancelled"):
        raise StartError(f"The run is {run.status}; start a new one.")
    if not next(h for h in S.health(run) if h["phase"] == phase)["can_retry"]:
        raise StartError(f"The phase {phase} is not stopped, so there is nothing to retry.")
    S.reset_phase(run, phase)
    run.status, run.error, run.updated_at = "running", None, utc_now()
    db.commit()
    launch(run.id)
    return run


def _language_view(db: Session, run: AutopilotRun, case: Case, lang: str) -> dict:
    job_id = S.film_job_ids(run).get(lang)
    job = db.get(DocumentaryJob, job_id) if job_id else None
    titles = (db.query(CaseTitleCandidate)
              .filter(CaseTitleCandidate.case_id == case.id, CaseTitleCandidate.language == lang,
                      CaseTitleCandidate.status != "rejected")
              .order_by(CaseTitleCandidate.id).all())
    return {"film_job": documentary_jobs.job_dict(job) if job is not None else None,
            "titles": [{"id": t.id, "title": t.title, "selected": t.status == "selected"}
                       for t in titles]}


def _live_texts_detail(db: Session, case_id: int, languages: list[str]) -> str:
    """The stored "texts" step detail only changes when the autopilot's own pass writes it — a
    manually-triggered /revise or /improve (case 15: 5 rounds outside the autopilot's own attempts)
    moves the real state forward without the step ever refreshing, so the UI can show an hours-old
    snapshot while the text keeps changing underneath it. Recomputed live on every read instead."""
    parts = []
    for lang in languages:
        if approved_final(db, case_id, lang) is not None:
            parts.append(f"{lang}: approved")
        else:
            parts.append(f"{lang}: {explain_unapproved(db, case_id, lang) or 'not yet approved'}")
    return "; ".join(parts)


def run_view(db: Session, run: AutopilotRun) -> dict:
    case = db.get(Case, run.case_id)
    assert case is not None
    snapshot = BudgetService(db).snapshot(case.id)
    languages = json.loads(run.languages_json)
    steps = S.steps(run)
    for step in steps:
        if step["name"] == "texts" and step["status"] not in ("done", "pending"):
            step["detail"] = _live_texts_detail(db, case.id, languages)
    return {
        "id": run.id, "status": run.status, "stage": run.stage, "error": run.error,
        "created_at": run.created_at, "updated_at": run.updated_at,
        "completed_at": run.completed_at,
        "case": {"id": case.id, "title": case.canonical_title},
        "estimate": json.loads(run.estimate_json or "{}"),
        "budget": {"cap_eur": snapshot.cap_eur, "spent_eur": snapshot.spent_eur},
        "steps": steps, "health": S.health(run), "needs_you": S.needs(run),
        "languages": {lang: _language_view(db, run, case, lang) for lang in languages},
        "publish_requests": db.query(PublishRequest).filter(
            PublishRequest.run_id == run.id).count(),
    }


def resume_active_runs(db: Session, *, held_case_ids: set[int] | None = None) -> list[int]:
    """Run ids a worker should be driving: every run that has not finished or been
    cancelled. Runs of held cases (paused/stopped/trashed) are left alone."""
    query = db.query(AutopilotRun).filter(AutopilotRun.status.in_(ACTIVE_RUN))
    if held_case_ids:
        query = query.filter(AutopilotRun.case_id.notin_(held_case_ids))
    return [r.id for r in query.order_by(AutopilotRun.id).all()]
