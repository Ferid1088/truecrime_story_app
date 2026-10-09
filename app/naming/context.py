"""CaseNamingContext — the compact input of the naming agents.

Built from stored case data only (facts, contradictions, blueprint,
status, discovered titles). Also derives two safety views the gates and
critics use:

  hold_back / reveal_terms   what the story withholds until later (from the
                             blueprint's reveals), minus what is public at
                             the opening;
  claim_limits               whether guilt/killing may be stated at all
                             (only for a SOLVED case) and which claims are
                             disputed or weak.
"""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.agents.story import build_evidence_pack
from app.db.models import (Case, Contradiction, EditorialBlueprint, Fact, Source)
from app.identity.titles import public_status, resolution_provenance
from app.naming.normalize import norm_title

_MIN_TERM = 4
_COMMON = {"that", "this", "with", "from", "were", "have", "been", "their", "which", "after",
           "before", "there", "about", "where", "while", "would", "could", "other", "into",
           "also", "later", "first", "found", "said", "police", "case"}


def _terms(text: str) -> set[str]:
    return {t for t in norm_title(text).split() if len(t) >= _MIN_TERM and t not in _COMMON}


def _blueprint(db: Session, case_id: int) -> dict:
    row = (db.query(EditorialBlueprint)
           .filter(EditorialBlueprint.case_id == case_id, EditorialBlueprint.status != "invalid")
           .order_by(EditorialBlueprint.id.desc()).first())
    if row is None:
        return {}
    try:
        data = json.loads(row.blueprint_json or "{}")
    except (TypeError, ValueError):
        data = {}
    data.setdefault("central_question", row.central_question)
    data.setdefault("editorial_thesis", row.editorial_thesis)
    data.setdefault("human_thread", row.human_thread)
    return data


def build_context(db: Session, case: Case, *, opening_beats: int = 2) -> dict:
    facts = db.query(Fact).filter(Fact.case_id == case.id).order_by(Fact.id).all()
    contradictions = db.query(Contradiction).filter(Contradiction.case_id == case.id).all()
    sources = db.query(Source).filter(Source.case_id == case.id).order_by(Source.id).all()
    pack = build_evidence_pack(facts, contradictions, sources)
    by_id = {f["id"]: f for f in pack["facts"]} if "facts" in pack else {}
    bp = _blueprint(db, case.id)
    beats = bp.get("beats") or []

    public_names = set()
    for name in [case.canonical_title, case.location or "", case.country or "",
                 *json.loads(case.aliases_json or "[]"), *json.loads(case.people_json or "[]")]:
        public_names |= _terms(str(name))
    opening_text = " ".join([bp.get("central_question") or "", case.summary or ""])
    opening_ids: set[str] = set()
    late_ids: set[str] = set()
    for i, b in enumerate(beats):
        (opening_ids if i < opening_beats else late_ids).update(b.get("reveals") or [])
    opening_terms = _terms(opening_text) | public_names
    for fid in opening_ids:
        opening_terms |= _terms(by_id.get(fid, {}).get("claim", ""))
    hold_back, reveal_terms = [], set()
    for fid in sorted(late_ids - opening_ids):
        claim = by_id.get(fid, {}).get("claim")
        if claim:
            hold_back.append(claim[:200])
            reveal_terms |= _terms(claim)
    reveal_terms -= opening_terms

    weak = [f["claim"][:160] for f in by_id.values()
            if f.get("uncertain") or f.get("evidence_strength") in ("weak", "unverified")][:10]
    status = case.resolution_status or "UNKNOWN"
    prov = resolution_provenance(db, case)
    top_facts = sorted(by_id.values(),
                       key=lambda f: (f.get("narrative_value") == "high", f.get("confidence") or 0),
                       reverse=True)[:12]
    return {
        "canonical_case_name": case.canonical_title,
        "known_public_names": json.loads(case.aliases_json or "[]"),
        "people": json.loads(case.people_json or "[]"),
        "location": case.location, "country": case.country, "incident_date": case.incident_date,
        "summary": (case.summary or "")[:600],
        "central_question": bp.get("central_question"),
        "editorial_thesis": bp.get("editorial_thesis"),
        "human_core": bp.get("human_thread"),
        "key_facts": [{"claim": f["claim"][:200], "uncertain": f.get("uncertain", False)}
                      for f in top_facts],
        "contradictions": [{"topic": c["topic"], "description": c["description"][:200]}
                           for c in pack.get("contradictions", [])[:3]],
        "timeline": [t["claim"][:140] for t in pack.get("timeline", [])[:4]],
        "resolution_status": public_status(status) or "unknown",
        "resolution_confidence": prov["resolution_confidence"],
        "hold_back": hold_back[:10],
        "reveal_terms": sorted(reveal_terms),
        "claim_limits": {
            "may_state_guilt": status == "SOLVED",
            "weak_or_disputed_claims": weak,
        },
        "existing_research_titles": [s.title for s in sources[:25]],
    }


def context_signature(ctx: dict) -> str:
    import hashlib

    return hashlib.sha1(json.dumps(ctx, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


_WORD = re.compile(r"\w+", re.U)
