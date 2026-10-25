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
    DiscoveryCandidate,
    DocumentaryJob,
    HostOutfit,
    HostLook,
    OutfitItem,
    PublishRequest,
)
from app.documentary import jobs as documentary_jobs
from app.documentary import outfits as documentary_outfits
from app.lifecycle.candidates import open_case_from_candidate
from app.lifecycle.identity import IdentityIndex, candidate_identity
from app.outfits.registry import OutfitRegistry
from app.services.budget import BudgetService
from app.services.cost_model import documentary_estimate
from app.utils import utc_now

BLOCKED_STATES = ("archived", "deleted", "ignored", "duplicate", "filtered")
ACTIVE_RUN = ("running", "waiting_owner")
FILM_ACTIVE = ("queued", "running", "cancelling")


class StartError(ValueError):
    """The Start button was refused; the message says why in plain words."""


def _check_languages(languages: list[str]) -> list[str]:
    cfg = ai_config.autopilot_settings()
    chosen = list(dict.fromkeys(languages))
    if "en" not in chosen:
        raise StartError("English is required: it is the base every other language is told from.")
    unknown = [lang for lang in chosen if lang not in cfg.languages]
    if unknown:
        raise StartError(f"Not a language of the autopilot: {', '.join(unknown)}.")
    return [lang for lang in cfg.languages if lang in chosen]


def estimate_for_candidate(db: Session, cand: DiscoveryCandidate, languages: list[str]) -> dict:
    """What a run would cost, from the same model the documentary dry run uses. A suggestion has
    no case yet, so the estimate is made for an empty case: no row is created."""
    langs = _check_languages(languages)
    case = Case(canonical_title=cand.title, slug="estimate", language="en")
    result = documentary_estimate(db, case, langs)
    research = sum(line["eur"]["expected"] for line in result["lines"]
                   if line["stage"] in ("visual_search", "visual_check", "visual_second_pass"))
    return {"languages": langs, "cap_eur": float(ai_config.budget.total_cap_eur),
            "estimated_eur": result["totals"]["expected"], "visual_research_eur": round(research, 2),
            "range_eur": {"low": result["totals"]["low"], "high": result["totals"]["high"]},
            "pricing_last_verified": result["pricing_last_verified"],
            "price_warnings": result["price_warnings"]}


def _outfit_option_key(outfit: HostOutfit) -> str:
    return f"existing:{outfit.id}"


def _library_photos(db: Session, outfit_ref: str | None,
                    selected_ids: list[int] | None = None) -> list[dict]:
    if not outfit_ref or "/" not in outfit_ref:
        return []
    studio, slug = outfit_ref.split("/", 1)
    selected = set(selected_ids or [])
    return [{"id": item.id, "situation": item.situation, "stance": item.stance,
             "status": item.status, "selected": item.id in selected}
            for item in (db.query(OutfitItem)
                         .filter(OutfitItem.studio == studio, OutfitItem.outfit_slug == slug)
                         .order_by(OutfitItem.id).all())]


def outfit_options_for_candidate(db: Session, cand: DiscoveryCandidate,
                                 languages: list[str]) -> dict:
    """List the outfit choices shown before an Autopilot run starts.

    A candidate may not have a case yet.  In that situation the choices come
    from the shared, language-specific outfit registry and are materialised as
    case outfits only when the user starts the run.
    """
    langs = _check_languages(languages)
    case = db.get(Case, cand.case_id) if cand.case_id else None
    registry = OutfitRegistry()
    result = []
    for lang in langs:
        options = []
        existing = [] if case is None else (db.query(HostOutfit)
            .filter(HostOutfit.case_id == case.id, HostOutfit.language == lang,
                    HostOutfit.status != "retired")
            .order_by(HostOutfit.id).all())
        for outfit in existing:
            options.append({
                "key": _outfit_option_key(outfit), "name": outfit.name,
                "ref": outfit.outfit_ref, "source": "case", "status": outfit.status,
                "selected": outfit.status == "selected",
                "photos": _library_photos(
                    db, outfit.outfit_ref, documentary_outfits.selected_library_item_ids(db, outfit)),
            })
        existing_refs = {o.outfit_ref for o in existing if o.outfit_ref}
        for outfit in registry.all(lang):
            if outfit.outfit_id in existing_refs:
                continue
            options.append({
                "key": f"registry:{outfit.outfit_id}", "name": outfit.label,
                "ref": outfit.outfit_id, "source": "library", "status": "available",
                "selected": False,
                "photos": _library_photos(db, outfit.outfit_id),
            })
        selected = next((o["key"] for o in options if o["selected"]), None)
        result.append({"language": lang, "selected": selected, "options": options})
    return {"candidate_id": cand.id, "case_id": case.id if case else None,
            "languages": result}


def _apply_outfit_choices(db: Session, case: Case, languages: list[str],
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

    registry = OutfitRegistry()
    resolved: list[tuple[str, HostOutfit | None, str | None, list[int]]] = []
    for lang in languages:
        key = choices[lang]
        if key.startswith("existing:"):
            try:
                outfit_id = int(key.removeprefix("existing:"))
            except ValueError as exc:
                raise StartError(f"Invalid outfit choice for {lang}.") from exc
            outfit = db.get(HostOutfit, outfit_id)
            if outfit is None or outfit.case_id != case.id or outfit.language != lang \
                    or outfit.status == "retired":
                raise StartError(f"That outfit choice is not available for {lang}.")
            resolved.append((lang, outfit, None, list(photo_choices.get(lang, []))))
            continue
        if not key.startswith("registry:"):
            raise StartError(f"Invalid outfit choice for {lang}.")
        ref = key.removeprefix("registry:")
        try:
            registered = registry.resolve(ref)
        except KeyError as exc:
            raise StartError(f"That outfit is no longer available for {lang}.") from exc
        if registered.language != lang:
            raise StartError(f"That outfit belongs to another language, not {lang}.")
        outfit = (db.query(HostOutfit)
                  .filter(HostOutfit.case_id == case.id, HostOutfit.language == lang,
                          HostOutfit.outfit_ref == ref, HostOutfit.status != "retired")
                  .order_by(HostOutfit.id).first())
        resolved.append((lang, outfit, ref, list(photo_choices.get(lang, []))))

    missing_photos = [lang for lang, outfit, ref, item_ids in resolved
                      if ((outfit is not None and outfit.outfit_ref) or ref) and not item_ids]
    if missing_photos:
        raise StartError("Choose at least one existing outfit photo for: " + ", ".join(missing_photos))

    for lang, outfit, ref, item_ids in resolved:
        if outfit is None:
            registered = registry.resolve(ref)  # ref is set for registry choices
            outfit = documentary_outfits.create_outfit(
                db, case.id, lang, registered.label, outfit_ref=ref)
        documentary_outfits.select_outfit(db, outfit)
        if outfit.outfit_ref:
            result = documentary_outfits.reuse_library_photos(
                db, outfit, selected_item_ids=item_ids)
            for attached in result["attached"]:
                look = db.get(HostLook, attached["look_id"])
                if look is not None:
                    # Checking a library photo is the owner's approval for this film;
                    # no image model call is made here.
                    look.status = "approved"
                    look.approved_by = "autopilot_start"
                    look.approved_at = utc_now()
            db.commit()


def start_run(db: Session, candidate_id: int, languages: list[str],
              outfits: dict[str, str] | None = None,
              outfit_photos: dict[str, list[int]] | None = None) -> AutopilotRun:
    cand = db.get(DiscoveryCandidate, candidate_id)
    if cand is None:
        raise StartError("That suggestion does not exist.")
    langs = _check_languages(languages)
    if cand.state in BLOCKED_STATES:
        raise StartError(f"This suggestion is {cand.state}; restore it first.")
    estimate = estimate_for_candidate(db, cand, langs)
    case = db.get(Case, cand.case_id) if cand.case_id else None
    if case is not None and db.query(AutopilotRun).filter(
            AutopilotRun.case_id == case.id, AutopilotRun.status.in_(ACTIVE_RUN)).first():
        raise StartError("A run for this case is already going.")
    if case is None:
        index = IdentityIndex([i for i in IdentityIndex.from_db(db).items if i.kind == "case"])
        if index.check(candidate_identity(cand)).duplicate:
            raise StartError("This case already exists in the system.")
        case = open_case_from_candidate(db, cand, "en")
    if outfits is not None:
        _apply_outfit_choices(db, case, langs, outfits, outfit_photos)
    run = AutopilotRun(case_id=case.id, candidate_id=cand.id, languages_json=json.dumps(langs),
                       status="running", steps_json=json.dumps(S.initial_steps()),
                       estimate_json=json.dumps(estimate))
    db.add(run)
    db.commit()
    db.refresh(run)
    launch(run.id)
    return run


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
        "steps": steps, "needs_you": S.needs(run),
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
