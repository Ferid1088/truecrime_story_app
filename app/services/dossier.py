"""Canonical Research Dossier (Parts 12/13).

Merges KNOWLEDGE — never raw prose — from all source kinds into one
canonical evidence view: web/article sources, engine research and
transcript-derived claims (via Facts). Every dossier entry carries full
provenance: source refs, independent-source count, languages, confidence,
status — and for transcript-derived items, the claim -> segment -> video
-> timestamp trace.
"""

import json

from sqlalchemy.orm import Session

from app.db.models import (
    Case,
    ClaimCluster,
    Contradiction,
    Fact,
    NarrativeInsight,
    Source,
    TranscriptClaim,
    TranscriptSegment,
    VideoSource,
)


def _ts(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _source_refs(
    fact: Fact,
    sources_by_id: dict[int, Source],
    claims_by_id: dict[int, TranscriptClaim],
    videos_by_id: dict[int, VideoSource],
    segments_by_id: dict[int, TranscriptSegment],
) -> list[dict]:
    """Complete editorial traceability for one evidence item (Part 34)."""
    refs: list[dict] = []
    for sid in json.loads(fact.source_ids_json or "[]"):
        s = sources_by_id.get(sid)
        if not s:
            continue
        ref: dict = {
            "kind": "source",
            "source_id": s.id,
            "title": s.title,
            "source_type": s.source_type,
            "language": s.language,
            "content_status": s.content_status,
        }
        refs.append(ref)
    for cid in json.loads(fact.transcript_claim_ids_json or "[]"):
        cl = claims_by_id.get(cid)
        if not cl:
            continue
        v = videos_by_id.get(cl.video_source_id)
        seg_ids = json.loads(cl.segment_ids_json or "[]")
        ref = {
            "kind": "transcript_claim",
            "claim_id": cl.id,
            "video_source_id": cl.video_source_id,
            "video_title": v.title if v else None,
            "channel": v.channel_name if v else None,
            "language": cl.original_language,
            "timestamp_start": cl.timestamp_start,
            "timestamp_end": cl.timestamp_end,
            "timestamp_label": (
                f"{_ts(cl.timestamp_start)}–{_ts(cl.timestamp_end)}"
                if cl.timestamp_start is not None else None
            ),
            "segment_ids": seg_ids,
        }
        refs.append(ref)
    return refs


def _fact_entry(
    fact: Fact,
    sources_by_id: dict[int, Source],
    claims_by_id: dict[int, TranscriptClaim],
    videos_by_id: dict[int, VideoSource],
    segments_by_id: dict[int, TranscriptSegment],
) -> dict:
    refs = _fact_entry_refs = _source_refs(
        fact, sources_by_id, claims_by_id, videos_by_id, segments_by_id
    )
    claim_langs = {
        r["language"] for r in refs if r["kind"] == "transcript_claim"
    }
    src_langs = {
        r["language"] for r in refs if r["kind"] == "source"
    }
    return {
        "evidence_id": f"F{fact.id:03d}",
        "canonical_claim_en": fact.claim,
        "original_claim": fact.original_claim,
        "category": fact.category,
        "narrative_value": fact.narrative_value,
        "evidence_strength": fact.evidence_strength,
        "quote_status": fact.quote_status,
        "speaker": fact.speaker,
        "people": json.loads(fact.people_json or "[]"),
        "locations": json.loads(fact.locations_json or "[]"),
        "event_date": fact.event_date,
        "disputed": fact.disputed,
        "confidence": fact.confidence,
        "status": (
            "disputed" if fact.disputed
            else "verified" if (fact.evidence_strength in ("primary", "strong_secondary"))
            else "supported" if refs else "unverified"
        ),
        "source_refs": refs,
        "independent_source_count": len({r.get("source_id") for r in refs if r["kind"] == "source"})
        + len({r.get("video_source_id") for r in refs if r["kind"] == "transcript_claim"}),
        "languages": sorted((claim_langs | src_langs) - {"unknown", None}),
    }


def build_dossier(db: Session, case: Case) -> dict:
    """The CanonicalResearchDossier — one merged evidence view for the case.

    Contains NO raw transcript prose: transcript-derived entries carry only
    the canonical claim text plus provenance refs."""
    facts = db.query(Fact).filter(Fact.case_id == case.id).all()
    contradictions = (
        db.query(Contradiction).filter(Contradiction.case_id == case.id).all()
    )
    sources = db.query(Source).filter(Source.case_id == case.id).all()
    videos = db.query(VideoSource).filter(VideoSource.case_id == case.id).all()
    claims = db.query(TranscriptClaim).filter(TranscriptClaim.case_id == case.id).all()
    clusters = db.query(ClaimCluster).filter(ClaimCluster.case_id == case.id).all()
    insights = db.query(NarrativeInsight).filter(NarrativeInsight.case_id == case.id).all()
    segments = (
        db.query(TranscriptSegment)
        .join(VideoSource, TranscriptSegment.video_source_id == VideoSource.id)
        .filter(VideoSource.case_id == case.id)
        .all()
    )

    sources_by_id = {s.id: s for s in sources}
    claims_by_id = {c.id: c for c in claims}
    videos_by_id = {v.id: v for v in videos}
    segments_by_id = {s.id: s for s in segments}

    entry = lambda f: _fact_entry(  # noqa: E731
        f, sources_by_id, claims_by_id, videos_by_id, segments_by_id
    )
    by_nv = {}
    for f in facts:
        by_nv.setdefault(f.narrative_value or "fact", []).append(entry(f))

    dossier = {
        "case_id": case.id,
        "case_identity": {
            "title": case.canonical_title,
            "summary": case.summary,
            "language": case.language,
        },
        "verified_facts": [entry(f) for f in facts if not f.disputed],
        "timeline": sorted(
            [entry(f) for f in facts if f.event_date],
            key=lambda e: e["event_date"] or "",
        ),
        "people": sorted({p for f in facts for p in json.loads(f.people_json or "[]")}),
        "locations": sorted({l for f in facts for l in json.loads(f.locations_json or "[]")}),
        "human_details": by_nv.get("human_detail", []),
        "scene_details": by_nv.get("scene_detail", []) + by_nv.get("environment", []),
        "investigation_details": by_nv.get("investigation_detail", []),
        "physical_evidence": by_nv.get("physical_evidence", []),
        "legal_details": by_nv.get("legal", []),
        "historical_context": by_nv.get("historical_context", []) + by_nv.get("context", []),
        "quotes": by_nv.get("quote", []),
        "contradictions": [
            {
                "evidence_id": f"C{c.id:03d}",
                "topic": c.topic,
                "description": c.description,
                "severity": c.severity,
                "source_refs": [
                    {
                        "kind": "source",
                        "source_id": sid,
                        "title": sources_by_id[sid].title,
                    }
                    for sid in json.loads(c.source_ids_json or "[]")
                    if sid in sources_by_id
                ],
            }
            for c in contradictions
        ],
        "disputed_claims": [entry(f) for f in facts if f.disputed],
        "unverified_claims": [
            {
                "evidence_id": f"TC{c.id:03d}",
                "canonical_claim_en": c.canonical_claim_en,
                "claim_type": c.claim_type,
                "verification_status": c.verification_status,
                "video_title": (
                    v.title if (v := videos_by_id.get(c.video_source_id)) else None
                ),
                "timestamp_label": _ts(c.timestamp_start),
            }
            for c in claims
            if c.verification_status in ("unverified", "unknown") and not c.promoted_fact_id
        ],
        "theories": [
            {
                "evidence_id": f"TC{c.id:03d}",
                "canonical_claim_en": c.canonical_claim_en,
                "verification_status": c.verification_status,
                "video_title": (
                    v.title if (v := videos_by_id.get(c.video_source_id)) else None
                ),
            }
            for c in claims if c.claim_type == "theory"
        ],
        "narrative_questions": [
            {
                "type": i.insight_type,
                "text": i.canonical_text_en,
                "video_source_id": i.video_source_id,
            }
            for i in insights
        ],
        "research_gaps": [],
        "stats": {
            "evidence_items": len(facts) + len(contradictions),
            "transcript_claims": len(claims),
            "claim_clusters": len(clusters),
            "videos": len(videos),
            "insights": len(insights),
        },
    }
    return dossier
