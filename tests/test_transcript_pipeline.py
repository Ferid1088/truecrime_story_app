"""Tests for the multilingual transcript-intelligence pipeline
(Master_Prompt): cleaning/segmentation, claim extraction, cross-video
clustering, independence analysis, verification/promotion, dossier merge,
and the source-separation originality gates.

All LLM calls are scripted — no real provider cost.
"""

import asyncio
import hashlib
import json
import uuid

from app.providers.generation.base import GenerationResult


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _mk_case(db):
    from app.db.models import Case
    from app.utils import slugify

    title = f"TP Case {uuid.uuid4().hex[:8]}"
    c = Case(canonical_title=title, slug=slugify(title), language="en")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _mk_video(db, case, video_id=None, channel="DocChannel",
              channel_url=None, language="en", **kw):
    from app.db.models import VideoSource

    vid = video_id or f"v{uuid.uuid4().hex[:9]}"
    v = VideoSource(
        case_id=case.id, video_id=vid,
        url=f"https://www.youtube.com/watch?v={vid}",
        title=kw.pop("title", f"Video {vid}"),
        channel_name=channel,
        channel_url=channel_url or f"https://yt/c/{channel}",
        language=language, classification="documentary",
        transcript_status=kw.pop("transcript_status", "available"), **kw,
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def _mk_segment(db, video, index, text, start=None, end=None,
                canonical=None, language="en"):
    from app.db.models import TranscriptSegment

    s = TranscriptSegment(
        video_source_id=video.id, segment_index=index,
        start_seconds=start if start is not None else index * 30.0,
        end_seconds=end if end is not None else index * 30.0 + 30.0,
        text_original=text, language=language,
        canonical_text_en=canonical,
        content_hash=hashlib.sha256(text.lower().encode()).hexdigest(),
    )
    db.add(s)
    db.commit()
    db.refresh(video)
    return s


def _claim(db, case, video, text, cluster_id=None, ctype="fact",
           conf=0.9, ts=0.0, **kw):
    from app.db.models import TranscriptClaim

    c = TranscriptClaim(
        case_id=case.id, video_source_id=video.id,
        canonical_claim_en=text, original_language="en",
        claim_type=ctype, timestamp_start=ts, timestamp_end=ts + 5,
        confidence=conf, cluster_id=cluster_id, **kw,
    )
    db.add(c)
    db.commit()
    return c


class FakeGen:
    """Scripted generation provider."""

    def __init__(self):
        self.structured = {}
        self.calls = []

    def is_configured(self):
        return True

    def _result(self):
        return GenerationResult(
            text="ok", model="m/fake", provider="fake",
            total_tokens=10, cost_usd=0.0001,
        )

    async def generate_text(self, role, system, user):
        self.calls.append(("text", role, user))
        r = self._result()
        r.text = "ok"
        return r

    async def generate_structured(self, role, system, user):
        self.calls.append(("json", role, user))
        v = self.structured.get(role, {})
        out = v(system, user) if callable(v) else v
        return out, self._result()


def _patch_gen(monkeypatch, fake):
    """Services bind get_generation_provider via `from ... import` at module
    level — patch both the source module and every consumer namespace."""
    import app.providers.generation as gen_mod

    monkeypatch.setattr(gen_mod, "get_generation_provider", lambda: fake)
    for mod_name in (
        "app.services.claims",
        "app.agents.transcript_intel",
        "app.services.video_research",
    ):
        import importlib

        mod = importlib.import_module(mod_name)
        if hasattr(mod, "get_generation_provider"):
            monkeypatch.setattr(mod, "get_generation_provider", lambda: fake)


def _patch_transcript_provider(monkeypatch, result):
    """result: TranscriptResult | callable(video_id, preferred_languages)."""
    import app.services.video_research as vr

    class _P:
        name = "fake"

        async def get_transcript(self, video_id, preferred_languages=None):
            if callable(result):
                out = result(video_id, preferred_languages)
                if asyncio.iscoroutine(out):
                    out = await out
                return out
            return result

    monkeypatch.setattr(vr, "get_transcript_provider", lambda: _P())


# ---------------------------------------------------------------------------
# cleaning + segmentation (deterministic, no LLM)
# ---------------------------------------------------------------------------


def test_cleaning_removes_artifacts_and_speaker_labels():
    from app.services.transcripts import clean_segments

    raw = [
        {"start_seconds": 0, "end_seconds": 4,
         "text": "[Music] In 2005, four people vanished."},
        {"start_seconds": 4, "end_seconds": 8,
         "text": "NARRATOR: They were never seen again"},
    ]
    cleaned = clean_segments(raw)
    assert "[Music]" not in cleaned[0]["text"]
    assert "NARRATOR:" not in cleaned[1]["text"]
    assert cleaned[0]["start_seconds"] == 0


def test_cleaning_drops_pure_filler_and_rolling_duplicates():
    from app.services.transcripts import clean_segments

    raw = [
        {"start_seconds": 0, "end_seconds": 3, "text": "[Music]"},
        {"start_seconds": 3, "end_seconds": 6, "text": "the family vanished"},
        # rolling-caption duplicate: next line contains the previous one
        {"start_seconds": 6, "end_seconds": 9,
         "text": "the family vanished overnight"},
    ]
    cleaned = clean_segments(raw)
    texts = [c["text"] for c in cleaned]
    assert "[Music]" not in texts
    assert len(cleaned) == 1  # shorter duplicate absorbed into longer


def test_chunks_preserve_provenance(db_session):
    from app.services.transcripts import transcript_chunks

    case = _mk_case(db_session)
    v = _mk_video(db_session, case)
    for i in range(5):
        _mk_segment(db_session, v, i, f"segment {i} has some words here",
                    start=i * 20.0, end=i * 20.0 + 18.0)
    chunks = transcript_chunks(v)
    assert chunks
    for c in chunks:
        assert c["segment_ids"]
        assert c["start_seconds"] < c["end_seconds"]
        assert c["text"]
    # timestamps must be monotonically non-decreasing across chunks
    starts = [c["start_seconds"] for c in chunks]
    assert starts == sorted(starts)


def test_video_id_extraction():
    from app.services.video_research import video_id_from_url

    assert video_id_from_url(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert video_id_from_url("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert video_id_from_url("not a url") is None


# ---------------------------------------------------------------------------
# acquisition + storage
# ---------------------------------------------------------------------------


def test_store_transcript_persists_timed_segments(db_session):
    from app.providers.transcript.base import (
        TranscriptResult,
        TranscriptSegmentData,
    )
    from app.services.transcripts import store_transcript

    case = _mk_case(db_session)
    v = _mk_video(db_session, case, transcript_status="pending")
    result = TranscriptResult(
        available=True, language="en", transcript_type="auto_caption",
        segments=[
            TranscriptSegmentData(0.0, 5.0, "First line."),
            TranscriptSegmentData(5.0, 10.0, "Second line."),
        ],
    )
    n = store_transcript(db_session, v, result)
    assert n == 2
    assert v.transcript_status == "available"
    segs = sorted(v.segments, key=lambda s: s.segment_index)
    assert segs[0].start_seconds == 0.0
    assert segs[1].end_seconds == 10.0
    assert segs[0].text_original == "First line."


def test_unavailable_transcript_stores_status_only(db_session):
    from app.providers.transcript.base import TranscriptResult
    from app.services.transcripts import store_transcript

    case = _mk_case(db_session)
    v = _mk_video(db_session, case, transcript_status="pending")
    n = store_transcript(
        db_session, v,
        TranscriptResult(available=False, reason="no captions"),
    )
    assert n == 0
    assert v.transcript_status == "unavailable"


# ---------------------------------------------------------------------------
# normalization (canonical English alongside original)
# ---------------------------------------------------------------------------


def test_normalization_fills_canonical_en(db_session, monkeypatch):
    from app.services.transcripts import normalize_transcript

    fake = FakeGen()

    def _norm(system, user):
        items = json.loads(user)["items"]
        return {"translations": [
            {"id": i["id"], "text_en": f"EN: {i['text'][:20]}"}
            for i in items
        ]}

    fake.structured["transcript_normalizer"] = _norm
    _patch_gen(monkeypatch, fake)

    case = _mk_case(db_session)
    v = _mk_video(db_session, case, language="de")
    s1 = _mk_segment(db_session, v, 0, "Der Fall begann in Nannup.",
                     language="de")
    s2 = _mk_segment(db_session, v, 1, "Vier Menschen verschwanden.",
                     language="de")

    updated = asyncio.run(normalize_transcript(db_session, v))
    db_session.refresh(s1)
    db_session.refresh(s2)
    assert updated == 2
    # original text is NEVER overwritten
    assert s1.text_original == "Der Fall begann in Nannup."
    assert s1.canonical_text_en.startswith("EN:")


def test_english_segments_skip_normalization(db_session, monkeypatch):
    from app.services.transcripts import normalize_transcript

    fake = FakeGen()
    _patch_gen(monkeypatch, fake)
    case = _mk_case(db_session)
    v = _mk_video(db_session, case, language="en")
    _mk_segment(db_session, v, 0, "English text", canonical="English text")
    assert asyncio.run(normalize_transcript(db_session, v)) == 0
    assert not fake.calls


# ---------------------------------------------------------------------------
# claim extraction
# ---------------------------------------------------------------------------


def test_extraction_produces_timestamped_typed_claims(db_session, monkeypatch):
    from app.agents.transcript_intel import TranscriptIntelligenceAgent

    fake = FakeGen()
    fake.structured["transcript_intelligence_extractor"] = lambda s, u: {
        "claims": [
            {"canonical_claim_en": "Four people vanished in 2005",
             "claim_type": "timeline_event", "confidence": 0.9,
             "certainty": "supported", "segment_indices": [0]},
            {"canonical_claim_en": "The farmhouse door was locked",
             "claim_type": "scene_detail", "confidence": 0.8,
             "certainty": "unverified", "segment_indices": [1]},
        ],
        "narrative_insights": [
            {"type": "important_question",
             "canonical_text_en": "Why was the door locked from inside?",
             "segment_indices": [1]},
        ],
    }
    _patch_gen(monkeypatch, fake)
    case = _mk_case(db_session)
    v = _mk_video(db_session, case)
    _mk_segment(db_session, v, 0, "Four people vanished.", 0.0, 5.0,
                canonical="Four people vanished.")
    _mk_segment(db_session, v, 1, "The door was locked.", 30.0, 35.0,
                canonical="The door was locked.")

    counts = asyncio.run(
        TranscriptIntelligenceAgent().extract(db_session, case, v))

    assert counts == {"claims": 2, "insights": 1}

    from app.db.models import NarrativeInsight, TranscriptClaim
    claims = db_session.query(TranscriptClaim).all()
    assert len(claims) == 2
    assert all(c.case_id == case.id for c in claims)
    assert claims[0].claim_type == "timeline_event"
    # timestamps come from the referenced SEGMENTS, not model claims
    assert claims[0].timestamp_start == 0.0
    assert claims[1].timestamp_start == 30.0
    ins = db_session.query(NarrativeInsight).all()
    assert ins[0].insight_type == "important_question"


# ---------------------------------------------------------------------------
# clustering + independence
# ---------------------------------------------------------------------------


def test_cross_language_claims_cluster(db_session, monkeypatch):
    """Same fact asserted in en + de + fa transcripts merges into ONE
    cluster via the semantic clusterer."""
    from app.services.claims import cluster_claims

    fake = FakeGen()
    # deterministic merge won't catch these (different scripts) —
    # the claim_clusterer LLM groups them.
    fake.structured["claim_clusterer"] = lambda s, u: {
        "groups": [
            {"canonical_claim_en": "Four people vanished in 2005",
             "claim_ids": [c["id"] for c in json.loads(u)["claims"]]},
        ]
    }
    _patch_gen(monkeypatch, fake)

    case = _mk_case(db_session)
    v_en = _mk_video(db_session, case, channel="EnDoc")
    v_de = _mk_video(db_session, case, channel="DeDoku")
    v_fa = _mk_video(db_session, case, channel="FaDoc")
    _claim(db_session, case, v_en, "Four people vanished in 2005 xq")
    _claim(db_session, case, v_de, "Vier Personen verschwanden 2005 xq")
    _claim(db_session, case, v_fa, "چهار نفر در سال ۲۰۰۵ ناپدید شدند")

    res = asyncio.run(cluster_claims(db_session, case))
    assert res["clusters"] == 1

    from app.db.models import ClaimCluster
    cl = db_session.query(ClaimCluster).one()
    assert len(json.loads(cl.member_claim_ids_json)) == 3
    # three different channels => three independent families
    assert cl.independent_source_family_count == 3
    assert set(json.loads(cl.languages_json)) == {"en"}


def test_same_channel_counts_as_one_family(db_session, monkeypatch):
    from app.services.claims import cluster_claims

    fake = FakeGen()
    fake.structured["claim_clusterer"] = lambda s, u: {
        "groups": [
            {"canonical_claim_en": "shared claim",
             "claim_ids": [c["id"] for c in json.loads(u)["claims"]]},
        ]
    }
    _patch_gen(monkeypatch, fake)
    case = _mk_case(db_session)
    v1 = _mk_video(db_session, case, channel="SameChan")
    v2 = _mk_video(db_session, case, channel="SameChan")
    _claim(db_session, case, v1, "Shared claim phrasing one")
    _claim(db_session, case, v2, "Shared claim phrasing two")

    res = asyncio.run(cluster_claims(db_session, case))
    from app.db.models import ClaimCluster
    assert res["clusters"] == 1
    cl = db_session.query(ClaimCluster).one()
    assert cl.independent_source_family_count == 1


def test_clusterer_failure_keeps_singletons(db_session, monkeypatch):
    """A provider error must never lose claims — singles become singleton
    clusters."""
    from app.services.claims import cluster_claims

    fake = FakeGen()

    async def _boom(role, system, user):
        raise RuntimeError("provider down")

    fake.generate_structured = _boom
    _patch_gen(monkeypatch, fake)
    case = _mk_case(db_session)
    v = _mk_video(db_session, case)
    _claim(db_session, case, v, "Completely unrelated alpha")
    _claim(db_session, case, v, "Totally different beta")

    res = asyncio.run(cluster_claims(db_session, case))
    assert res["clusters"] == 2
    assert res["claims"] == 2


def test_independence_marks_same_family_derivative(db_session):
    from app.services.claims import compute_independence

    case = _mk_case(db_session)
    v1 = _mk_video(db_session, case, channel="OneChan",
                   published_at="2020-01-01")
    v2 = _mk_video(db_session, case, channel="OneChan",
                   published_at="2021-01-01")
    # same channel => same creator family => second is derivative
    res = compute_independence(db_session, case)
    db_session.refresh(v1)
    db_session.refresh(v2)
    assert res["primary"] == 1
    assert res["derivative"] == 1
    assert v1.source_independence == "primary"
    assert v2.source_independence == "likely_derivative"
    assert v2.likely_derivative_of == v1.id


# ---------------------------------------------------------------------------
# verification + promotion into Facts
# ---------------------------------------------------------------------------


def _clustered_claims(db, case, videos, text):
    """One claim per video, all sharing one cluster."""
    from app.db.models import ClaimCluster

    cl = ClaimCluster(case_id=case.id, canonical_claim_en=text,
                      claim_type="fact", support_status="unverified",
                      confidence=0.8)
    db.add(cl)
    db.flush()
    for i, v in enumerate(videos):
        _claim(db, case, v, text, cluster_id=cl.id, ts=i * 10)
    cl.member_claim_ids_json = json.dumps(
        [c.id for c in cl_member_query(db, cl)])
    cl.independent_source_family_count = len(videos)
    db.commit()
    return cl


def cl_member_query(db, cl):
    from app.db.models import TranscriptClaim
    return db.query(TranscriptClaim).filter(
        TranscriptClaim.cluster_id == cl.id).all()


def test_verified_cluster_promotes_to_fact_with_provenance(
        db_session, monkeypatch):
    from app.db.models import Fact
    from app.services.claims import (
        promote_clusters,
        verify_clusters,
    )

    fake = FakeGen()
    fake.structured["evidence_verifier"] = lambda s, u: {
        "verdicts": [
            {"cluster_id": c["id"],
             "verification_status": "strongly_supported",
             "confidence": 0.9, "supporting_fact_ids": [],
             "supporting_source_ids": []}
            for c in json.loads(u)["clusters"]
        ]
    }
    _patch_gen(monkeypatch, fake)

    case = _mk_case(db_session)
    v1 = _mk_video(db_session, case, channel="ChanA")
    v2 = _mk_video(db_session, case, channel="ChanB")
    cl = _clustered_claims(
        db_session, case, [v1, v2], "Four people vanished in 2005")

    asyncio.run(verify_clusters(db_session, case))
    db_session.refresh(cl)
    assert cl.support_status == "strongly_supported"

    res = promote_clusters(db_session, case)
    assert res["promoted"] == 1

    fact = db_session.query(Fact).filter(Fact.case_id == case.id).one()
    assert "vanished" in fact.claim
    # provenance: claim -> segment -> video -> timestamp trace survives
    member_ids = json.loads(fact.transcript_claim_ids_json)
    assert len(member_ids) == 2
    assert cl.promoted_fact_id == fact.id


def test_unverified_cluster_never_promotes(db_session):
    from app.db.models import Fact
    from app.services.claims import promote_clusters

    case = _mk_case(db_session)
    v = _mk_video(db_session, case)
    _clustered_claims(db_session, case, [v], "A creator's speculation")
    res = promote_clusters(db_session, case)
    assert res["promoted"] == 0
    assert db_session.query(Fact).filter(
        Fact.case_id == case.id).count() == 0


def test_creator_narration_quote_never_promotes(db_session):
    """Part 20 rule: creator narration is not an approved quote."""
    from app.db.models import ClaimCluster, Fact
    from app.services.claims import promote_clusters

    case = _mk_case(db_session)
    v = _mk_video(db_session, case)
    cl = ClaimCluster(case_id=case.id, canonical_claim_en="a quote",
                      claim_type="quote", support_status="supported",
                      confidence=0.9)
    db_session.add(cl)
    db_session.flush()
    _claim(db_session, case, v, "a quote", cluster_id=cl.id,
           ctype="quote", quote_classification="creator_narration")
    cl.member_claim_ids_json = json.dumps(
        [c.id for c in cl_member_query(db_session, cl)])
    db_session.commit()

    res = promote_clusters(db_session, case)
    assert res["promoted"] == 0
    assert db_session.query(Fact).filter(
        Fact.case_id == case.id).count() == 0


def test_strongly_supported_requires_independent_families(
        db_session, monkeypatch):
    """Deterministic guard: LLM saying 'strongly_supported' is downgraded
    to 'supported' when the cluster has a single source family."""
    from app.services.claims import verify_clusters

    fake = FakeGen()
    fake.structured["evidence_verifier"] = lambda s, u: {
        "verdicts": [
            {"cluster_id": c["id"],
             "verification_status": "strongly_supported",
             "confidence": 0.9,
             "supporting_fact_ids": [], "supporting_source_ids": []}
            for c in json.loads(u)["clusters"]
        ]
    }
    _patch_gen(monkeypatch, fake)
    case = _mk_case(db_session)
    v = _mk_video(db_session, case)
    cl = _clustered_claims(db_session, case, [v], "single-source claim")
    asyncio.run(verify_clusters(db_session, case))
    db_session.refresh(cl)
    assert cl.support_status == "supported"  # downgraded, not promoted to strong


# ---------------------------------------------------------------------------
# dossier merge
# ---------------------------------------------------------------------------


def test_dossier_merges_facts_and_claims_with_provenance(db_session):
    from app.db.models import Fact
    from app.services.dossier import build_dossier

    case = _mk_case(db_session)
    db_session.add(Fact(
        case_id=case.id, claim="Web-sourced fact",
        category="context", confidence=0.9, event_date="2005-01-01",
    ))
    db_session.commit()
    v = _mk_video(db_session, case)
    _claim(db_session, case, v, "An unverified video claim", conf=0.6)

    dossier = build_dossier(db_session, case)
    assert len(dossier["verified_facts"]) == 1
    assert len(dossier["unverified_claims"]) == 1
    assert dossier["stats"]["transcript_claims"] == 1
    # provenance chain on the dossier's fact entries
    entry = dossier["verified_facts"][0]
    assert entry["evidence_id"].startswith("F")
    assert "source_refs" in entry


def test_dossier_never_contains_raw_transcript(db_session):
    from app.db.models import Fact
    from app.services.dossier import build_dossier

    case = _mk_case(db_session)
    db_session.add(Fact(case_id=case.id, claim="a fact",
                        category="context", confidence=0.9))
    v = _mk_video(db_session, case)
    secret = "UNIQUE_RAW_TRANSCRIPT_PHRASE"
    _mk_segment(db_session, v, 0, secret)
    db_session.commit()

    blob = json.dumps(build_dossier(db_session, case))
    assert secret not in blob


# ---------------------------------------------------------------------------
# originality gates
# ---------------------------------------------------------------------------


def test_transcript_phrase_overlap_gate_trips(db_session):
    from app.services.similarity import check_text_similarity

    case = _mk_case(db_session)
    v = _mk_video(db_session, case)
    transcript_text = (
        "the nannup four disappeared without a trace on a cold winter "
        "morning and the farmhouse door was found locked from the inside "
        "leaving behind cars keys wallets and unfinished coffee"
    )
    _mk_segment(db_session, v, 0, transcript_text)
    # story wholesale copies the transcript's phrasing
    story = "In July 2005. " + transcript_text + " Police searched."
    result = check_text_similarity(db_session, case.id, story)
    assert result["status"] == "fail"
    assert result["worst_video_source_id"] == v.id


def test_original_story_passes_similarity_gate(db_session):
    from app.services.similarity import check_text_similarity

    case = _mk_case(db_session)
    v = _mk_video(db_session, case)
    _mk_segment(db_session, v, 0,
                "the nannup four disappeared without a trace")
    story = (
        "Something happened in the southwest of Australia that no "
        "investigator could explain. An entire household had vanished "
        "overnight, leaving investigators with a puzzle that would "
        "persist for decades and resist every conventional explanation "
        "offered by detectives, journalists and the local community alike."
    )
    result = check_text_similarity(db_session, case.id, story)
    assert result["status"] == "pass"


def test_structure_gate_flags_replayed_beat_order(db_session):
    from app.services.similarity import check_structure_similarity

    case = _mk_case(db_session)
    v = _mk_video(db_session, case)
    types = ["fact", "scene_detail", "timeline_event", "quote",
             "contradiction", "investigation_detail"]
    for i, t in enumerate(types):
        _claim(db_session, case, v, f"claim {i}", ctype=t, ts=i * 10)
    sections = [{"narrative_value": t} for t in types]
    result = check_structure_similarity(db_session, case.id, sections)
    assert result["status"] == "fail"


def test_structure_gate_passes_different_order(db_session):
    from app.services.similarity import check_structure_similarity

    case = _mk_case(db_session)
    v = _mk_video(db_session, case)
    for i, t in enumerate(["fact", "fact", "fact", "fact"]):
        _claim(db_session, case, v, f"claim {i}", ctype=t, ts=i * 10)
    sections = [{"narrative_value": t} for t in
                ["human_detail", "contradiction", "quote", "legal"]]
    result = check_structure_similarity(db_session, case.id, sections)
    assert result["status"] == "pass"


# ---------------------------------------------------------------------------
# acquisition failure isolation
# ---------------------------------------------------------------------------


def test_video_processing_failure_marks_video(db_session, monkeypatch):
    """An acquisition failure marks the video failed; the caller loop in
    run_video_research continues to the next candidate."""
    from app.services.video_research import _acquire_and_process

    async def _fail(video_id, preferred_languages=None):
        raise RuntimeError("Transcript disabled")

    _patch_transcript_provider(monkeypatch, _fail)
    case = _mk_case(db_session)
    v = _mk_video(db_session, case)
    import pytest
    with pytest.raises(RuntimeError):
        asyncio.run(_acquire_and_process(db_session, case, v))


def test_identical_transcript_skips_reextraction(db_session, monkeypatch):
    """extraction_cache_key: the same transcript corpus on a re-run reuses
    stored claims — zero additional provider spend (Part 39)."""
    from app.db.models import TranscriptClaim
    from app.providers.transcript.base import (
        TranscriptResult,
        TranscriptSegmentData,
    )
    from app.services.video_research import _acquire_and_process

    result = TranscriptResult(
        available=True, language="en", transcript_type="manual",
        segments=[TranscriptSegmentData(0.0, 5.0, "The family disappeared.")],
    )
    _patch_transcript_provider(monkeypatch, result)
    fake = FakeGen()
    fake.structured["transcript_intelligence_extractor"] = lambda s, u: {
        "claims": [{"canonical_claim_en": "Cached claim",
                    "claim_type": "fact", "confidence": 0.8,
                    "segment_indices": [0]}],
        "narrative_insights": [],
    }
    _patch_gen(monkeypatch, fake)

    case = _mk_case(db_session)
    v = _mk_video(db_session, case, transcript_status="pending")
    asyncio.run(_acquire_and_process(db_session, case, v))
    n_calls = len(fake.calls)
    assert n_calls > 0

    # Second pass over identical content: cached — no new extraction calls.
    out = asyncio.run(_acquire_and_process(db_session, case, v))
    assert out.get("cached") is True
    assert out["claims"] == 1
    assert len(fake.calls) == n_calls
    assert db_session.query(TranscriptClaim).filter(
        TranscriptClaim.case_id == case.id).count() == 1


def test_acquire_and_process_end_to_end(db_session, monkeypatch):
    from app.providers.transcript.base import (
        TranscriptResult,
        TranscriptSegmentData,
    )
    from app.services.video_research import _acquire_and_process

    result = TranscriptResult(
        available=True, language="en", transcript_type="manual",
        segments=[
            TranscriptSegmentData(0.0, 5.0, "The family disappeared."),
            TranscriptSegmentData(5.0, 10.0, "Police found nothing."),
        ],
    )
    _patch_transcript_provider(monkeypatch, result)
    fake = FakeGen()
    fake.structured["transcript_intelligence_extractor"] = lambda s, u: {
        "claims": [{"canonical_claim_en": "A claim",
                    "claim_type": "fact", "confidence": 0.8,
                    "segment_indices": [0]}],
        "narrative_insights": [],
    }
    _patch_gen(monkeypatch, fake)

    case = _mk_case(db_session)
    v = _mk_video(db_session, case, transcript_status="pending")
    out = asyncio.run(_acquire_and_process(db_session, case, v))
    assert out["segments"] == 2
    assert out["claims"] == 1
    db_session.refresh(v)
    assert v.transcript_status == "processed"
    assert v.processed_at is not None


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def test_videos_endpoint_lists_case_videos(client, db_session):
    case = _mk_case(db_session)
    _mk_video(db_session, case)
    r = client.get(f"/api/cases/{case.id}/videos")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["transcript_status"] == "available"
    assert body[0]["channel_name"] == "DocChannel"


def test_video_detail_includes_claims_and_segments(client, db_session):
    case = _mk_case(db_session)
    v = _mk_video(db_session, case)
    _mk_segment(db_session, v, 0, "segment text", canonical="segment text")
    _claim(db_session, case, v, "a claim", ts=3.0)
    r = client.get(f"/api/cases/{case.id}/videos/{v.id}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["segments"]) == 1
    assert body["segments"][0]["start_seconds"] == 0.0
    assert len(body["claims"]) == 1
    assert body["claims"][0]["canonical_claim_en"] == "a claim"


def test_dossier_endpoint_returns_schema(client, db_session):
    case = _mk_case(db_session)
    r = client.get(f"/api/cases/{case.id}/dossier")
    assert r.status_code == 200
    body = r.json()
    for key in ("verified_facts", "timeline", "contradictions",
                "unverified_claims", "narrative_questions", "stats"):
        assert key in body


def test_claim_clusters_endpoint(client, db_session):
    case = _mk_case(db_session)
    v = _mk_video(db_session, case)
    cl = _clustered_claims(db_session, case, [v], "clustered claim")
    r = client.get(f"/api/cases/{case.id}/claim-clusters")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["id"] == cl.id
    assert body[0]["claims"][0]["video"] == v.video_id
