"""Tests for central AI config + the APIMaster generation provider.

All APIMaster/OpenRouter HTTP calls are mocked — no real credits are spent.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.core.ai_config import AIConfig, ai_config, REQUIRED_ROLES
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError, GenerationResult
from app.providers.generation.apimaster import APIMasterGenerationProvider


def _raw_config() -> dict:
    from app.core.ai_config import load_raw_config

    return load_raw_config()


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------


def test_config_file_loads():
    assert ai_config.providers.research == "truecrime"
    assert ai_config.providers.generation == "apimaster"
    assert ai_config.research_provider().type == "search_engine"
    assert ai_config.research_provider().secret_env is None
    assert ai_config.generation_provider().secret_env == "TrueCrime_APIMASTER_API_KEY"
    for role in REQUIRED_ROLES:
        assert ai_config.model_for(role)
        assert ai_config.generation_for(role).max_tokens > 0


def test_invalid_config_fails():
    raw = _raw_config()
    raw["generation_providers"]["apimaster"]["routing"]["writer"] = "nonexistent_alias"
    with pytest.raises(Exception):
        AIConfig.model_validate(raw)

    raw = _raw_config()
    raw["generation_providers"]["apimaster"]["routing"]["writer"] = "unknown_role"
    del raw["generation_providers"]["apimaster"]["models"]["cheap"]
    with pytest.raises(Exception):
        AIConfig.model_validate(raw)

    raw = _raw_config()
    raw["generation"]["writer"]["temperature"] = 9.9
    with pytest.raises(Exception):
        AIConfig.model_validate(raw)

    raw = _raw_config()
    del raw["generation"]["engagement_critic"]
    with pytest.raises(Exception):
        AIConfig.model_validate(raw)

    raw = _raw_config()
    raw["providers"]["generation"] = "something-else"
    with pytest.raises(Exception):
        AIConfig.model_validate(raw)


def test_invalid_fallback_config_fails():
    raw = _raw_config()
    raw["generation_providers"]["apimaster"]["fallbacks"] = {"cheap": ["ghost"]}
    with pytest.raises(Exception):
        AIConfig.model_validate(raw)

    raw = _raw_config()
    raw["generation_providers"]["apimaster"]["fallbacks"] = {"cheap": ["cheap"]}
    with pytest.raises(Exception):
        AIConfig.model_validate(raw)


def test_invalid_story_config_fails():
    raw = _raw_config()
    raw["story"]["minimum_length_ratio"] = 1.5
    with pytest.raises(Exception):
        AIConfig.model_validate(raw)

    raw = _raw_config()
    raw["story"]["minimum_length_ratio"] = 0.9
    raw["story"]["maximum_length_ratio"] = 0.5
    with pytest.raises(Exception):
        AIConfig.model_validate(raw)


def test_missing_secret_env_fails():
    raw = _raw_config()
    del raw["generation_providers"]["apimaster"]["secret_env"]
    with pytest.raises(Exception):
        AIConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Logical routing
# ---------------------------------------------------------------------------

EXPECTED_ROUTING = {
    "fact_extractor": "cheap",
    "timeline_builder": "cheap",
    "contradiction_analyzer": "cheap",
    "discovery": "cheap",
    "grounding_validator": "cheap",
    "consistency_checker": "cheap",
    "story_director": "writer",
    "writer": "writer",
    "rewriter": "writer",
    "engagement_critic": "premium",
    "section_critic": "premium",
    "final_editor": "premium",
}


def test_logical_model_routing():
    models = ai_config.generation_provider().models
    for role, alias in EXPECTED_ROUTING.items():
        assert ai_config.alias_for(role) == alias, role
        assert ai_config.model_for(role) == models[alias], role


def test_model_aliases_resolve():
    models = ai_config.generation_provider().models
    assert set(models) == {
        "cheap", "research_intelligence", "writer", "premium", "embedding"}
    for spec in models.values():
        assert "/" not in spec  # bare APIMaster model IDs


def test_fallback_chain_from_config():
    models = ai_config.generation_provider().models
    assert ai_config.fallback_models_for("writer") == [models["premium"]]
    assert ai_config.fallback_models_for("fact_extractor") == [models["writer"]]
    # Reviewers never fall back to the writer model (review_independence):
    # a rate-limited critic must not become the author grading itself.
    assert ai_config.fallback_models_for("engagement_critic") == [models["cheap"]]
    assert models["writer"] not in ai_config.fallback_models_for(
        "native_language_critic"
    )


def test_unknown_role_raises():
    with pytest.raises(KeyError):
        ai_config.model_for("nonexistent_role")
    with pytest.raises(KeyError):
        ai_config.generation_for("nonexistent_role")


# ---------------------------------------------------------------------------
# Provider behaviour (mocked HTTP)
# ---------------------------------------------------------------------------


def _provider() -> APIMasterGenerationProvider:
    p = APIMasterGenerationProvider()
    p.api_key = "apk-test-key"
    return p


def test_generate_text_uses_routed_model():
    provider = _provider()
    cheap = ai_config.generation_provider().models["cheap"]
    seen = {}

    async def fake_chat(model, system, user, gen):
        seen["model"] = model
        return "{}", model, {}

    with patch.object(provider, "_chat", side_effect=fake_chat):
        res = asyncio.run(provider.generate_text("fact_extractor", "sys", "user"))
    assert seen["model"] == cheap
    assert res.provider == "apimaster"
    assert res.fallback_used is False


def test_fallback_on_rate_limit_records_actual_model():
    provider = _provider()
    models = ai_config.generation_provider().models
    calls = []

    async def flaky(model, system, user, gen):
        calls.append(model)
        if len(calls) == 1:
            raise GenerationError("rate_limited", "APIMaster rate limit exceeded (429).")
        return "text", model, {}

    with patch.object(provider, "_chat", side_effect=flaky):
        res = asyncio.run(provider.generate_text("writer", "sys", "user"))
    assert calls == [models["writer"], models["premium"]]
    assert res.model == models["premium"]
    assert res.fallback_used is True


def test_no_fallback_on_unauthorized():
    provider = _provider()

    async def denied(model, system, user, gen):
        raise GenerationError("unauthorized", "APIMaster API key rejected (401).")

    with patch.object(provider, "_chat", side_effect=denied):
        with pytest.raises(GenerationError) as exc:
            asyncio.run(provider.generate_text("writer", "sys", "user"))
    assert exc.value.kind == "unauthorized"


def test_invalid_json_output_no_fallback():
    provider = _provider()

    async def garbage(model, system, user, gen):
        return "not json at all", model, {}

    with patch.object(provider, "_chat", side_effect=garbage):
        with pytest.raises(GenerationError) as exc:
            asyncio.run(provider.generate_structured("fact_extractor", "sys", "user"))
    assert exc.value.kind == "invalid_output"


def test_structured_parses_json():
    provider = _provider()

    async def good(model, system, user, gen):
        return json.dumps({"facts": []}), model, {}

    with patch.object(provider, "_chat", side_effect=good):
        data, res = asyncio.run(provider.generate_structured("fact_extractor", "sys", "user"))
    assert data == {"facts": []}
    assert res.provider == "apimaster"


def test_missing_key_raises():
    provider = APIMasterGenerationProvider()
    provider.api_key = ""
    with pytest.raises(GenerationError) as exc:
        provider._headers()
    assert exc.value.kind == "missing_key"
    assert ai_config.generation_provider().secret_env in str(exc.value)


def test_provider_reads_key_via_config_secret_env(monkeypatch):
    monkeypatch.setenv(ai_config.generation_provider().secret_env, "apk-env-check")
    provider = APIMasterGenerationProvider()
    assert provider.api_key == "apk-env-check"
    assert provider.is_configured()


# ---------------------------------------------------------------------------
# Quality gates (pure logic — no HTTP)
# ---------------------------------------------------------------------------


def test_language_quality_detects_script_leakage():
    from app.utils import language_quality

    cfg = ai_config.language_quality_for("fa")
    clean_fa = "این یک روایت کاملاً فارسی است دربارهٔ سه نگهبان مناره که ناپدید شدند. " * 5
    assert language_quality(clean_fa, "fa", cfg)["pass"] is True

    corrupted = clean_fa + " silencio télégraf escena hielten mão bijna corazón siempre"
    res = language_quality(corrupted, "fa", cfg)
    assert res["pass"] is False
    assert res["foreign_token_ratio"] > 0.03

    # Unconfigured languages always pass.
    assert language_quality("anything goes", "en", None)["pass"] is True


def test_length_gate_boundaries():
    from app.agents.story import StoryPipeline

    pipe = StoryPipeline()
    target = ai_config.story.default_target_minutes * ai_config.story.words_per_minute
    inside = " ".join(["w"] * int(target * 0.95))
    short = " ".join(["w"] * int(target * 0.5))
    long = " ".join(["w"] * int(target * 1.5))

    assert pipe._evaluate_gates(inside, "en", ai_config.story.default_target_minutes)["pass"]
    assert not pipe._evaluate_gates(short, "en", ai_config.story.default_target_minutes)["pass"]
    assert not pipe._evaluate_gates(long, "en", ai_config.story.default_target_minutes)["pass"]


def test_purity_gate_flags_urls():
    from app.agents.story import StoryPipeline

    pipe = StoryPipeline()
    target = ai_config.story.default_target_minutes
    words = target * ai_config.story.words_per_minute
    story = " ".join(["w"] * int(words)) + " https://example.com"
    gates = pipe._evaluate_gates(story, "en", target)
    assert "output_purity" in gates["failures"]


# ---------------------------------------------------------------------------
# Story version status / best-version / audit stamping
# ---------------------------------------------------------------------------


def _mk_case(db, title=None):
    import uuid
    from app.db.models import Case
    from app.utils import slugify

    title = title or f"Case {uuid.uuid4().hex[:8]}"
    c = Case(canonical_title=title, slug=slugify(title), language="en")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_new_version_status_and_best_flag(db_session):
    from app.agents.story import StoryPipeline
    from app.providers.generation.base import GenerationResult

    pipe = StoryPipeline()
    case = _mk_case(db_session)
    res = GenerationResult(text="x", model="m/test", provider="openrouter")

    v1 = pipe._new_version(
        db_session, case, "plan", [{"id": "full", "text": "story one"}],
        "story one", 80.0, None, "not_evaluated",
        {"quality_gates": {"pass": True}}, "en", res,
    )
    assert v1.status == "ready" and v1.is_best is True
    assert v1.generation_model == "m/test"
    assert v1.text_hash

    # Lower score must not dethrone v1.
    v2 = pipe._new_version(
        db_session, case, "plan", [{"id": "full", "text": "story two"}],
        "story two", 60.0, None, "not_evaluated",
        {"quality_gates": {"pass": False}}, "en", res,
    )
    assert v2.status == "needs_revision" and v2.is_best is False
    db_session.refresh(v1)
    assert v1.is_best is True

    # Higher score becomes the new best.
    v3 = pipe._new_version(
        db_session, case, "plan", [{"id": "full", "text": "story three"}],
        "story three", 90.0, None, "not_evaluated",
        {"quality_gates": {"pass": True}}, "en", res,
    )
    assert v3.is_best is True
    db_session.refresh(v1)
    assert v1.is_best is False


def test_stamp_run_records_role_and_fallback(db_session):
    from app.services.tracking import record_run, stamp_run

    run = record_run(db_session, None, "T", "completed")
    res = GenerationResult(text="x", model="m/fb", provider="openrouter", fallback_used=True)
    stamp_run(run, res, "writer")
    db_session.commit()
    assert run.role == "writer"
    assert run.fallback_used is True
    assert run.model == "m/fb"


def test_similarity_not_evaluated_without_source_text(db_session):
    import asyncio

    from app.agents.story import StoryPipeline
    from app.db.models import Source

    pipe = StoryPipeline()
    case = _mk_case(db_session)
    db_session.add(
        Source(
            case_id=case.id, title="S", url="https://x.com",
            source_type="article", research_provider="openrouter",
            summary="Only a summary, no raw text.",
        )
    )
    db_session.commit()
    score, status = pipe._score_similarity(
        db_session, case, "story text", case.sources
    )
    assert score is None and status == "not_evaluated"


# ---------------------------------------------------------------------------
# No hardcoded models / params in agent code
# ---------------------------------------------------------------------------


def test_no_hardcoded_model_ids_in_agents():
    agents_dir = Path(__file__).resolve().parents[1] / "app" / "agents"
    model_ids = list(ai_config.generation_provider().models.values())
    for path in agents_dir.glob("*.py"):
        code = path.read_text()
        for mid in model_ids:
            assert mid not in code, f"{path.name} hardcodes {mid}"
        assert "temperature=" not in code, f"{path.name} hardcodes temperature"
        assert "max_tokens" not in code, f"{path.name} hardcodes max_tokens"


def test_no_hardcoded_model_ids_anywhere_in_app():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    model_ids = list(ai_config.generation_provider().models.values())
    for path in app_dir.rglob("*.py"):
        code = path.read_text()
        for mid in model_ids:
            assert mid not in code, f"{path.relative_to(app_dir)} hardcodes {mid}"


# ---------------------------------------------------------------------------
# Endpoints / secrets
# ---------------------------------------------------------------------------


def test_search_engine_status_shape_and_no_key(client):
    """Search-engine status exposes backend/fetcher/embedding/llm —
    no OpenRouter fields, no keys."""
    r = client.get("/api/integrations/research/status")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "truecrime"
    assert body["engine"] == "truecrime_search_engine"
    assert "search_backend" in body
    assert "embedding" in body
    assert "llm" in body
    assert "sk-or" not in json.dumps(body)
    assert "apk" not in json.dumps(body)


def test_apimaster_status_shape(client):
    r = client.get("/api/integrations/apimaster/status")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "apimaster"
    assert set(body["models"]) == {
        "cheap", "research_intelligence", "writer", "premium", "embedding"}
    assert body["routing"]["writer"] == "writer"
    assert body["routing"]["engagement_critic"] == "premium"
    assert "apk" not in json.dumps(body)


def test_search_engine_alias_status(client):
    r = client.get("/api/integrations/search-engine/status")
    assert r.status_code == 200
    assert r.json()["provider"] == "truecrime"


def test_settings_status_exposes_models_not_keys(client):
    r = client.get("/api/settings/status")
    body = r.json()
    gen = body["generation"]
    assert gen["provider"] == "apimaster"
    assert set(gen["models"]) == {
        "cheap", "research_intelligence", "writer", "premium", "embedding"}
    assert gen["routing"]["final_editor"] == "premium"
    res = body["research"]
    assert res["provider"] == "truecrime"
    assert "sk-" not in json.dumps(body)
    assert "apk" not in json.dumps(body)


def test_story_meta_serializes_new_fields(client):
    from app.db.base import SessionLocal
    from app.db.models import Case, StoryVersion
    from app.utils import slugify

    db = SessionLocal()
    try:
        c = Case(canonical_title="Serialize Case", slug=slugify("Serialize Case"))
        db.add(c)
        db.commit()
        sv = StoryVersion(
            case_id=c.id, version=1, narrative_angle="p", story_text="one two",
            status="needs_revision", is_best=True, similarity_status="not_evaluated",
            similarity_score=None,
        )
        db.add(sv)
        db.commit()
        rows = client.get(f"/api/cases/{c.id}/stories").json()
        assert rows[0]["status"] == "needs_revision"
        assert rows[0]["is_best"] is True
        assert rows[0]["similarity_status"] == "not_evaluated"
        assert rows[0]["similarity_score"] is None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Final Product Quality Pass — hash-matched critique, grounding, sections
# ---------------------------------------------------------------------------


def _big_words(n: int) -> str:
    return " ".join(["word"] * n)


def test_strip_narration_artifacts():
    from app.utils import strip_narration_artifacts

    raw = (
        "### پرده اول: عنوان\n\n"
        "پاراگراف اول متن روایی است.\n\n"
        "---\n\n"
        "پرده دوم\n\n"
        "پاراگراف دوم با جزییات بیشتر.\n"
    )
    out = strip_narration_artifacts(raw)
    assert "###" not in out and "---" not in out
    assert "پاراگراف اول" in out and "پاراگراف دوم" in out
    # A narration line merely starting with the word survives.
    keep = strip_narration_artifacts("پرده‌برداری شدید بر حقیقت تأثیر گذاشت.")
    assert "پرده‌برداری" in keep


def test_parse_sections_markers():
    from app.agents.story import _parse_sections, _join_sections

    raw = "[[ACT:act1]]\n\nFirst act text.\n\n[[ACT:act2]]\n\nSecond act text."
    sections = _parse_sections(raw)
    assert [s["id"] for s in sections] == ["act1", "act2"]
    joined = _join_sections(sections)
    assert "[[ACT" not in joined
    assert "First act" in joined and "Second act" in joined


def test_grounding_gate_failures():
    from app.agents.story import StoryPipeline

    pipe = StoryPipeline()
    target = ai_config.story.default_target_minutes
    story = _big_words(int(target * ai_config.story.words_per_minute))

    bad = {
        "grounding_score": 0.5,
        "unsupported_claims": [{"claim": "x"}] * 5,
        "uncertainty_errors": [],
    }
    g = pipe._evaluate_gates(story, "en", target, grounding=bad, engagement=95)
    assert "grounding" in g["failures"]

    certain = {
        "grounding_score": 0.99,
        "unsupported_claims": [],
        "uncertainty_errors": [{"claim": "disputed told as fact", "evidence_id": "F001"}],
    }
    g = pipe._evaluate_gates(story, "en", target, grounding=certain, engagement=95)
    assert "grounding" in g["failures"]

    ok = {"grounding_score": 0.95, "unsupported_claims": [], "uncertainty_errors": []}
    g = pipe._evaluate_gates(story, "en", target, grounding=ok, engagement=95)
    assert g["pass"]


def test_consistency_gate_fails_on_high_severity():
    from app.agents.story import StoryPipeline

    pipe = StoryPipeline()
    target = ai_config.story.default_target_minutes
    story = _big_words(int(target * ai_config.story.words_per_minute))

    viol = {"violations": [{"severity": "high", "detail": "lamp extinguished forever but relit 1901"}]}
    g = pipe._evaluate_gates(story, "en", target, consistency=viol, engagement=95)
    assert "consistency" in g["failures"]

    low = {"violations": [{"severity": "low", "detail": "minor"}]}
    g = pipe._evaluate_gates(story, "en", target, consistency=low, engagement=95)
    assert "consistency" not in g["failures"]


def test_usage_metadata_recorded_and_nullable(db_session):
    from app.services.tracking import record_run, stamp_run

    run = record_run(db_session, None, "T", "completed")
    res = GenerationResult(
        text="x", model="m/t", provider="openrouter",
        input_tokens=10, output_tokens=20, total_tokens=30,
        cost_usd=0.001, generation_id="gen-1",
    )
    stamp_run(run, res, "writer")
    db_session.commit()
    assert run.input_tokens == 10 and run.total_tokens == 30
    assert run.estimated_cost_usd == 0.001 and run.generation_id == "gen-1"

    run2 = record_run(db_session, None, "T", "completed")
    stamp_run(run2, GenerationResult(text="x", model="m/t", provider="openrouter"), "writer")
    db_session.commit()
    assert run2.total_tokens is None and run2.estimated_cost_usd is None


class _FakeGen:
    """Scripted generation provider — no HTTP, deterministic outputs."""

    def __init__(self, critic_score=90):
        self.calls = []
        self.critic_score = critic_score

    def is_configured(self):
        return True

    async def generate_text(self, role, system, user):
        self.calls.append(("text", role))
        if role == "writer":
            body = _big_words(650) + "\n\n"
            text = f"[[ACT:act1]]\n\n{body}[[ACT:act2]]\n\n{body}"
            return GenerationResult(text=text, model="m/w", provider="fake")
        return GenerationResult(text=_big_words(1300), model="m/rw", provider="fake")

    async def generate_structured(self, role, system, user):
        self.calls.append(("json", role))
        table = {
            "story_director": {
                "title": "t", "central_question": "q",
                "acts": [{"id": "act1", "title": "a"}, {"id": "act2", "title": "b"}],
            },
            "engagement_critic": {
                "score": self.critic_score, "dimensions": {},
                "problems": [], "rewrite_instructions": [],
            },
            "section_critic": {"score": 90, "problems": []},
            "grounding_validator": {
                "supported_claims": [], "unsupported_claims": [],
                "uncertainty_errors": [], "grounding_score": 0.95,
            },
            "consistency_checker": {"violations": []},
        }
        return table[role], GenerationResult(text="{}", model="m/j", provider="fake")


def test_pipeline_final_critic_scores_stored_text(db_session, monkeypatch):
    """The engagement score stored must be generated for the exact text saved."""
    import app.agents.story as story_mod
    from app.db.models import Fact
    from app.utils import text_hash

    fake = _FakeGen(critic_score=95)
    monkeypatch.setattr(story_mod, "get_generation_provider", lambda: fake)

    case = _mk_case(db_session)
    db_session.add(Fact(case_id=case.id, claim="F", category="context", confidence=0.9))
    db_session.commit()

    pipe = story_mod.StoryPipeline()
    v = asyncio.run(pipe.run(db_session, case, target_minutes=10, language="en", tone="t", iterations=1))

    assert v.text_hash == text_hash(v.story_text)
    assert "[[ACT" not in v.story_text
    assert v.narrative_structure  # structure stored separately
    struct = json.loads(v.narrative_structure)
    assert struct["acts"] or struct["sections"]

    # The critic run that produced the stored score must carry the same hash.
    from app.db.models import AgentRun
    critic_runs = [
        r for r in db_session.query(AgentRun).filter(AgentRun.case_id == case.id)
        if r.role == "engagement_critic"
    ]
    assert critic_runs and critic_runs[-1].text_hash == v.text_hash


def test_pipeline_stale_critique_detectable(db_session, monkeypatch):
    """If text changes after critique, the hashes provably differ."""
    import app.agents.story as story_mod
    from app.db.models import Fact, AgentRun
    from app.utils import text_hash

    class DriftyFake(_FakeGen):
        async def generate_text(self, role, system, user):
            if role == "final_editor":
                return GenerationResult(
                    text=_big_words(900) + " changed tail", model="m/e", provider="fake"
                )
            return await super().generate_text(role, system, user)

    fake = DriftyFake(critic_score=95)
    monkeypatch.setattr(story_mod, "get_generation_provider", lambda: fake)
    case = _mk_case(db_session)
    db_session.add(Fact(case_id=case.id, claim="F", category="context", confidence=0.9))
    db_session.commit()
    v = asyncio.run(
        story_mod.StoryPipeline().run(
            db_session, case, target_minutes=10, language="en", tone="t", iterations=1
        )
    )
    final_critic = [
        r for r in db_session.query(AgentRun).filter(AgentRun.case_id == case.id)
        if r.role == "engagement_critic"
    ][-1]
    # Final critic ran on the post-edit text — hash matches the stored version.
    assert final_critic.text_hash == v.text_hash == text_hash(v.story_text)


def test_evidence_pack_ids_and_uncertainty(db_session):
    from app.agents.story import build_evidence_pack
    from app.db.models import Fact, Contradiction, Source

    case = _mk_case(db_session)
    db_session.add(Fact(case_id=case.id, claim="low conf", category="x", confidence=0.5))
    db_session.add(Fact(case_id=case.id, claim="solid", category="x", confidence=0.95, event_date="1900-12-15"))
    db_session.add(Contradiction(case_id=case.id, topic="t", description="A vs B", severity="high"))
    db_session.add(Source(case_id=case.id, title="S", url="u", summary="sum", source_type="a"))
    db_session.commit()

    pack = build_evidence_pack(case.facts, case.contradictions, case.sources)
    ids = [f["id"] for f in pack["facts"]]
    assert ids == ["F001", "F002"]
    assert pack["facts"][0]["uncertain"] is True  # low confidence
    assert pack["facts"][1]["uncertain"] is False
    assert pack["timeline"][0]["id"] == "T001"
    assert pack["contradictions"][0]["id"] == "C001"
    assert pack["approved_context"][0]["id"] == "CTX001"
    assert [f["id"] for f in pack["disputed_facts"]] == ["F001"]


def test_section_rewrite_preserves_other_sections(db_session, monkeypatch):
    import app.agents.story as story_mod

    async def weak_scores(self, title, text, position):
        score = 20 if "weak" in title else 90
        return {"score": score, "problems": ["flat"]}, GenerationResult(
            text="{}", model="m/c", provider="fake"
        )

    monkeypatch.setattr(
        story_mod.EngagementCritic, "critique_section", weak_scores
    )
    class SizedFake(_FakeGen):
        async def generate_text(self, role, system, user):
            if role == "writer":
                return await super().generate_text(role, system, user)
            return GenerationResult(
                text="rewritten weak section", model="m/rw",
                provider="fake",
            )

    fake = SizedFake()
    monkeypatch.setattr(story_mod, "get_generation_provider", lambda: fake)
    sections = [
        {"id": "good", "text": "original good section"},
        {"id": "weak", "text": "original weak section"},
    ]
    pipe = story_mod.StoryPipeline()
    case = _mk_case(db_session)
    out, scores = asyncio.run(
        pipe._section_pass(db_session, case, sections, {"facts": []}, {}, "en")
    )
    assert out[0]["text"] == "original good section"
    assert out[1]["text"] != "original weak section"  # rewritten


def test_story_quality_config_drives_gates(monkeypatch):
    from app.agents.story import StoryPipeline

    pipe = StoryPipeline()
    target = ai_config.story.default_target_minutes
    story = _big_words(int(target * ai_config.story.words_per_minute))
    marginal = {
        "grounding_score": 0.5, "unsupported_claims": [],
        "uncertainty_errors": [],
    }
    g1 = pipe._evaluate_gates(story, "en", target, grounding=marginal, engagement=95)
    assert "grounding" in g1["failures"]
    monkeypatch.setattr(ai_config.story_quality, "minimum_grounding_score", 0.4)
    g2 = pipe._evaluate_gates(story, "en", target, grounding=marginal, engagement=95)
    assert "grounding" not in g2["failures"]
