"""Claim clustering, source-independence analysis, verification and
promotion into the canonical evidence layer (Parts 9/10/18).

Two-stage dedup: deterministic near-duplicate merge first (rapidfuzz on
normalized canonical text) bounds LLM input; the claim_clusterer role then
groups semantically-equal claims across languages.

Independence: videos from the same channel or whose claims mostly fall in
the same clusters are likely derivative — they must not inflate an
evidence item's independent-source-family count.
"""

import json

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    Case,
    ClaimCluster,
    Contradiction,
    Fact,
    Source,
    TranscriptClaim,
    VideoSource,
)
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.utils import utc_now

_CLAIM_TO_NV = {
    "human_detail": "human_detail",
    "scene_detail": "scene_detail",
    "investigation_detail": "investigation_detail",
    "physical_evidence": "physical_evidence",
    "legal": "legal",
    "historical_context": "historical_context",
    "quote": "quote",
    "reported_statement": "quote",
    "timeline_event": "timeline",
    "fact": "fact",
    "location": "fact",
    "person": "human_detail",
    "relationship": "human_detail",
}

# Claim types that may be promoted into canonical Facts.
_PROMOTABLE = set(_CLAIM_TO_NV) - {"quote"}
# Quotes promote only when the underlying quotation is a real documented
# statement — creator narration never becomes an approved quote.
_PROMOTABLE_QUOTE_CLASSES = {
    "source_document_quote",
    "interview_quote",
    "reported_quote",
}


def _norm(t: str) -> str:
    return " ".join((t or "").lower().split())


def _claim_family(claim: TranscriptClaim, video: VideoSource | None) -> str:
    if video and video.creator_source_family:
        return f"video:{video.creator_source_family}"
    if video:
        return f"video:{video.channel_name or video.video_id}"
    return f"claim:{claim.id}"


async def cluster_claims(db: Session, case: Case) -> dict:
    """Assign every unclustered claim for the case to a ClaimCluster.

    Pass 1 (deterministic): canonical texts with token-set similarity above
    claim_clustering.near_duplicate_threshold merge into an existing cluster
    or form a new one — no LLM cost for verbatim/near-verbatim repeats.
    Pass 2 (LLM): remaining singletons are grouped by the claim_clusterer
    role, which can merge paraphrases across languages.
    """
    cfg = ai_config.claim_clustering
    claims = (
        db.query(TranscriptClaim)
        .filter(TranscriptClaim.case_id == case.id)
        .order_by(TranscriptClaim.id)
        .all()
    )
    if not claims:
        return {"clusters": 0, "claims": 0}

    clusters = (
        db.query(ClaimCluster).filter(ClaimCluster.case_id == case.id).all()
    )

    def members(c: ClaimCluster) -> list[int]:
        return json.loads(c.member_claim_ids_json or "[]")

    def add_to(c: ClaimCluster, claim: TranscriptClaim):
        ids = members(c)
        if claim.id not in ids:
            ids.append(claim.id)
            c.member_claim_ids_json = json.dumps(ids)
        claim.cluster_id = c.id
        langs = set(json.loads(c.languages_json or "[]"))
        langs.add(claim.original_language)
        c.languages_json = json.dumps(sorted(langs))

    def new_cluster(claim: TranscriptClaim) -> ClaimCluster:
        c = ClaimCluster(
            case_id=case.id,
            canonical_claim_en=claim.canonical_claim_en,
            claim_type=claim.claim_type,
            narrative_value=_CLAIM_TO_NV.get(claim.claim_type),
            confidence=claim.confidence,
        )
        db.add(c)
        db.flush()
        add_to(c, claim)
        clusters.append(c)
        return c

    clustered_norms: list[tuple[ClaimCluster, str]] = [
        (c, _norm(c.canonical_claim_en)) for c in clusters
    ]
    singles: list[TranscriptClaim] = []
    for claim in claims:
        if claim.cluster_id:
            continue
        n = _norm(claim.canonical_claim_en)
        hit = next(
            (
                (c, cn) for c, cn in clustered_norms
                if fuzz.token_set_ratio(n, cn) >= cfg.near_duplicate_threshold
            ),
            None,
        )
        if hit:
            add_to(hit[0], claim)
        else:
            singles.append(claim)

    # LLM semantic merge for what deterministic matching missed. A provider
    # failure degrades to deterministic-only clustering — claims are kept
    # as singletons, never dropped.
    if len(singles) > 1:
        try:
            singles = await _semantic_merge(db, case, singles, clusters, add_to)
        except Exception:
            pass
    for claim in singles:
        new_cluster(claim)

    # Refresh family counts: distinct creator families + non-video sources.
    by_id = {c.id: c for c in claims}
    videos = {
        v.id: v for v in db.query(VideoSource)
        .filter(VideoSource.case_id == case.id).all()
    }
    for c in clusters:
        fams = set()
        sup_sources = set()
        for cid in members(c):
            cl = by_id.get(cid)
            if not cl:
                continue
            fams.add(_claim_family(cl, videos.get(cl.video_source_id)))
            sup_sources.update(
                json.loads(cl.supporting_source_ids_json or "[]")
            )
        c.independent_source_family_count = len(fams) + len(sup_sources)
    db.flush()
    return {"clusters": len(clusters), "claims": len(claims)}


async def _semantic_merge(db, case, singles, clusters, add_to) -> list[TranscriptClaim]:
    """Ask claim_clusterer to group semantically-equal singletons. Any claim
    the model does not group remains a singleton cluster."""
    gen = get_generation_provider()
    system = (
        "You deduplicate factual claims extracted from multiple videos "
        "(possibly different languages) about the same case. Group claims "
        "that assert the SAME underlying fact — paraphrases and "
        "translations count. Do NOT group merely related or complementary "
        "claims. Return JSON only."
    )
    user = json.dumps(
        {
            "claims": [
                {"id": c.id, "text": c.canonical_claim_en, "type": c.claim_type}
                for c in singles
            ][: ai_config.claim_clustering.batch_size],
            "output_schema": {
                "groups": [{"canonical_claim_en": "...", "claim_ids": [1, 2]}]
            },
        },
        ensure_ascii=False,
    )
    with track_run(
        db, case.id, "Claim Clusterer",
        input_summary=f"claims={len(singles)}",
    ) as run:
        data, res = await gen.generate_structured("claim_clusterer", system, user)
        stamp_run(run, res, "claim_clusterer")
    grouped: set[int] = set()
    for g in (data or {}).get("groups") or []:
        ids = [i for i in (g.get("claim_ids") or []) if isinstance(i, int)]
        ids = [i for i in ids if any(c.id == i for c in singles)]
        if not ids:
            continue
        c = ClaimCluster(
            case_id=case.id,
            canonical_claim_en=(g.get("canonical_claim_en") or "") or next(
                c.canonical_claim_en for c in singles if c.id == ids[0]
            ),
            claim_type=next(
                (c.claim_type for c in singles if c.id == ids[0]), "fact"
            ),
        )
        db.add(c)
        db.flush()
        for cid in ids:
            claim = next(c2 for c2 in singles if c2.id == cid)
            add_to(c, claim)
            grouped.add(cid)
        clusters.append(c)
    run.output_summary = f"groups={len((data or {}).get('groups') or [])}"
    return [c for c in singles if c.id not in grouped]


def compute_independence(db: Session, case: Case) -> dict:
    """Classify each video's source independence (Part 10).

    Deterministic signals — same creator channel => same family; two videos
    sharing >= shared_unusual_claim_ratio of one's claims are likely
    derivative (later-published one derives from the earlier)."""
    cfg = ai_config.source_independence
    videos = (
        db.query(VideoSource).filter(VideoSource.case_id == case.id).all()
    )
    claims_by_video: dict[int, set[int]] = {}
    for c in (
        db.query(TranscriptClaim)
        .filter(TranscriptClaim.case_id == case.id)
        .all()
    ):
        claims_by_video.setdefault(c.video_source_id, set()).add(c.cluster_id or c.id)

    for v in videos:
        if not v.creator_source_family:
            v.creator_source_family = (v.channel_name or v.channel_url or v.video_id)[:300]
        v.source_independence = "independent_secondary"

    # Same channel => same family, but still "primary" for the first one.
    by_family: dict[str, list[VideoSource]] = {}
    for v in videos:
        by_family.setdefault(v.creator_source_family or "", []).append(v)
    for fam, vs in by_family.items():
        if not fam:
            continue
        vs_sorted = sorted(vs, key=lambda v: v.published_at or "")
        for i, v in enumerate(vs_sorted):
            if i == 0:
                v.source_independence = "primary"
            else:
                v.source_independence = "likely_derivative"
                v.likely_derivative_of = vs_sorted[0].id

    # Cross-channel dependence: shared cluster membership.
    for i, a in enumerate(videos):
        ca = claims_by_video.get(a.id) or set()
        if not ca:
            continue
        for b in videos[i + 1 :]:
            cb = claims_by_video.get(b.id) or set()
            if not cb:
                continue
            shared = len(ca & cb)
            if shared < cfg.min_shared_unusual_claims:
                continue
            ratio = shared / min(len(ca), len(cb))
            if ratio >= cfg.shared_unusual_claim_ratio:
                # Earlier-published video is the likely source.
                first, second = (
                    (a, b)
                    if (a.published_at or "9") <= (b.published_at or "9")
                    else (b, a)
                )
                if second.source_independence != "primary":
                    second.source_independence = "likely_derivative"
                    second.likely_derivative_of = first.id
    db.flush()
    return {
        "primary": sum(1 for v in videos if v.source_independence == "primary"),
        "independent": sum(
            1 for v in videos if v.source_independence == "independent_secondary"
        ),
        "derivative": sum(
            1 for v in videos if v.source_independence == "likely_derivative"
        ),
    }


async def verify_clusters(db: Session, case: Case) -> dict:
    """evidence_verifier scores each cluster against the case's canonical
    Facts + Sources. Creator repetition alone can never reach
    'strongly_supported' — independent source families decide."""
    clusters = (
        db.query(ClaimCluster).filter(ClaimCluster.case_id == case.id).all()
    )
    if not clusters:
        return {"verified": 0}
    facts = db.query(Fact).filter(Fact.case_id == case.id).all()
    sources = db.query(Source).filter(Source.case_id == case.id).all()
    gen = get_generation_provider()

    system = (
        "You verify transcript-derived claims against the case's established "
        "evidence. For each cluster, return verification_status: "
        "supported | strongly_supported | contradicted | disputed | "
        "unverified. 'strongly_supported' requires corroboration by "
        "independent established evidence, not repetition across videos. "
        "Optionally set supporting_fact_ids (ids of supplied facts that "
        "corroborate) and supporting_source_ids. Return JSON only."
    )
    user = json.dumps(
        {
            "clusters": [
                {
                    "id": c.id,
                    "claim": c.canonical_claim_en,
                    "type": c.claim_type,
                    "members": len(json.loads(c.member_claim_ids_json or "[]")),
                    "independent_families": c.independent_source_family_count,
                    "languages": json.loads(c.languages_json or "[]"),
                }
                for c in clusters
            ],
            "facts": [{"id": f.id, "claim": f.claim} for f in facts[:400]],
            "sources": [
                {"id": s.id, "title": s.title, "type": s.source_type}
                for s in sources[:200]
            ],
            "output_schema": {
                "verdicts": [
                    {
                        "cluster_id": 1,
                        "verification_status": "...",
                        "confidence": 0.0,
                        "supporting_fact_ids": [1],
                        "supporting_source_ids": [1],
                    }
                ]
            },
        },
        ensure_ascii=False,
    )
    with track_run(
        db, case.id, "Evidence Verifier",
        input_summary=f"clusters={len(clusters)}",
    ) as run:
        data, res = await gen.generate_structured("evidence_verifier", system, user)
        stamp_run(run, res, "evidence_verifier")
        by_id = {c.id: c for c in clusters}
        min_fams = ai_config.claim_clustering.min_independent_families_for_verified
        n = 0
        for v in data.get("verdicts") or []:
            c = by_id.get(v.get("cluster_id"))
            if not c:
                continue
            status = v.get("verification_status") or "unverified"
            # Deterministic guard: a cluster echoed only by derivative
            # retellings of ONE family can never be strongly_supported.
            if (
                status == "strongly_supported"
                and c.independent_source_family_count < min_fams
                and not (v.get("supporting_fact_ids") or v.get("supporting_source_ids"))
            ):
                status = "supported"
            c.support_status = status
            if v.get("confidence") is not None:
                c.confidence = min(max(float(v["confidence"]), 0.0), 1.0)
            sup = list(v.get("supporting_source_ids") or [])
            if sup:
                for cid in json.loads(c.member_claim_ids_json or "[]"):
                    cl = db.get(TranscriptClaim, cid)
                    if cl:
                        existing = set(
                            json.loads(cl.supporting_source_ids_json or "[]")
                        )
                        cl.supporting_source_ids_json = json.dumps(
                            sorted(existing | set(sup))
                        )
            for cid in json.loads(c.member_claim_ids_json or "[]"):
                cl = db.get(TranscriptClaim, cid)
                if cl:
                    cl.verification_status = status
            n += 1
        run.output_summary = f"verified={n}"
    db.flush()
    return {"verified": n}


def promote_clusters(db: Session, case: Case) -> dict:
    """Promote verified clusters into canonical Facts / Contradictions —
    THE merge point (Part 12). Facts carry transcript_claim_ids so every
    canonical item traces back claim -> segment -> video -> timestamp."""
    clusters = (
        db.query(ClaimCluster)
        .filter(
            ClaimCluster.case_id == case.id,
            ClaimCluster.promoted_fact_id.is_(None),
            ClaimCluster.support_status.in_(
                ["supported", "strongly_supported"]
            ),
        )
        .all()
    )
    existing_facts = db.query(Fact).filter(Fact.case_id == case.id).all()
    existing_norms = {_norm(f.claim) for f in existing_facts}
    videos = {
        v.id: v
        for v in db.query(VideoSource).filter(VideoSource.case_id == case.id)
    }
    claims_by_id = {
        c.id: c
        for c in db.query(TranscriptClaim)
        .filter(TranscriptClaim.case_id == case.id).all()
    }
    promoted = contradictions_added = 0
    for c in clusters:
        if _norm(c.canonical_claim_en) in existing_norms:
            continue  # already canonical — don't duplicate evidence
        member_ids = json.loads(c.member_claim_ids_json or "[]")
        members = [claims_by_id[i] for i in member_ids if i in claims_by_id]
        if c.claim_type in ("contradiction", "disputed"):
            db.add(
                Contradiction(
                    case_id=case.id,
                    topic=c.canonical_claim_en[:500],
                    description=c.canonical_claim_en,
                    source_ids_json=json.dumps(
                        sorted(
                            {
                                v.source_id
                                for m in members
                                if (v := videos.get(m.video_source_id))
                                and v.source_id
                            }
                        )
                    ),
                    severity="medium",
                )
            )
            contradictions_added += 1
            continue
        if c.claim_type == "quote":
            ok_classes = {
                m.quote_classification for m in members
            } & _PROMOTABLE_QUOTE_CLASSES
            if not ok_classes:
                continue  # creator narration never becomes an approved quote
        if c.claim_type not in _PROMOTABLE | {"quote"}:
            continue  # theory/question/unverified stay out of the Fact layer

        source_ids = sorted(
            {
                v.source_id
                for m in members
                if (v := videos.get(m.video_source_id)) and v.source_id
            }
            | {
                sid
                for m in members
                for sid in json.loads(m.supporting_source_ids_json or "[]")
            }
        )
        quote = next(
            (
                m for m in members
                if m.quote_classification in _PROMOTABLE_QUOTE_CLASSES
            ),
            None,
        )
        fact = Fact(
            case_id=case.id,
            claim=c.canonical_claim_en,
            category={
                "timeline_event": "timeline",
                "legal": "legal",
                "investigation_detail": "investigation",
                "physical_evidence": "evidence",
            }.get(c.claim_type, "general"),
            confidence=c.confidence,
            source_ids_json=json.dumps(source_ids),
            disputed=c.support_status == "disputed",
            narrative_value=c.narrative_value
            or _CLAIM_TO_NV.get(c.claim_type),
            evidence_strength=(
                "strong_secondary" if c.support_status == "strongly_supported"
                else "secondary"
            ),
            transcript_claim_ids_json=json.dumps(member_ids),
            quote_status=(
                {
                    "source_document_quote": "verified_direct",
                    "interview_quote": "verified_direct",
                    "reported_quote": "reported_quote",
                }.get(quote.quote_classification) if quote else None
            ),
            speaker=quote.speaker if quote else None,
        )
        db.add(fact)
        db.flush()
        c.promoted_fact_id = fact.id
        for m in members:
            m.promoted_fact_id = fact.id
        existing_norms.add(_norm(c.canonical_claim_en))
        promoted += 1
    db.flush()
    return {"promoted": promoted, "contradictions": contradictions_added}


def score_videos(db: Session, case: Case) -> dict:
    """novel_information_ratio + value_score per video (Parts 20/21).

    unique = sole member of its cluster from this video (or unclustered).
    Value weights come from config — views never count."""
    cfg = ai_config.transcript_value
    videos = (
        db.query(VideoSource).filter(VideoSource.case_id == case.id).all()
    )
    claims = (
        db.query(TranscriptClaim).filter(TranscriptClaim.case_id == case.id).all()
    )
    cluster_sizes: dict[int, set[int]] = {}
    for c in claims:
        key = c.cluster_id or -c.id
        cluster_sizes.setdefault(key, set()).add(c.video_source_id)

    out = {}
    for v in videos:
        vclaims = [c for c in claims if c.video_source_id == v.id]
        if not vclaims:
            v.novel_information_ratio = 0.0
            v.value_score = 0.0
            out[v.id] = {"novel": 0.0, "value": 0.0}
            continue
        unique = [
            c for c in vclaims
            if len(cluster_sizes.get(c.cluster_id or -c.id) or {0}) == 1
        ]
        v.novel_information_ratio = round(len(unique) / len(vclaims), 3)
        types = {c.claim_type for c in vclaims}
        w = cfg.weights
        raw = (
            w.get("unique_claims", 0) * min(1.0, len(unique) / 10)
            + w.get("credible_details", 0)
            * min(1.0, sum(1 for c in vclaims if c.confidence >= 0.7) / 10)
            + w.get("human_details", 0)
            * min(1.0, sum(1 for c in vclaims if c.claim_type == "human_detail") / 5)
            + w.get("scene_details", 0)
            * min(1.0, sum(1 for c in vclaims if c.claim_type == "scene_detail") / 5)
            + w.get("quotes", 0)
            * min(
                1.0,
                sum(
                    1 for c in vclaims
                    if c.quote_classification in _PROMOTABLE_QUOTE_CLASSES
                ) / 3,
            )
            + w.get("contradictions", 0)
            * min(1.0, sum(1 for c in vclaims if c.claim_type == "contradiction") / 3)
            + w.get("independence", 0)
            * (1.0 if v.source_independence == "primary" else 0.5 if v.source_independence == "independent_secondary" else 0.0)
        )
        v.value_score = round(raw, 3)
        v.processed_at = utc_now()
        out[v.id] = {"novel": v.novel_information_ratio, "value": v.value_score}
    db.flush()
    return out
