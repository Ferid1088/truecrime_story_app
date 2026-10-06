"""Deep-source ingestion, evidence enrichment and capacity v2 tests.

Covers the Part-27 checklist: content states, chunking, provenance,
dedupe, typed evidence, coverage matrix, conservative capacity, gap
plans, act-scoped packs/grounding and provider authorization gating.
"""
import asyncio
import json
import uuid

import pytest
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


def _run_extract(db_session, case, fake):
    import app.agents.research as research_mod

    original = research_mod.get_generation_provider
    research_mod.get_generation_provider = lambda: fake
    try:
        return asyncio.run(research_mod.ResearchAgent().run(db_session, case))
    finally:
        research_mod.get_generation_provider = original


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
    src = _mk_source(db_session, case)
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
    import app.main as main_mod

    case = _mk_case(db_session)
    _mk_source(db_session, case, content_status="summary_only")

    captured = {}

    class FakeResearch:
        name = "openrouter"

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
    import app.main as main_mod

    monkeypatch.undo()
    monkeypatch.setattr(main_mod, "get_generation_provider", lambda: provider)
    return main_mod._require_generation_authorized


class _QuotaProvider:
    name = "openrouter"

    async def provider_status(self):
        return {
            "configured": True, "reachable": True, "authorized": False,
            "status": "quota_or_permission_error",
        }


class _HealthyProvider:
    name = "openrouter"

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
