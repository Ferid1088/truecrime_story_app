"""Tests for central AI config + the APIMaster generation provider.

All APIMaster HTTP calls are mocked — no real credits are spent.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.ai_config import AIConfig, ai_config, REQUIRED_ROLES
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
    assert ai_config.generation_provider().secret_env == "TRUECRIME_APIMASTER_API_KEY"
    for role in REQUIRED_ROLES:
        assert ai_config.model_for(role)
        assert ai_config.generation_for(role).max_tokens > 0


def test_invalid_config_fails():
    raw = _raw_config()
    raw["generation_providers"]["apimaster"]["routing"]["fact_extractor"] = "nonexistent_alias"
    with pytest.raises(ValueError):
        AIConfig.model_validate(raw)

    raw = _raw_config()
    raw["generation_providers"]["apimaster"]["routing"]["fact_extractor"] = "unknown_role"
    del raw["generation_providers"]["apimaster"]["models"]["cheap"]
    with pytest.raises(ValueError):
        AIConfig.model_validate(raw)

    raw = _raw_config()
    raw["generation"]["fact_extractor"]["temperature"] = 9.9
    with pytest.raises(ValueError):
        AIConfig.model_validate(raw)

    raw = _raw_config()
    del raw["generation"]["grounding_validator"]
    with pytest.raises(ValueError):
        AIConfig.model_validate(raw)

    raw = _raw_config()
    raw["providers"]["generation"] = "something-else"
    with pytest.raises(ValueError):
        AIConfig.model_validate(raw)


def test_invalid_fallback_config_fails():
    raw = _raw_config()
    raw["generation_providers"]["apimaster"]["fallbacks"] = {"cheap": ["ghost"]}
    with pytest.raises(ValueError):
        AIConfig.model_validate(raw)

    raw = _raw_config()
    raw["generation_providers"]["apimaster"]["fallbacks"] = {"cheap": ["cheap"]}
    with pytest.raises(ValueError):
        AIConfig.model_validate(raw)


def test_invalid_story_config_fails():
    raw = _raw_config()
    raw["story"]["default_target_minutes"] = raw["story"]["max_target_minutes"] + 1
    with pytest.raises(ValueError):
        AIConfig.model_validate(raw)

    raw = _raw_config()
    raw["language_pipeline"]["long_draft_minutes"] = 40     # shorter than the final it feeds
    with pytest.raises(ValueError, match="long_draft_minutes"):
        AIConfig.model_validate(raw)


def test_missing_secret_env_fails():
    raw = _raw_config()
    del raw["generation_providers"]["apimaster"]["secret_env"]
    with pytest.raises(ValueError):
        AIConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Logical routing
# ---------------------------------------------------------------------------

EXPECTED_ROUTING = {
    "fact_extractor": "premium",
    "timeline_builder": "cheap",
    "contradiction_analyzer": "cheap",
    "grounding_validator": "cheap",
    "consistency_checker": "cheap",
    "story_director": "premium",
    "language_draft_writer": "writer",
    "language_rewrite_writer": "sol",
    "language_inspector": "sol",
    "language_final_writer": "sol",
}


def test_logical_model_routing():
    models = ai_config.generation_provider().models
    for role, alias in EXPECTED_ROUTING.items():
        assert ai_config.alias_for(role) == alias, role
        assert ai_config.model_for(role) == models[alias], role


def test_model_aliases_resolve():
    models = ai_config.generation_provider().models
    assert set(models) == {
        "cheap", "research_intelligence", "writer", "premium", "sol", "luna", "embedding"}
    for spec in models.values():
        assert "/" not in spec  # bare APIMaster model IDs


def test_fallback_chain_from_config():
    models = ai_config.generation_provider().models
    assert ai_config.fallback_models_for("fact_extractor") == [models["writer"]]
    assert ai_config.fallback_models_for("language_draft_writer") == [models["premium"]]   # Flash falls back to Sonnet
    # Reviewers never fall back to the writer model (review_independence):
    # a rate-limited critic must not become the author grading itself.
    assert models["premium"] not in ai_config.fallback_models_for("grounding_validator")
    assert ai_config.fallback_models_for("language_rewrite_writer") == []


def test_unknown_role_raises():
    with pytest.raises(KeyError):
        ai_config.model_for("nonexistent_role")
    with pytest.raises(KeyError):
        ai_config.generation_for("nonexistent_role")


# ---------------------------------------------------------------------------
# Provider behaviour (mocked HTTP)
# ---------------------------------------------------------------------------


def _provider() -> APIMasterGenerationProvider:
    p = object.__new__(APIMasterGenerationProvider)
    p.base_url = ai_config.generation_provider().base_url.rstrip("/")
    p.api_key = "apk-test-key"
    return p


def test_generate_text_uses_routed_model():
    provider = _provider()
    routed = ai_config.generation_provider().models["premium"]
    seen = {}

    async def fake_chat(model, system, user, gen):
        seen["model"] = model
        return "{}", model, {}

    with patch.object(provider, "_chat", side_effect=fake_chat):
        res = asyncio.run(provider.generate_text("fact_extractor", "sys", "user"))
    assert seen["model"] == routed
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
        res = asyncio.run(provider.generate_text("fact_extractor", "sys", "user"))
    assert calls == [models["premium"], models["writer"]]
    assert res.model == models["writer"]
    assert res.fallback_used is True


def test_discarded_billed_attempt_before_a_successful_fallback_is_not_lost():
    """A truncated (real, billed) first attempt must not vanish just
    because a later fallback candidate succeeds — its usage must be
    folded into the eventual GenerationResult."""
    provider = _provider()
    models = ai_config.generation_provider().models
    calls = []

    async def first_truncated_then_ok(model, system, user, gen):
        calls.append(model)
        if len(calls) == 1:
            raise GenerationError(
                "invalid_output", "truncated at max_tokens",
                provider="apimaster", model=model,
                input_tokens=200, output_tokens=100, total_tokens=300, cost_usd=0.01,
            )
        return "text", model, {"input_tokens": 50, "output_tokens": 20,
                               "total_tokens": 70, "cost_usd": 0.002}

    with patch.object(provider, "_chat", side_effect=first_truncated_then_ok):
        res = asyncio.run(provider.generate_text("fact_extractor", "sys", "user"))
    assert calls == [models["premium"], models["premium"]]  # same-model retry, not a fallback
    assert res.input_tokens == 250
    assert res.output_tokens == 120
    assert res.total_tokens == 370
    assert res.cost_usd == pytest.approx(0.012)


def test_discarded_billed_attempts_are_kept_even_if_every_candidate_fails():
    """Same shape, but nothing ever succeeds — the final raised error must
    carry the sum of every attempt's billed usage, with a provider/model
    identifying what was actually billed (not None from the last,
    unbilled attempt)."""
    provider = _provider()
    calls = []

    async def billed_then_unbilled_timeout(model, system, user, gen):
        calls.append(model)
        if len(calls) == 1:
            raise GenerationError(
                "invalid_output", "truncated at max_tokens",
                provider="apimaster", model=model,
                input_tokens=200, output_tokens=100, total_tokens=300, cost_usd=0.01,
            )
        raise GenerationError("timeout", "request timed out")

    with patch.object(provider, "_chat", side_effect=billed_then_unbilled_timeout):
        with pytest.raises(GenerationError) as exc:
            asyncio.run(provider.generate_text("fact_extractor", "sys", "user"))
    assert exc.value.provider == "apimaster"
    assert exc.value.input_tokens == 200
    assert exc.value.output_tokens == 100
    assert exc.value.cost_usd == pytest.approx(0.01)


def test_no_fallback_on_unauthorized():
    provider = _provider()

    async def denied(model, system, user, gen):
        raise GenerationError("unauthorized", "APIMaster API key rejected (401).")

    with patch.object(provider, "_chat", side_effect=denied):
        with pytest.raises(GenerationError) as exc:
            asyncio.run(provider.generate_text("fact_extractor", "sys", "user"))
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


# ---------------------------------------------------------------------------
# _chat: real HTTP shape (mocked transport) — cost-ledger-relevant behaviour
# ---------------------------------------------------------------------------


def _mock_client(handler):
    import httpx

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self._transport = httpx.MockTransport(handler)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, url, headers=None, json=None):
            request = httpx.Request("POST", url, headers=headers, json=json)
            response = self._transport.handle_request(request)
            response.request = request
            return response

    return _FakeAsyncClient


def test_chat_bills_against_the_requested_model_even_if_the_gateway_echoes_a_different_one(monkeypatch):
    import httpx as httpx_module

    provider = _provider()
    gen = ai_config.generation_for("fact_extractor")

    def handler(request):
        return httpx_module.Response(200, json={
            "id": "gen-1",
            "model": "claude-haiku-4-5-20251001",  # gateway's own, longer id
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    monkeypatch.setattr(
        "app.providers.generation.openai_compat.httpx.AsyncClient",
        _mock_client(handler),
    )
    with patch("app.providers.generation.openai_compat.log") as mock_log:
        content, used_model, meta = asyncio.run(
            provider._chat("claude-haiku-4-5", "sys", "user", gen)
        )
    assert used_model == "claude-haiku-4-5"
    assert mock_log.warning.called


def test_chat_truncated_completion_raises_with_billed_usage_attached(monkeypatch):
    import httpx as httpx_module

    provider = _provider()
    gen = ai_config.generation_for("fact_extractor")

    def handler(request):
        return httpx_module.Response(200, json={
            "id": "gen-2",
            "model": "claude-haiku-4-5",
            "choices": [{"message": {"content": "cut off"}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.02},
        })

    monkeypatch.setattr(
        "app.providers.generation.openai_compat.httpx.AsyncClient",
        _mock_client(handler),
    )
    with pytest.raises(GenerationError) as exc:
        asyncio.run(provider._chat("claude-haiku-4-5", "sys", "user", gen))
    assert exc.value.kind == "invalid_output"
    assert exc.value.provider == "apimaster"
    assert exc.value.model == "claude-haiku-4-5"
    assert exc.value.input_tokens == 100
    assert exc.value.output_tokens == 50
    assert exc.value.cost_usd == 0.02


def test_chat_empty_completion_raises_with_billed_usage_attached(monkeypatch):
    import httpx as httpx_module

    provider = _provider()
    gen = ai_config.generation_for("fact_extractor")

    def handler(request):
        return httpx_module.Response(200, json={
            "id": "gen-3",
            "model": "claude-haiku-4-5",
            "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 42, "completion_tokens": 0},
        })

    monkeypatch.setattr(
        "app.providers.generation.openai_compat.httpx.AsyncClient",
        _mock_client(handler),
    )
    with pytest.raises(GenerationError) as exc:
        asyncio.run(provider._chat("claude-haiku-4-5", "sys", "user", gen))
    assert exc.value.kind == "invalid_output"
    assert exc.value.input_tokens == 42


def test_timeout_raises_with_no_billed_usage():
    import httpx as httpx_module

    provider = _provider()
    gen = ai_config.generation_for("fact_extractor")

    class _TimeoutClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, *a, **k):
            raise httpx_module.TimeoutException("timed out")

    with patch("app.providers.generation.openai_compat.httpx.AsyncClient", _TimeoutClient):
        with pytest.raises(GenerationError) as exc:
            asyncio.run(provider._chat("claude-haiku-4-5", "sys", "user", gen))
    assert exc.value.kind == "timeout"
    assert exc.value.provider is None
    assert exc.value.model is None
    assert exc.value.input_tokens is None


def test_generate_structured_repair_exhausted_sums_both_calls_usage_into_the_error():
    provider = _provider()
    calls = []

    async def non_json_both_times(model, system, user, gen):
        calls.append(1)
        meta = {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140, "cost_usd": 0.01}
        return "not json" + str(len(calls)), model, meta

    with patch.object(provider, "_chat", side_effect=non_json_both_times):
        with pytest.raises(GenerationError) as exc:
            asyncio.run(provider.generate_structured("fact_extractor", "sys", "user"))
    assert len(calls) == 2  # first attempt + one repair attempt, no more
    assert exc.value.kind == "invalid_output"
    assert exc.value.input_tokens == 200  # both calls' tokens, not just the repair's
    assert exc.value.output_tokens == 80
    assert exc.value.cost_usd == pytest.approx(0.02)


def test_missing_key_raises():
    provider = _provider()
    provider.api_key = ""
    with pytest.raises(GenerationError) as exc:
        provider._headers()
    assert exc.value.kind == "missing_key"
    assert ai_config.generation_provider().secret_env in str(exc.value)


def test_provider_reads_key_via_config_secret_env(monkeypatch):
    monkeypatch.setenv(ai_config.generation_provider().secret_env, "apk-env-check")
    provider = _provider()
    provider.api_key = "apk-env-check"
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
    from app.agents.story import save_story_version
    from app.providers.generation.base import GenerationResult

    case = _mk_case(db_session)
    res = GenerationResult(text="x", model="m/test", provider="legacy-provider")

    v1 = save_story_version(
        db_session, case, "plan", [{"id": "full", "text": "story one"}],
        "story one", 80.0, None, "not_evaluated",
        {"quality_gates": {"pass": True}}, "en", res,
    )
    assert v1.status == "ready" and v1.is_best is True
    assert v1.generation_model == "m/test"
    assert v1.text_hash

    # Lower score must not dethrone v1.
    v2 = save_story_version(
        db_session, case, "plan", [{"id": "full", "text": "story two"}],
        "story two", 60.0, None, "not_evaluated",
        {"quality_gates": {"pass": False}}, "en", res,
    )
    assert v2.status == "needs_revision" and v2.is_best is False
    db_session.refresh(v1)
    assert v1.is_best is True

    # Higher score becomes the new best.
    v3 = save_story_version(
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
    res = GenerationResult(text="x", model="m/fb", provider="legacy-provider", fallback_used=True)
    stamp_run(run, res, "fact_extractor")
    db_session.commit()
    assert run.role == "fact_extractor"
    assert run.fallback_used is True
    assert run.model == "m/fb"


def test_stamp_run_tolerates_a_missing_generation_result(db_session):
    # Some callers' run_agent doubles short-circuit the documented
    # (data, GenerationResult) contract and pass None as the result (e.g.
    # tests/test_case_lifecycle.py's discovery fake) — stamp_run must not
    # crash on that, just leave provider/model unset.
    from app.services.tracking import record_run, stamp_run

    run = record_run(db_session, None, "T", "completed")
    stamp_run(run, None, "fact_extractor", text_hash="abc123")
    db_session.commit()
    assert run.provider is None
    assert run.model is None
    assert run.text_hash == "abc123"




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
    no vendor-specific fields, no keys."""
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
        "cheap", "research_intelligence", "writer", "premium", "sol", "luna", "embedding"}
    assert body["routing"]["story_director"] == "premium"
    assert body["routing"]["grounding_validator"] == "cheap"
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
        "cheap", "research_intelligence", "writer", "premium", "sol", "luna", "embedding"}
    assert gen["routing"]["language_final_writer"] == "sol"
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
    from app.agents.story import _join_sections, _parse_sections

    raw = "[[ACT:act1]]\n\nFirst act text.\n\n[[ACT:act2]]\n\nSecond act text."
    sections = _parse_sections(raw)
    assert [s["id"] for s in sections] == ["act1", "act2"]
    joined = _join_sections(sections)
    assert "[[ACT" not in joined
    assert "First act" in joined and "Second act" in joined






def test_usage_metadata_recorded_and_nullable(db_session):
    from app.services.tracking import record_run, stamp_run

    run = record_run(db_session, None, "T", "completed")
    res = GenerationResult(
        text="x", model="m/t", provider="legacy-provider",
        input_tokens=10, output_tokens=20, total_tokens=30,
        cost_usd=0.001, generation_id="gen-1",
    )
    stamp_run(run, res, "fact_extractor")
    db_session.commit()
    assert run.input_tokens == 10 and run.total_tokens == 30
    assert run.estimated_cost_usd == 0.001 and run.generation_id == "gen-1"

    run2 = record_run(db_session, None, "T", "completed")
    stamp_run(run2, GenerationResult(text="x", model="m/t", provider="legacy-provider"), "fact_extractor")
    db_session.commit()
    assert run2.total_tokens is None and run2.estimated_cost_usd is None








def test_evidence_pack_ids_and_uncertainty(db_session):
    from app.agents.story import build_evidence_pack
    from app.db.models import Contradiction, Fact, Source

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






def _sent_messages(monkeypatch, model, images):
    import httpx as httpx_module

    provider = _provider()
    gen = ai_config.generation_for("fact_extractor")
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx_module.Response(200, json={
            "id": "gen-1", "model": model,
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    monkeypatch.setattr("app.providers.generation.openai_compat.httpx.AsyncClient", _mock_client(handler))
    monkeypatch.setattr(ai_config.capabilities_of(model), "streaming", False)
    asyncio.run(provider._chat(model, "THE RULES", "the input", gen, images=images))
    return seen["body"]["messages"]


def test_a_request_with_images_repeats_the_instructions_in_the_user_message(monkeypatch):
    messages = _sent_messages(monkeypatch, "claude-sonnet-5-5", ["data:image/jpeg;base64,AAAA"])
    assert messages[0] == {"role": "system", "content": "THE RULES"}
    assert messages[1]["content"][0]["text"] == "THE RULES\n\nINPUT:\nthe input"


def test_a_request_without_images_sends_the_user_message_as_it_is(monkeypatch):
    messages = _sent_messages(monkeypatch, "claude-sonnet-5-5", None)
    assert messages[1]["content"] == "the input"
