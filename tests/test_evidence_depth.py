"""Deep-source ingestion, evidence enrichment and capacity v2 tests.

Covers the Part-27 checklist: content states, chunking, provenance,
dedupe, typed evidence, coverage matrix, conservative capacity, gap
plans, act-scoped packs/grounding and provider authorization gating.
"""
import asyncio
import json
import uuid

import pytest

from tests.factories import DistinctEmbedder
from fastapi import HTTPException

from app.agents.story import (
    StoryPipeline,
    build_act_pack,
    build_evidence_pack,
    estimate_narrative_capacity,
    evidence_coverage_matrix,
    research_gap_plan,
)
from app.services.chunking import chunk_source, split_chunks
from app.services.research_jobs import _ingest_research, _source_content


def _mk_case(db):
    from app.db.models import Case
    from app.utils import slugify

    title = f"ED Case {uuid.uuid4().hex[:8]}"
    c = Case(canonical_title=title, slug=slugify(title), language="en")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _mk_source(db, case, **kw):
    from app.db.models import Source

    n = uuid.uuid4().hex[:6]
    s = Source(
        case_id=case.id,
        title=kw.pop("title", f"Src {n}"),
        url=kw.pop("url", f"https://e/{n}"),
        source_type=kw.pop("source_type", "article"),
        **kw,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _mk_fact(db, case, claim="claim", **kw):
    from app.db.models import Fact

    f = Fact(case_id=case.id, claim=claim, confidence=0.9, **kw)
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


# ---------------------------------------------------------------------------
# 1-4. Source content states (resolved from research output, never faked)
# ---------------------------------------------------------------------------


def test_content_status_metadata_only():
    text, status = _source_content({"title": "t"})
    assert text is None and status == "metadata_only"


def test_content_status_summary_only():
    text, status = _source_content({"summary": "a short summary"})
    assert text is None and status == "summary_only"


def test_content_status_partial_text():
    text, status = _source_content(
        {"excerpts": [{"text": "verbatim one"}, {"text": "verbatim two"}]}
    )
    assert status == "partial_text"
    assert "verbatim one" in text and "verbatim two" in text


def test_content_status_full_text_and_unavailable():
    text, status = _source_content(
        {"full_text": "the whole document", "full_text_available": True}
    )
    assert status == "full_text" and "whole document" in text

    text, status = _source_content({"content_status": "unavailable"})
    assert text is None and status == "unavailable"


def test_ingest_stores_retrieval_fields(db_session):
    case = _mk_case(db_session)
    job = type(
        "J", (),
        {"case_id": case.id, "provider": "truecrime",
         "external_job_id": "x", "result_summary": ""},
    )()
    result = {
        "sources": [{
            "title": "Official Report", "url": "https://gov/report",
            "language": "en", "source_type": "police",
            "summary": "s",
            "full_text": "full official text " * 50,
            "full_text_available": True,
            "retrieval_notes": "public record",
            "source_family": "gov-inquiry",
        }]
    }
    _ingest_research(db_session, job, result)
    from app.db.models import Source

    s = db_session.query(Source).filter(Source.case_id == case.id).one()
    assert s.content_status == "full_text"
    assert s.retrieval_method == "http_fetch"
    assert s.retrieval_notes == "public record"
    assert s.source_family == "gov-inquiry"
    assert s.is_authorized_text and s.raw_text


# ---------------------------------------------------------------------------
# 5-6. Chunk creation + dedupe
# ---------------------------------------------------------------------------


def test_chunk_creation_bounds(db_session):
    case = _mk_case(db_session)
    src = _mk_source(
        db_session, case, content_status="full_text",
        raw_text=" ".join(f"Sentence number {i} ends." for i in range(400)),
    )
    n = chunk_source(db_session, src)
    assert n > 0
    for c in src.chunks:
        assert c.source_id == src.id and c.token_count > 0 and c.content_hash


def test_chunk_dedupe_by_hash(db_session):
    case = _mk_case(db_session)
    text = " ".join(f"Sentence number {i} ends." for i in range(400))
    s1 = _mk_source(
        db_session, case, title="A", url="https://e/a",
        content_status="full_text", raw_text=text,
    )
    s2 = _mk_source(
        db_session, case, title="B", url="https://e/b",
        content_status="full_text", raw_text=text,  # identical body
    )
    n1 = chunk_source(db_session, s1)
    n2 = chunk_source(db_session, s2)
    assert n1 > 0 and n2 == 0  # same content never chunked twice in a case


def test_metadata_source_never_chunked(db_session):
    case = _mk_case(db_session)
    src = _mk_source(
        db_session, case, content_status="metadata_only",
        raw_text="some text",
    )
    assert chunk_source(db_session, src) == 0


def test_split_chunks_respects_bounds():
    text = " ".join(f"S{i}." for i in range(2000))
    for c in split_chunks(text):
        # nothing pathological: every chunk is a real slice of the input
        assert len(c) > 0 and len(c.split()) < 2500


# ---------------------------------------------------------------------------
# 7-8. Evidence provenance + family dedupe
# ---------------------------------------------------------------------------


def test_evidence_pack_keeps_provenance(db_session):
    case = _mk_case(db_session)
    src = _mk_source(db_session, case)
    f = _mk_fact(
        db_session, case, "documented claim",
        source_ids_json=json.dumps([src.id]),
        chunk_ids_json=json.dumps([7, 8]),
        evidence_strength="primary",
        supporting_text="verbatim passage",
    )
    pack = build_evidence_pack([f], [], [src])
    item = pack["facts"][0]
    assert item["source_ids"] == [src.id]
    assert item["chunk_ids"] == [7, 8]
    assert item["evidence_strength"] == "primary"
    assert item["supporting_text"] == "verbatim passage"


def test_multilingual_family_dedupe(db_session):
    case = _mk_case(db_session)
    job = type(
        "J", (),
        {"case_id": case.id, "provider": "truecrime",
         "external_job_id": "x", "result_summary": ""},
    )()
    result = {
        "sources": [
            {"title": "Report EN", "url": "https://a/1", "language": "en",
             "source_family": "fam-x", "summary": "s"},
            {"title": "Report DE", "url": "https://b/2", "language": "de",
             "source_family": "fam-x", "summary": "s"},  # same family
            {"title": "Different", "url": "https://c/3", "language": "de",
             "source_family": "fam-y", "summary": "s"},
        ]
    }
    _ingest_research(db_session, job, result)
    from app.db.models import Source

    sources = db_session.query(Source).filter(Source.case_id == case.id).all()
    assert len(sources) == 2  # translated duplicate rejected
    assert {s.source_family for s in sources} == {"fam-x", "fam-y"}


# ---------------------------------------------------------------------------
# 9-12. Typed evidence: human/scene detail, quotes, strength
# ---------------------------------------------------------------------------


def _run_extract(db_session, case, fake, embedder=None):
    import app.agents.research as research_mod

    original = research_mod.get_generation_provider, research_mod.default_embedder
    research_mod.get_generation_provider = lambda: fake
    research_mod.default_embedder = lambda: embedder or DistinctEmbedder()
    try:
        return asyncio.run(research_mod.ResearchAgent().run(db_session, case))
    finally:
        research_mod.get_generation_provider, research_mod.default_embedder = original


class _ExtractFake:
    def __init__(self, facts):
        self._facts = facts

    async def generate_structured(self, role, system, user):
        from app.providers.generation.base import GenerationResult

        res = GenerationResult(text="{}", model="m", provider="fake")
        if role == "fact_extractor":
            return {"facts": self._facts}, res
        if role == "timeline_builder":
            return {"timeline": []}, res
        return {"contradictions": []}, res


def test_human_scene_quote_evidence_stored(db_session):
    case = _mk_case(db_session)
    src = _mk_source(db_session, case, content_status="full_text", is_authorized_text=True,
                     raw_text="Keeper James Ducat was married with children. "
                              "The kitchen clock had stopped. \"The damage is done,\" Moore reportedly said.")
    fake = _ExtractFake([
        {
            "claim": "Keeper James Ducat was married with children",
            "category": "person", "confidence": 0.9, "source_ids": [src.id],
            "narrative_value": "human_detail", "evidence_strength": "primary",
            "people": ["James Ducat"], "supporting_text": "passage",
        },
        {
            "claim": "The kitchen clock had stopped",
            "category": "evidence", "confidence": 0.9, "source_ids": [src.id],
            "narrative_value": "scene_detail",
            "evidence_strength": "strong_secondary",
            "locations": ["Flannan Isles"],
        },
        {
            "claim": '"The damage is done," Moore reportedly said',
            "category": "evidence", "confidence": 0.8, "source_ids": [src.id],
            "narrative_value": "quote", "quote_status": "reported_quote",
            "speaker": "Joseph Moore",
        },
    ])
    _run_extract(db_session, case, fake)
    from app.db.models import Fact

    facts = db_session.query(Fact).filter(Fact.case_id == case.id).all()
    by_nv = {f.narrative_value: f for f in facts}
    assert by_nv["human_detail"].evidence_strength == "primary"
    assert json.loads(by_nv["human_detail"].people_json) == ["James Ducat"]
    assert by_nv["scene_detail"].evidence_strength == "strong_secondary"
    assert by_nv["quote"].quote_status == "reported_quote"
    assert by_nv["quote"].speaker == "Joseph Moore"

    pack = build_evidence_pack(facts, [], [src])
    assert len(pack["human_details"]) == 1
    assert len(pack["scene_details"]) == 1
    assert len(pack["approved_quotes"]) == 1


def test_quote_verified_is_deterministic_not_a_model_call(db_session):
    """Part 2(e): a fact's supporting_text is checked against the real
    source text in code, not trusted as-is from the model."""
    case = _mk_case(db_session)
    src = _mk_source(
        db_session, case, content_status="full_text", is_authorized_text=True,
        raw_text="Keeper James Ducat was married with three children in Edinburgh.",
    )
    fake = _ExtractFake([
        {
            "claim": "Ducat was married", "category": "person", "confidence": 0.9,
            "source_ids": [src.id],
            "supporting_text": "James Ducat was married with three children",
        },
        {
            "claim": "A confession was made in court", "category": "evidence",
            "confidence": 0.9, "source_ids": [src.id],
            "supporting_text": "the defendant confessed in open court",
        },
        {
            "claim": "No supporting text given", "category": "context",
            "confidence": 0.5, "source_ids": [src.id],
        },
    ])
    _run_extract(db_session, case, fake)
    from app.db.models import Fact

    facts = {f.claim: f for f in db_session.query(Fact).filter(Fact.case_id == case.id).all()}
    assert facts["Ducat was married"].quote_verified is True
    assert facts["A confession was made in court"].quote_verified is False
    assert facts["No supporting text given"].quote_verified is None


def test_supporting_span_is_resolved_from_the_real_chunk_not_retyped(db_session):
    """Part 2(e) item 4: the model points at its evidence (chunk id +
    character offsets) instead of retyping it; the actual text is sliced
    from the real chunk in code, never trusted from the model's own copy
    — and never asked of it, cutting output tokens for long quotes."""
    from app.db.models import Fact, SourceChunk

    case = _mk_case(db_session)
    src = _mk_source(db_session, case, content_status="full_text")
    chunk_text = ("Keeper James Ducat was married with three children in Edinburgh. "
                 f"span-resolution-test {uuid.uuid4().hex[:8]}")
    chunk = SourceChunk(source_id=src.id, chunk_index=0, text=chunk_text,
                       content_hash="x" * 64, language="en")
    db_session.add(chunk)
    db_session.commit()

    fake = _ExtractFake([
        {
            "claim": "Ducat was married", "category": "person", "confidence": 0.9,
            "source_ids": [src.id], "chunk_ids": [chunk.id],
            "supporting_span": {"chunk_id": chunk.id, "start": 8, "end": 34},
        },
        {
            "claim": "Out of bounds span", "category": "context", "confidence": 0.5,
            "source_ids": [src.id], "chunk_ids": [chunk.id],
            "supporting_span": {"chunk_id": chunk.id, "start": 0, "end": 9999},
        },
        {
            "claim": "Unknown chunk referenced", "category": "context", "confidence": 0.5,
            "source_ids": [src.id],
            "supporting_span": {"chunk_id": 999999, "start": 0, "end": 5},
        },
        {
            "claim": "No span given", "category": "context", "confidence": 0.5,
            "source_ids": [src.id],
            "supporting_span": None,
        },
    ])
    _run_extract(db_session, case, fake)

    facts = {f.claim: f for f in db_session.query(Fact).filter(Fact.case_id == case.id).all()}
    assert facts["Ducat was married"].supporting_text == chunk_text[8:34]
    assert facts["Ducat was married"].quote_verified is True
    assert facts["Out of bounds span"].supporting_text is None
    assert facts["Out of bounds span"].quote_verified is False
    assert facts["Unknown chunk referenced"].quote_verified is None
    assert facts["No span given"].quote_verified is None


def test_chunk_selection_sends_the_most_relevant_chunks_not_the_first_n(db_session, monkeypatch):
    """Part 2(e) item 2: cut the text, not the source — a long source's
    chunks are ranked by relevance to the case, not truncated at the
    first N in document order."""
    from app.core.ai_config import ai_config
    from app.db.models import SourceChunk

    monkeypatch.setattr(ai_config.source_chunking, "max_chunks_per_source_for_extraction", 2)
    # A unique marker instead of a real-world case theme: several other
    # test files independently use "Flannan Isles"/lighthouse-keeper
    # content as a shared fixture for unrelated discovery/dedup checks,
    # and the full suite shares one database — colliding with it caused a
    # spurious failure there when this test ran earlier in the same run.
    marker = f"relevance-marker-{uuid.uuid4().hex[:8]}"
    case = _mk_case(db_session)
    case.canonical_title = f"a case about {marker}"
    db_session.commit()
    src = _mk_source(db_session, case, content_status="full_text")
    chunk_texts = [
        "An unrelated weather almanac entry about rainfall averages.",
        f"The central fact of {marker} was documented here.",      # 1: relevant
        "A recipe for traditional Scottish shortbread biscuits.",
        f"Investigators confirmed {marker} in their report.",      # 3: relevant
        "An advertisement for a steamship ticket to Glasgow.",
    ]
    chunks = [
        SourceChunk(source_id=src.id, chunk_index=i, text=t, content_hash=f"{i}" * 64, language="en")
        for i, t in enumerate(chunk_texts)
    ]
    db_session.add_all(chunks)
    db_session.commit()
    relevant_ids = {chunks[1].id, chunks[3].id}

    class RelevanceAwareEmbedder(DistinctEmbedder):
        async def embed(self, texts):
            import numpy as np
            vectors = []
            for t in texts:
                vectors.append([1.0, 0.0] if marker in t else [0.0, 1.0])
            return np.array(vectors, dtype=np.float32)

    seen_chunk_ids = []

    class RecordingFake(_ExtractFake):
        async def generate_structured(self, role, system, user):
            if role == "fact_extractor":
                for s in json.loads(user)["sources"]:
                    seen_chunk_ids.extend(c["id"] for c in s["chunks"])
            return await super().generate_structured(role, system, user)

    _run_extract(db_session, case, RecordingFake([]), RelevanceAwareEmbedder())
    assert set(seen_chunk_ids) == relevant_ids


def test_deleting_a_promoted_fact_clears_the_reference_instead_of_blocking(tmp_path):
    """A real bug found live on case 5, 2026-10-16: _store_facts deletes
    and re-inserts every Fact for a case on each research run, but
    claim_clusters.promoted_fact_id and transcript_claims.promoted_fact_id
    pointed at a specific fact row with the implicit NO ACTION delete
    behaviour — a stale "promoted to fact #N" pointer from an earlier run
    blocked the delete outright (ForeignKeyViolation). Both are now
    ON DELETE SET NULL (migration 0025).

    A fully isolated, dedicated database: SQLite only enforces foreign
    keys when PRAGMA foreign_keys=ON is set per-connection, and that
    setting leaking onto a pooled connection the shared db_session fixture
    hands to a later, unrelated test caused real cross-test failures
    (tried first, reverted) — a throwaway engine sidesteps the pool
    entirely, which also means this test needs no cleanup of its own.
    """
    from alembic import command
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from app.db.migrate import alembic_config
    from app.db.models import Case, ClaimCluster, Fact, TranscriptClaim, VideoSource

    db_path = tmp_path / "promoted_fact_fk.db"
    command.upgrade(alembic_config(f"sqlite:///{db_path}"), "head")
    engine = create_engine(f"sqlite:///{db_path}")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    db = sessionmaker(bind=engine)()
    try:
        case = Case(canonical_title="fk test", slug=f"fk-test-{uuid.uuid4().hex[:8]}", language="en")
        db.add(case)
        db.commit()
        fact = Fact(case_id=case.id, claim="a promoted claim", confidence=0.9)
        db.add(fact)
        db.commit()
        cluster = ClaimCluster(case_id=case.id, canonical_claim_en="a promoted claim",
                               promoted_fact_id=fact.id)
        video = VideoSource(case_id=case.id, video_id="v1", url="https://example/v1",
                            title="v1", channel_name="c")
        db.add_all([cluster, video])
        db.commit()
        claim = TranscriptClaim(case_id=case.id, video_source_id=video.id,
                                canonical_claim_en="a promoted claim",
                                original_claim="a promoted claim",
                                promoted_fact_id=fact.id)
        db.add(claim)
        db.commit()

        db.query(Fact).filter_by(id=fact.id).delete()
        db.commit()

        db.refresh(cluster)
        db.refresh(claim)
        assert cluster.promoted_fact_id is None
        assert claim.promoted_fact_id is None
    finally:
        db.close()
        engine.dispose()


def test_extraction_cache_makes_no_provider_call_on_a_repeat(db_session):
    """Part 2(e) item 3: the same content, prompt version and model is
    never extracted twice — a restart must not pay for it again."""
    case = _mk_case(db_session)
    src = _mk_source(
        db_session, case, content_status="full_text", is_authorized_text=True,
        raw_text="The keeper's logbook recorded fair weather on the twelfth of December.",
    )
    calls = {"n": 0}

    class CountingFake(_ExtractFake):
        async def generate_structured(self, role, system, user):
            if role == "fact_extractor":
                calls["n"] += 1
            return await super().generate_structured(role, system, user)

    fake = CountingFake([
        {"claim": "Fair weather was logged", "category": "context",
         "confidence": 0.8, "source_ids": [src.id]},
    ])
    _run_extract(db_session, case, fake)
    assert calls["n"] == 1

    # A second extraction run over the exact same content (e.g. a restart
    # re-running the stage) must be served entirely from cache.
    _run_extract(db_session, case, fake)
    assert calls["n"] == 1


def test_extraction_cache_is_portable_across_cases_with_remapped_ids(db_session):
    """Part 2(e) item 3: "also across cases that share a source" — the
    cached fact's source_ids/chunk_ids must point at the NEW case's real
    row ids, never the ids of the case that first populated the cache."""
    case_a = _mk_case(db_session)
    src_a = _mk_source(
        db_session, case_a, content_status="full_text", is_authorized_text=True,
        raw_text="A rescue boat was dispatched from Breasclete at dawn.",
    )
    fake = _ExtractFake([
        {"claim": "A rescue boat was dispatched", "category": "context",
         "confidence": 0.8, "source_ids": [src_a.id]},
    ])
    _run_extract(db_session, case_a, fake)

    case_b = _mk_case(db_session)
    src_b = _mk_source(
        db_session, case_b, content_status="full_text", is_authorized_text=True,
        raw_text="A rescue boat was dispatched from Breasclete at dawn.",
    )
    assert src_b.id != src_a.id

    class FailingFake(_ExtractFake):
        async def generate_structured(self, role, system, user):
            if role == "fact_extractor":
                raise AssertionError("must be served from cache, not a real call")
            return await super().generate_structured(role, system, user)

    _run_extract(db_session, case_b, FailingFake([]))
    from app.db.models import Fact

    fact_b = db_session.query(Fact).filter(Fact.case_id == case_b.id).one()
    assert json.loads(fact_b.source_ids_json) == [src_b.id]


# ---------------------------------------------------------------------------
# 13. EvidenceCoverageMatrix
# ---------------------------------------------------------------------------


def test_coverage_matrix_counts_assigned_buckets():
    pack = {
        "facts": [
            {"id": "F001", "narrative_value": "fact"},
            {"id": "F002", "narrative_value": "human_detail"},
            {"id": "F003", "narrative_value": "scene_detail"},
        ],
        "timeline": [{"id": "T001"}],
        "contradictions": [{"id": "C001"}],
        "approved_quotes": [{"id": "F004", "narrative_value": "quote"}],
        "human_details": [{"id": "F002", "narrative_value": "human_detail"}],
        "scene_details": [{"id": "F003", "narrative_value": "scene_detail"}],
    }
    acts = [
        {"id": "a1", "evidence_ids": ["F001", "T001"]},
        {"id": "a2", "evidence_ids": ["F002", "F003", "C001", "F004"]},
    ]
    m = evidence_coverage_matrix(acts, pack)
    assert m["a1"]["facts"] == 1 and m["a1"]["timeline"] == 1
    assert m["a2"]["human_details"] == 1 and m["a2"]["scene_details"] == 1
    assert m["a2"]["contradictions"] == 1 and m["a2"]["quotes"] == 1


# ---------------------------------------------------------------------------
# 14-16. Conservative capacity
# ---------------------------------------------------------------------------


def test_summary_only_pool_cannot_support_45_minutes(db_session):
    """30 summary-only sources must not outrank a few rich documents."""
    case = _mk_case(db_session)
    sources = [
        _mk_source(
            db_session, case, title=f"S{i}", content_status="summary_only",
            summary="s",
        )
        for i in range(30)
    ]
    facts = [_mk_fact(db_session, case, f"claim {i}") for i in range(20)]
    cap = estimate_narrative_capacity(facts, [], sources, 45)
    assert cap["status"] == "insufficient_for_requested_length"
    assert "deep source text" in cap["weak_areas"]


def test_fulltext_sources_raise_capacity(db_session):
    case = _mk_case(db_session)
    thin = _mk_source(db_session, case, title="thin",
                    content_status="summary_only", summary="s")
    facts = [_mk_fact(db_session, case, f"claim {i}") for i in range(20)]
    before = estimate_narrative_capacity(facts, [], [thin], 45)

    for i in range(5):
        _mk_source(
            db_session, case, title=f"ft{i}", content_status="full_text",
            raw_text="x " * 500, source_family=f"fam{i}",
        )
    deep = [thin] + [
        s for s in case.sources if s.content_status == "full_text"
    ]
    after = estimate_narrative_capacity(facts, [], deep, 45)
    assert after["estimated_supported_minutes"] > before[
        "estimated_supported_minutes"]


def test_weak_evidence_marks_weak_areas(db_session):
    case = _mk_case(db_session)
    src = _mk_source(db_session, case, content_status="summary_only")
    cap = estimate_narrative_capacity([], [], [src], 45)
    assert "human detail" in cap["weak_areas"]
    assert "scene texture" in cap["weak_areas"]
    assert "quotes" in cap["weak_areas"]


# ---------------------------------------------------------------------------
# 17-18. ResearchGapPlan + targeted follow-up
# ---------------------------------------------------------------------------


def test_gap_plan_generated_for_thin_evidence(db_session):
    case = _mk_case(db_session)
    src = _mk_source(db_session, case, content_status="summary_only")
    plan = research_gap_plan([], [], [src], 45)
    types = {m["type"] for m in plan["missing"]}
    assert "human_detail" in types and "scene_detail" in types
    assert "full_text_source" in types
    assert plan["status"] == "research_gaps_present"


def test_followup_endpoint_blocks_when_no_gaps(client, db_session):
    case = _mk_case(db_session)
    from app.db.models import Source

    for i in range(8):
        db_session.add(Source(
            case_id=case.id, title=f"ft{i}", url=f"https://e/f{i}",
            source_type="article", content_status="full_text",
            raw_text="x " * 500, source_family=f"fam{i}",
        ))
    db_session.commit()
    for i in range(60):
        _mk_fact(
            db_session, case, f"claim {i}",
            narrative_value=["human_detail", "scene_detail",
                             "investigation_detail", "quote"][i % 4],
            event_date="2000-01-01",
        )
    r = client.post(f"/api/cases/{case.id}/research/followup")
    assert r.status_code == 409  # nothing to chase — no blind reruns


def test_followup_builds_targeted_objective(client, db_session, monkeypatch):
    """A gap plan produces a targeted research request, not broad reruns."""
    import app.api.deps as main_mod

    case = _mk_case(db_session)
    _mk_source(db_session, case, content_status="summary_only")

    captured = {}

    class FakeResearch:
        name = "legacy-provider"

        def is_configured(self):
            return True

        async def start_case_research(self, case_title, language,
                                      objective, research_languages):
            captured["objective"] = objective
            captured["langs"] = research_languages
            return "session-123"

    monkeypatch.setattr(
        main_mod, "get_research_provider", lambda: FakeResearch()
    )
    r = client.post(f"/api/cases/{case.id}/research/followup")
    assert r.status_code == 200
    assert "TARGETED" in captured["objective"]
    assert set(captured["langs"]) == {"en", "de", "fa", "ar"}
    assert r.json()["gap_plan"]["missing"]


# ---------------------------------------------------------------------------
# 19-20. Act-scoped packs + scoped grounding
# ---------------------------------------------------------------------------


def test_act_pack_only_contains_assigned_evidence():
    pack = {
        "facts": [{"id": "F001"}, {"id": "F002"}, {"id": "F003"}],
        "disputed_facts": [{"id": "F002"}],
        "timeline": [{"id": "T001"}, {"id": "T002"}],
        "contradictions": [{"id": "C001"}],
        "approved_context": [{"id": "CTX001"}],
        "human_details": [{"id": "F002"}],
        "scene_details": [],
        "investigation_details": [],
        "physical_evidence": [],
        "historical_context": [],
        "environment_details": [],
        "approved_quotes": [],
        "research_gaps": [],
    }
    act = build_act_pack(pack, ["F002", "T001"])
    assert [f["id"] for f in act["facts"]] == ["F002"]
    assert [t["id"] for t in act["timeline"]] == ["T001"]
    assert act["contradictions"] == []
    assert act["human_details"][0]["id"] == "F002"
    # context survives — acts may need shared background
    assert act["approved_context"]
    # unassigned -> full pack fallback
    assert build_act_pack(pack, []) is pack


def test_grounding_scoped_to_act_evidence(db_session, monkeypatch):
    """The per-act grounding validator receives the act-scoped pack, not
    the entire case database (Part 18)."""
    from app.db.models import Fact
    from app.providers.generation.base import GenerationResult
    import app.agents.story as story_mod

    case = _mk_case(db_session)
    _mk_fact(db_session, case, "alpha claim")
    _mk_fact(db_session, case, "beta claim")
    _mk_fact(db_session, case, "gamma claim")

    captured = {}

    class Fake:
        def is_configured(self):
            return True

        async def generate_text(self, role, system, user):
            return GenerationResult(
                text="some narration text", model="m", provider="f"
            )

        async def generate_structured(self, role, system, user):
            u = json.loads(user)
            if role == "grounding_validator":
                captured["evidence"] = u["evidence"]
            return {
                "grounding_score": 1.0, "unsupported_claims": [],
                "uncertainty_errors": [],
            }, GenerationResult(text="{}", model="m", provider="f")

    monkeypatch.setattr(story_mod, "get_generation_provider", lambda: Fake())
    pipe = StoryPipeline()
    pack = build_evidence_pack(
        db_session.query(Fact).filter(Fact.case_id == case.id).all(), [], []
    )
    # The pipeline grounds each section against its act-scoped pack.
    act_pack = build_act_pack(pack, ["F001"])
    asyncio.run(
        pipe._grounding_check_only(
            db_session,
            case,
            # unmatched token forces the LLM path past deterministic triage
            "para mentions only alpha and the year 1901",
            act_pack,
        )
    )
    ids = [f["id"] for f in captured["evidence"]["facts"]]
    assert ids == ["F001"]  # F002/F003 never reached the validator


def test_claim_first_skips_llm_when_clean(db_session, monkeypatch):
    """Paragraphs whose specifics all match the pack never reach the LLM
    (Part 19) — deterministic pass costs zero tokens."""
    called = {"n": 0}

    class Fake:
        async def generate_structured(self, role, system, user):
            called["n"] += 1
            raise AssertionError("should not be called")

    pipe = StoryPipeline()
    pipe.gen = Fake()
    pack = {
        "facts": [{"id": "F001", "claim": "The lighthouse stood at 23 metres "
                   "and keeper James Ducat logged the weather daily"}],
        "timeline": [], "contradictions": [],
        "approved_context": [],
    }
    rep = asyncio.run(
        pipe._grounding_check_only(
            db_session,
            case := _mk_case(db_session),
            "The lighthouse stood at 23 metres and keeper James Ducat "
            "logged the weather daily.",
            pack,
        )
    )
    assert called["n"] == 0
    assert rep["method"] == "deterministic_scan"
    assert rep["grounding_score"] == 1.0


# ---------------------------------------------------------------------------
# 21-22. Provider authorization classification + generation gate
# ---------------------------------------------------------------------------


def _real_preflight(monkeypatch, provider):
    """Undo the autouse stub and test the real preflight against a fake."""
    import app.api.deps as main_mod

    monkeypatch.undo()
    monkeypatch.setattr(main_mod, "get_generation_provider", lambda: provider)
    return main_mod.require_generation_authorized


class _QuotaProvider:
    name = "legacy-provider"

    async def provider_status(self):
        return {
            "configured": True, "reachable": True, "authorized": False,
            "status": "quota_or_permission_error",
        }


class _HealthyProvider:
    name = "legacy-provider"

    async def provider_status(self):
        return {
            "configured": True, "reachable": True, "authorized": True,
            "status": "authorized",
        }


def test_provider_403_classified_correctly(monkeypatch):
    fn = _real_preflight(monkeypatch, _QuotaProvider())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(fn())
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "provider_unauthorized"
    assert (exc.value.detail["provider_status"]["status"]
            == "quota_or_permission_error")


def test_no_live_generation_when_unauthorized(
    client, db_session, monkeypatch
):
    """An unauthorized provider must stop generation before any expensive
    work — Part 23."""
    _real_preflight(monkeypatch, _QuotaProvider())
    case = _mk_case(db_session)
    r = client.post(
        f"/api/cases/{case.id}/master-story/generate",
        json={"target_minutes": 45, "language": "en"},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "provider_unauthorized"


def test_healthy_provider_passes_preflight(monkeypatch):
    fn = _real_preflight(monkeypatch, _HealthyProvider())
    st = asyncio.run(fn())
    assert st["authorized"] is True


# ---------------------------------------------------------------------------
# Layered readiness: source / evidence / master stay separate
# ---------------------------------------------------------------------------

from app.services.readiness import build_readiness  # noqa: E402

_OK_PROVIDER = {
    "configured": True, "reachable": True,
    "authorized": True, "status": "authorized",
}
_DEAD_PROVIDER = {
    "configured": True, "reachable": True,
    "authorized": False, "status": "quota_or_permission_error",
}


def _rich_sources(db, case, n_full=8):
    for i in range(n_full):
        _mk_source(
            db, case, content_status="full_text",
            raw_text=" ".join(["evidence"] * 600),
            source_family=f"fam{i}", retrieved_at=utc_now(),
        )


def utc_now():
    from app.utils import utc_now as _u
    return _u()


def test_readiness_rich_sources_no_extraction(db_session):
    """Deep sources but extraction never ran → source ready, evidence
    incomplete, master blocked — 'capacity' alone must not say ready."""
    case = _mk_case(db_session)
    _rich_sources(db_session, case)

    r = build_readiness(db_session, case.id, 45, provider_status=_OK_PROVIDER)
    assert r["source_readiness"]["status"] == "ready"
    assert r["evidence_readiness"]["status"] == "incomplete"
    assert r["master_readiness"]["status"] == "incomplete_evidence"


def test_readiness_rich_sources_provider_dead(db_session):
    """Same corpus, provider unauthorized → evidence reports
    blocked_provider and master reports provider_blocked."""
    case = _mk_case(db_session)
    _rich_sources(db_session, case)

    r = build_readiness(
        db_session, case.id, 45, provider_status=_DEAD_PROVIDER
    )
    assert r["source_readiness"]["status"] == "ready"
    assert r["evidence_readiness"]["status"] == "blocked_provider"
    assert r["master_readiness"]["status"] == "provider_blocked"


def test_readiness_complete_evidence_master_ready(db_session):
    """Deep sources + extracted evidence + sufficient capacity → ready."""
    case = _mk_case(db_session)
    _rich_sources(db_session, case)
    nvs = ["human_detail", "scene_detail", "investigation", "quote"]
    for i in range(30):
        _mk_fact(
            db_session, case, f"extracted {i}",
            narrative_value=nvs[i % 4],
            event_date=f"2000-01-{(i % 28) + 1:02d}",
            evidence_strength="secondary",
        )
    from app.db.models import Contradiction
    for i in range(3):
        db_session.add(
            Contradiction(case_id=case.id, topic=f"t{i}",
                          description="differ", severity="medium")
        )
    db_session.commit()

    r = build_readiness(db_session, case.id, 45, provider_status=_OK_PROVIDER)
    assert r["source_readiness"]["status"] == "ready"
    assert r["evidence_readiness"]["status"] == "ready"
    assert r["narrative_capacity"]["status"] == "ready"
    assert r["master_readiness"]["status"] == "ready"


def test_readiness_shallow_sources_blocked(db_session):
    """Summary-only corpus → source insufficient → master
    insufficient_research regardless of evidence state."""
    case = _mk_case(db_session)
    for i in range(10):
        _mk_source(db_session, case, content_status="summary_only",
                   publisher=f"p{i}")
    for i in range(20):
        _mk_fact(db_session, case, f"fact {i}")

    r = build_readiness(db_session, case.id, 45, provider_status=_OK_PROVIDER)
    assert r["source_readiness"]["status"] == "insufficient"
    assert r["master_readiness"]["status"] == "insufficient_research"


def test_capacity_endpoint_returns_layers(client, db_session):
    """The capacity endpoint exposes the layered report — no collapsing."""
    case = _mk_case(db_session)
    _rich_sources(db_session, case)
    r = client.get(f"/api/cases/{case.id}/research/capacity")
    assert r.status_code == 200
    body = r.json()
    for key in ("source_readiness", "evidence_readiness",
                "narrative_capacity", "master_readiness"):
        assert key in body
    assert body["source_readiness"]["status"] == "ready"
    assert body["evidence_readiness"]["status"] == "incomplete"
    # Flat capacity fields remain for existing consumers.
    assert body["estimated_supported_minutes"] > 0


def test_fact_extraction_runs_in_per_language_batches(db_session, monkeypatch):
    """One call over every source truncated at max_tokens and only the first
    language got facts; sources are extracted in bounded, single-language
    batches and the facts merged."""
    from app.core.ai_config import ai_config
    from app.providers.generation.base import GenerationResult

    monkeypatch.setattr(ai_config.source_chunking, "max_sources_per_extraction_call", 2)
    case = _mk_case(db_session)
    en = [_mk_source(db_session, case, language="en", content_status="full_text",
                     is_authorized_text=True, raw_text=f"distinct english content number {i}")
          for i in range(3)]
    ar = [_mk_source(db_session, case, language="ar", content_status="full_text",
                     is_authorized_text=True, raw_text="distinct arabic content")]
    calls = []

    class BatchFake(_ExtractFake):
        async def generate_structured(self, role, system, user):
            if role != "fact_extractor":
                return await super().generate_structured(role, system, user)
            bundle = json.loads(user)["sources"]
            calls.append([(s["id"], s["language"]) for s in bundle])
            facts = [{"claim": f"fact of {s['id']}", "source_ids": [s["id"]],
                      "category": "context"} for s in bundle]
            return {"facts": facts}, GenerationResult(text="{}", model="m", provider="fake")

    result = _run_extract(db_session, case, BatchFake([]))
    assert [len(c) for c in calls] == [2, 1, 1]
    assert all(len({lang for _, lang in c}) == 1 for c in calls)
    assert result["facts"] == 4
    assert {s.id for s in en + ar} == {i for c in calls for i, _ in c}


def test_timeline_and_contradiction_prompts_do_not_resend_source_texts(db_session):
    """Only fact extraction reads the chunk texts; the later stages work on the
    extracted claims, so resending every chunk of every source grows with the
    case until it no longer fits."""
    from app.providers.generation.base import GenerationResult

    case = _mk_case(db_session)
    src = _mk_source(db_session, case, language="en", raw_text="long source text",
                     is_authorized_text=True)
    seen = {}

    class Spy(_ExtractFake):
        async def generate_structured(self, role, system, user):
            if role in ("timeline_builder", "contradiction_analyzer"):
                seen[role] = json.loads(user)["sources"]
            return await super().generate_structured(role, system, user)

    fake = Spy([{"claim": "c", "source_ids": [src.id], "category": "context"}])
    _run_extract(db_session, case, fake)
    for role in ("timeline_builder", "contradiction_analyzer"):
        entry = seen[role][0]
        assert entry["id"] == src.id
        assert entry["text"] is None and entry["chunks"] == []


def test_failed_extraction_batch_is_split_and_retried(db_session, monkeypatch):
    """One oversized or malformed reply used to lose the whole pass; the failing
    batch is halved until its sources go through, and a lone failing source
    still fails the extraction."""
    from app.core.ai_config import ai_config
    from app.providers.generation.base import GenerationError, GenerationResult

    monkeypatch.setattr(ai_config.source_chunking, "max_sources_per_extraction_call", 4)
    case = _mk_case(db_session)
    sources = [_mk_source(db_session, case, language="en", content_status="full_text",
                          is_authorized_text=True,
                          raw_text=f"split-and-retry-test content {uuid.uuid4().hex[:8]} number {i}")
               for i in range(4)]
    sizes = []

    class FlakyFake(_ExtractFake):
        async def generate_structured(self, role, system, user):
            if role != "fact_extractor":
                return await super().generate_structured(role, system, user)
            bundle = json.loads(user)["sources"]
            sizes.append(len(bundle))
            if len(bundle) > 1:
                raise GenerationError("invalid_output", "truncated at max_tokens")
            facts = [{"claim": f"fact {bundle[0]['id']}", "source_ids": [bundle[0]["id"]],
                      "category": "context"}]
            return {"facts": facts}, GenerationResult(text="{}", model="m", provider="fake")

    result = _run_extract(db_session, case, FlakyFake([]))
    assert sizes == [4, 2, 1, 1, 2, 1, 1]
    assert result["facts"] == len(sources)

    class AlwaysFails(_ExtractFake):
        async def generate_structured(self, role, system, user):
            if role == "fact_extractor":
                raise ValueError("Expecting value: line 1 column 1 (char 0)")
            return await super().generate_structured(role, system, user)

    # A fresh case/source: the first batch's content is already cached
    # (Part 2(e) item 3) and would otherwise shadow this always-fails fake.
    other_case = _mk_case(db_session)
    _mk_source(db_session, other_case, language="en", content_status="full_text",
              is_authorized_text=True, raw_text="a lone failing source's content")
    with pytest.raises(ValueError):
        _run_extract(db_session, other_case, AlwaysFails([]))


def test_extraction_batches_run_concurrently_and_keep_batch_order(db_session, monkeypatch):
    """Batches are independent, so they run in parallel (bounded by
    extraction_concurrency, each with its own session) and the facts come back
    in batch order."""
    from app.core.ai_config import ai_config
    from app.providers.generation.base import GenerationResult

    monkeypatch.setattr(ai_config.source_chunking, "max_sources_per_extraction_call", 1)
    monkeypatch.setattr(ai_config.source_chunking, "extraction_concurrency", 3)
    case = _mk_case(db_session)
    sources = [_mk_source(db_session, case, language="en", content_status="full_text",
                          is_authorized_text=True,
                          raw_text=f"concurrent-order-test content {uuid.uuid4().hex[:8]} number {i}")
               for i in range(5)]
    state = {"now": 0, "peak": 0}

    class SlowFake(_ExtractFake):
        async def generate_structured(self, role, system, user):
            if role != "fact_extractor":
                return await super().generate_structured(role, system, user)
            source_id = json.loads(user)["sources"][0]["id"]
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
            await asyncio.sleep(0.05 if source_id != sources[0].id else 0.2)
            state["now"] -= 1
            return {"facts": [{"claim": f"fact {source_id}", "source_ids": [source_id],
                               "category": "context"}]}, \
                GenerationResult(text="{}", model="m", provider="fake")

    result = _run_extract(db_session, case, SlowFake([]))
    assert result["facts"] == 5
    assert state["peak"] == 3
    from app.db.models import Fact
    stored = db_session.query(Fact).filter(Fact.case_id == case.id).order_by(Fact.id).all()
    assert [f.claim for f in stored] == [f"fact {s.id}" for s in sources]


def test_research_merges_the_same_fact_from_different_languages(db_session):
    """The same event extracted from an English and a German source is stored
    once, with both sources behind it, and the merge is a tracked run."""
    import numpy as np
    from app.db.models import AgentRun, Fact

    class SameEmbedder(DistinctEmbedder):
        async def embed(self, texts):
            return np.ones((len(texts), 4), dtype=np.float32)

    case = _mk_case(db_session)
    en = _mk_source(db_session, case, language="en", content_status="full_text",
                    is_authorized_text=True, raw_text="Darrow defended both men in court.")
    de = _mk_source(db_session, case, language="de", content_status="full_text",
                    is_authorized_text=True, raw_text="Darrow verteidigte beide Männer vor Gericht.")
    facts = [
        {"claim": "Darrow defended both", "source_ids": [en.id], "category": "legal"},
        {"claim": "Darrow defended both men", "source_ids": [de.id], "category": "legal",
         "original_language": "de", "original_claim": "Darrow verteidigte beide"},
    ]

    class PerSourceFake(_ExtractFake):
        async def generate_structured(self, role, system, user):
            data, res = await super().generate_structured(role, system, user)
            if role == "fact_extractor":
                ids = {s["id"] for s in json.loads(user)["sources"]}
                data = {"facts": [f for f in facts if set(f["source_ids"]) <= ids]}
            return data, res

    result = _run_extract(db_session, case, PerSourceFake([]), SameEmbedder())
    assert result["facts"] == 1
    stored = db_session.query(Fact).filter(Fact.case_id == case.id).one()
    assert sorted(json.loads(stored.source_ids_json)) == sorted([en.id, de.id])
    merger_run = db_session.query(AgentRun).filter_by(
        case_id=case.id, agent_name="Fact Merger").one()
    assert merger_run.output_summary == "facts=1 merged=1"


def test_a_source_with_no_content_is_skipped_with_a_reason_and_never_extracted(db_session):
    """Part 2(e) item 1: skip sources with no case-specific content before
    any extraction call, with the reason recorded, not silently dropped."""
    case = _mk_case(db_session)
    empty = _mk_source(db_session, case, language="en")  # no raw_text, no summary, no chunks
    real = _mk_source(db_session, case, language="en", content_status="full_text",
                      is_authorized_text=True, raw_text="a real article with real content")
    seen_ids = []

    class RecordingFake(_ExtractFake):
        async def generate_structured(self, role, system, user):
            if role == "fact_extractor":
                seen_ids.extend(s["id"] for s in json.loads(user)["sources"])
            return await super().generate_structured(role, system, user)

    _run_extract(db_session, case, RecordingFake([]))
    db_session.refresh(empty)
    db_session.refresh(real)
    assert empty.id not in seen_ids
    assert real.id in seen_ids
    assert empty.extraction_skip_reason and "no extractable content" in empty.extraction_skip_reason
    assert real.extraction_skip_reason is None


def test_a_summary_only_source_still_counts_as_extractable(db_session):
    """A source with no full text but a real summary is still extracted
    from in production (app.agents.research._source_bundle sends the
    summary) — item 1's filter must not skip it."""
    case = _mk_case(db_session)
    src = _mk_source(db_session, case, language="en", content_status="summary_only",
                     summary_en="A human-written summary of the case coverage.")
    seen_ids = []

    class RecordingFake(_ExtractFake):
        async def generate_structured(self, role, system, user):
            if role == "fact_extractor":
                seen_ids.extend(s["id"] for s in json.loads(user)["sources"])
            return await super().generate_structured(role, system, user)

    _run_extract(db_session, case, RecordingFake([]))
    assert src.id in seen_ids


def test_a_near_duplicate_source_is_skipped_in_favour_of_the_more_reliable_one(db_session):
    """Part 2(e) item 1: a near-duplicate/syndicated copy (same language,
    high content similarity) is skipped, not extracted twice."""
    class DuplicateAwareEmbedder(DistinctEmbedder):
        async def embed(self, texts):
            import numpy as np
            # The two syndicated copies get an identical vector; the
            # unrelated third source gets its own distinct direction.
            vectors = []
            for t in texts:
                vectors.append([1.0, 0.0, 0.0, 0.0] if "syndicated" in t else [0.0, 1.0, 0.0, 0.0])
            return np.array(vectors, dtype=np.float32)

    case = _mk_case(db_session)
    weak = _mk_source(db_session, case, language="en", content_status="full_text",
                      is_authorized_text=True, reliability_score=0.3,
                      raw_text="syndicated wire copy of the same article")
    strong = _mk_source(db_session, case, language="en", content_status="full_text",
                        is_authorized_text=True, reliability_score=0.9,
                        raw_text="syndicated wire copy of the same article, published elsewhere")
    unrelated = _mk_source(db_session, case, language="en", content_status="full_text",
                           is_authorized_text=True, raw_text="a completely unrelated real article")
    seen_ids = []

    class RecordingFake(_ExtractFake):
        async def generate_structured(self, role, system, user):
            if role == "fact_extractor":
                seen_ids.extend(s["id"] for s in json.loads(user)["sources"])
            return await super().generate_structured(role, system, user)

    _run_extract(db_session, case, RecordingFake([]), DuplicateAwareEmbedder())
    db_session.refresh(weak)
    db_session.refresh(strong)
    assert weak.id not in seen_ids
    assert strong.id in seen_ids
    assert unrelated.id in seen_ids
    assert weak.extraction_skip_reason and f"source {strong.id}" in weak.extraction_skip_reason
    assert strong.extraction_skip_reason is None


def test_sources_beyond_the_per_case_limit_are_skipped_with_a_reason(db_session, monkeypatch):
    """Part 2(e) item 1: only the configured top N sources per case are
    ever sent to the extractor; the rest are skipped with a reason."""
    from app.core.ai_config import ai_config

    monkeypatch.setattr(ai_config.source_chunking, "max_sources_per_case_for_extraction", 2)
    case = _mk_case(db_session)
    sources = [
        _mk_source(db_session, case, language="en", content_status="full_text",
                  is_authorized_text=True, reliability_score=0.9 - i * 0.1,
                  raw_text=f"limit-test unrelated content {uuid.uuid4().hex[:8]} rank {i}")
        for i in range(4)
    ]
    seen_ids = []

    class RecordingFake(_ExtractFake):
        async def generate_structured(self, role, system, user):
            if role == "fact_extractor":
                seen_ids.extend(s["id"] for s in json.loads(user)["sources"])
            return await super().generate_structured(role, system, user)

    _run_extract(db_session, case, RecordingFake([]))
    for s in sources:
        db_session.refresh(s)
    assert len(seen_ids) == 2
    # The two highest-reliability sources (rank 0, 1) are kept.
    assert {sources[0].id, sources[1].id} == set(seen_ids)
    for s in sources[2:]:
        assert s.extraction_skip_reason and "per-case extraction limit" in s.extraction_skip_reason
