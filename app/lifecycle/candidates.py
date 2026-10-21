"""Turn a discovery suggestion into a case: the one place both the Investigate button and the
autopilot's Start button do it."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.api.deps import unique_slug
from app.db.models import Case, DiscoveryCandidate
from app.lifecycle.status import set_resolution


def open_case_from_candidate(db: Session, cand: DiscoveryCandidate, language: str) -> Case:
    case = Case(
        canonical_title=cand.title,
        slug=unique_slug(db, cand.title),
        language=language,
        summary=cand.rationale,
        aliases_json=cand.aliases_json or "[]",
        people_json=cand.people_json or "[]",
        location=cand.location,
        incident_date=cand.incident_date,
        latest_development_date=cand.latest_development_date,
        origin="discovery",
    )
    db.add(case)
    db.flush()
    set_resolution(
        db, case, cand.resolution_status or "UNKNOWN", changed_by="discovery",
        reason=cand.resolution_evidence or cand.suggestion_reason or "from the suggestion",
        confidence=cand.resolution_confidence,
        sources=[{"url": u} for u in json.loads(cand.source_urls_json or "[]")][:10],
        summary=cand.resolution_evidence, commit=False)
    cand.selected = True
    cand.rejected = False
    cand.state = "accepted"
    cand.case_id = case.id
    db.commit()
    db.refresh(case)
    return case
