"""Labels the ledger rows written before the dashboard existed (step, vendor, language when the stage names it, object, accuracy) and marks the repeated rows.
Rows whose language is not known stay without one: they are shown as "unknown", never guessed."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.costs import dedupe
from app.costs import registry as R
from app.db.models import HostScene, ProviderCall, ShortClip

BATCH = 5000
SQL = Path(__file__).with_name("runs_language.sql")


def label_rows(db: Session) -> int:
    """Gives every row without a label its rule-based one; returns how many rows were labelled."""
    done = 0
    while True:
        rows = db.query(ProviderCall).filter(ProviderCall.attribution.is_(None)).order_by(ProviderCall.id).limit(BATCH).all()
        if not rows:
            return done
        for row in rows:
            found = R.label(host=row.host, stage=row.stage, model=row.model, cost_eur=row.cost_eur, estimated=row.estimated, cost_source=row.cost_source, language=row.language)
            row.step, row.language, row.vendor, row.subject, row.accuracy, row.attribution = found.step, found.language, found.vendor, found.subject, found.accuracy, "rule"
        db.commit()
        done += len(rows)


def languages_from_runs(db: Session) -> int:
    """A ledger row without a language gets the language of the agent run that made the call (same case, same tokens in and out, finished within five seconds of the
    row; the nearest run if several)."""
    done = db.execute(text(SQL.read_text(encoding="utf-8")))
    db.commit()
    return done.rowcount


def heygen_from_jobs(db: Session) -> int:
    """The avatar videos' language and object from the job that asked for them: the host scene (its provider job id) or the short clip (the job ids kept in its media)."""
    owners: dict[str, tuple[str, str]] = {}
    for scene in db.query(HostScene).filter(HostScene.provider_job_id.isnot(None)):
        owners[f"heygen:{scene.provider_job_id}"] = (scene.language, f"host scene {scene.id}")
    for clip in db.query(ShortClip):
        for part, made in (json.loads(clip.media_json or "{}").get("avatars") or {}).items():
            if made.get("job_id"):
                owners[f"heygen:{made['job_id']}"] = (clip.language, f"short clip {clip.clip_key} {part}")
    done = 0
    for row in db.query(ProviderCall).filter(ProviderCall.vendor == "heygen", ProviderCall.language.is_(None), ProviderCall.request_id.in_(list(owners))):
        row.language, row.subject = owners[row.request_id]
        row.attribution = "job"
        done += 1
    db.commit()
    return done


def run(db: Session) -> dict:
    labelled = label_rows(db)
    return {"labelled": labelled, "from_runs": languages_from_runs(db), "from_jobs": heygen_from_jobs(db), "duplicates_marked": dedupe.mark_duplicates(db)}
