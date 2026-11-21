"""The owner's "I'm finished" with the photographs of a case's covers. Finishing records which photographs of the victim are approved at that moment and then makes every cover of the case with them,
in turn when there are fewer approved photographs than covers (no paid call: the covers are composed in code, new proposed versions only, nothing approved is touched). A case whose covers
need a photograph cannot be finished with none approved."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.covers import proposals as P
from app.covers import variants as V
from app.db.models import Case, CoverPhotoApproval

log = logging.getLogger(__name__)
KEYS = ("long", *ai_config.creative_pack.clip_keys)           # the film's cover and the five Shorts'


class PhotoGateError(Exception):
    pass


def status(db: Session, case_id: int) -> dict:
    row = db.query(CoverPhotoApproval).filter_by(case_id=case_id).first()
    return {"finished": row is not None, "approved_by": row.approved_by if row else None, "photo_ids": json.loads(row.photo_ids_json) if row else [], "finished_at": row.finished_at.isoformat() if row else None}


def approved_photos(db: Session, case: Case, language: str) -> tuple[str | None, list[int]]:
    """(the entity key of the person the covers show, the ids of that person's approved photographs): the first person the cover review lists (the victim, by the cover rule)."""
    people = V.subject_candidates(db, case, language)
    if not people:
        return None, []
    person = people[0]
    from app.db.models import VisualAsset

    return person["entity_key"], [p["asset_id"] for p in person["photos"] if (asset := db.get(VisualAsset, p["asset_id"])) is not None and V.cleared(asset)[0]]


def finish(db: Session, case: Case, approved_by: str, language: str = "en") -> dict:
    """Record "finished" and make the covers: for every language that has cover texts, the film's cover and each Short's with the approved photographs in turn. Returns what was made and what was skipped (and why)."""
    entity, photo_ids = approved_photos(db, case, language)
    if entity is None or not photo_ids:
        raise PhotoGateError("No photograph is approved yet: approve at least one photograph of the person before you finish.")
    row = db.query(CoverPhotoApproval).filter_by(case_id=case.id).first() or CoverPhotoApproval(case_id=case.id, approved_by=approved_by)
    row.approved_by, row.photo_ids_json = approved_by, json.dumps(photo_ids)
    db.add(row)
    db.commit()
    made, skipped = 0, []
    for code in ai_config.creative_pack.languages:
        current = P.current(db, case.id, code)
        for index, key in enumerate(KEYS):
            if key not in current:
                continue
            try:
                made += len(V.render(db, case, code, key, subject_entity=entity, photo_asset_id=photo_ids[index % len(photo_ids)]))
            except V.VariantError as exc:
                skipped.append({"language": code, "key": key, "reason": exc.reason if hasattr(exc, "reason") else str(exc)})
    log.info("cover photos finished for case %s: %s covers made, %s skipped", case.id, made, len(skipped))
    return {**status(db, case.id), "made": made, "skipped": skipped}


def withdraw(db: Session, case_id: int) -> dict:
    db.query(CoverPhotoApproval).filter_by(case_id=case_id).delete()
    db.commit()
    return status(db, case_id)
