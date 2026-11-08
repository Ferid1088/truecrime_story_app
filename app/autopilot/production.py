"""The production choices of a case, made on the case page: which languages to produce and the host outfit of each,
saved first and then started with the Autopilot button. A language that is not saved is never produced; nothing
starts before the case has an approved name."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.autopilot import service as SV
from app.autopilot import state as S
from app.core.ai_config import ai_config
from app.db.models import AutopilotRun, Case, CaseProduction, EpisodeIdentity
from app.documentary import outfits as documentary_outfits
from app.utils import utc_now


def allowed_languages() -> list[str]:
    return list(ai_config.autopilot_settings().languages)


def saved_languages(db: Session, case: Case) -> list[str] | None:
    """The languages the owner saved for this case, in the autopilot's order; None before the first save."""
    row = db.get(CaseProduction, case.id)
    if row is None:
        return None
    chosen = set(json.loads(row.languages_json))
    return [lang for lang in allowed_languages() if lang in chosen]


def name_approved(db: Session, case: Case) -> bool:
    """The case is confirmed when its English title was approved (the naming flow), not merely copied from the draft."""
    english = ai_config.language_pipeline.draft_language
    identity = (db.query(EpisodeIdentity)
                .filter(EpisodeIdentity.case_id == case.id, EpisodeIdentity.language == english).first())
    return bool(identity and identity.title_approved)


def latest_run(db: Session, case: Case) -> AutopilotRun | None:
    return (db.query(AutopilotRun).filter(AutopilotRun.case_id == case.id)
            .order_by(AutopilotRun.id.desc()).first())


def blockers(db: Session, case: Case) -> list[str]:
    """Why the Autopilot button cannot be pressed yet, in plain words (empty when it can)."""
    out = []
    if not name_approved(db, case):
        out.append("Approve the case's English name first (Naming tab): nothing is produced before the case is confirmed.")
    languages = saved_languages(db, case)
    if not languages:
        out.append("Save the languages to produce.")
    else:
        missing = [lang for lang in languages if documentary_outfits.selected_outfit(db, case.id, lang) is None]
        if missing:
            out.append("Choose and save a host outfit for: " + ", ".join(missing) + ".")
    run = latest_run(db, case)
    if run is not None and run.status in SV.ACTIVE_RUN:
        out.append("An Autopilot run for this case is already going.")
    return out


def view(db: Session, case: Case) -> dict:
    saved = saved_languages(db, case)
    run = latest_run(db, case)
    chosen = saved if saved is not None else [lang for lang in allowed_languages()
                                              if lang in ai_config.multilingual.production_languages]
    return {
        "case_id": case.id,
        "name": {"approved": name_approved(db, case), "title": case.canonical_title},
        "allowed_languages": allowed_languages(),
        "required_language": ai_config.language_pipeline.draft_language,
        "languages": chosen, "saved": saved is not None,
        "outfits": [documentary_outfits.choice_options(db, case.id, lang) for lang in allowed_languages()],
        "blockers": blockers(db, case),
        "run": SV.run_view(db, run) if run is not None else None,
    }


def save(db: Session, case: Case, languages: list[str], outfits: dict[str, str] | None = None,
         outfit_photos: dict[str, list[int]] | None = None) -> dict:
    """Save the languages (English is always one) and the outfits chosen so far. Outfits may be saved language by
    language; the Autopilot refuses to start until every saved language has one."""
    chosen = SV.check_languages(languages)
    run = latest_run(db, case)
    if run is not None and run.status in SV.ACTIVE_RUN:
        raise SV.StartError("The Autopilot is running for this case: its languages are fixed until it ends.")
    chosen_outfits = {lang: key for lang, key in (outfits or {}).items() if lang in chosen}
    if chosen_outfits:
        SV.apply_outfit_choices(db, case, list(chosen_outfits), chosen_outfits, outfit_photos)
    row = db.get(CaseProduction, case.id)
    if row is None:
        row = CaseProduction(case_id=case.id)
        db.add(row)
    row.languages_json, row.saved_at = json.dumps(chosen), utc_now()
    db.commit()
    return view(db, case)


def start(db: Session, case: Case) -> AutopilotRun:
    """Press the Autopilot button: a run for exactly the saved languages. English research and film come first."""
    reasons = blockers(db, case)
    if reasons:
        raise SV.StartError(" ".join(reasons))
    languages = saved_languages(db, case) or []
    run = AutopilotRun(case_id=case.id, candidate_id=None, languages_json=json.dumps(languages), status="running",
                       steps_json=json.dumps(S.initial_steps()), estimate_json=json.dumps({}))
    db.add(run)
    db.commit()
    db.refresh(run)
    SV.launch(run.id)
    return run
