"""Provider-boundary verification.

TrueCrime Search Engine = search/fetch/index (infrastructure, no LLM).
APIMaster               = ALL intelligence (generation/analysis/critique/
                          query-planning/reranking/embeddings).

These tests pin the boundary: role routing, registry selection,
fallbacks, telemetry stamping and cost split.
"""
import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from app.core.ai_config import (
    AIConfig,
    GENERATION_ROLES,
    REQUIRED_ROLES,
    RESEARCH_ROLES,
    ai_config,
)
from app.providers.generation import get_generation_provider
from app.providers.generation.apimaster import APIMasterGenerationProvider
from app.providers.generation.base import GenerationError, GenerationResult
from app.providers.generation.openrouter import OpenRouterGenerationProvider


def _raw_config() -> dict:
    from pathlib import Path

    return json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "ai_config.json").read_text()
    )


# ---------------------------------------------------------------------------
# 1-7 — Role routing
# ---------------------------------------------------------------------------


def test_research_provider_is_search_engine_not_llm():
    res = ai_config.research_provider()
    assert res.type == "search_engine"
    assert res.secret_env is None  # no LLM credentials for discovery


def test_generation_roles_route_to_apimaster():
    gen = ai_config.generation_provider()
    for role in GENERATION_ROLES:
        assert ai_config.provider_for_role(role) == "generation"
        model = ai_config.model_for(role)
        assert model in gen.models.values()
        assert "/" not in model  # bare APIMaster IDs


def test_every_required_role_is_routed():
    for role in REQUIRED_ROLES:
        assert ai_config.provider_for_role(role) in ("research", "generation")
        assert ai_config.model_for(role)


def test_specific_role_ownership():
    gen = ai_config.generation_provider()
    assert ai_config.model_for("story_director") == gen.models["writer"]
    assert ai_config.model_for("final_editor") == gen.models["premium"]
    assert ai_config.model_for("master_writer") == gen.models["writer"]
    assert ai_config.model_for("fact_extractor") == gen.models["cheap"]
    assert ai_config.model_for("evidence_verifier") == gen.models["cheap"]
    # Research intelligence roles route to research_intelligence —
    # through the GENERATION provider, never the search engine.
    assert ai_config.model_for("research_query_planner") == \
        gen.models["research_intelligence"]
    assert ai_config.model_for("result_reranker") == \
        gen.models["research_intelligence"]
    assert ai_config.model_for("case_discovery_agent") == \
        gen.models["research_intelligence"]
    assert ai_config.model_for("youtube_discovery_agent") == \
        gen.models["research_intelligence"]


def test_role_sets_are_disjoint_and_complete():
    assert RESEARCH_ROLES & GENERATION_ROLES == set()
    assert RESEARCH_ROLES | GENERATION_ROLES == REQUIRED_ROLES


# ---------------------------------------------------------------------------
# 8-9 — Registry isolation
# ---------------------------------------------------------------------------


def test_generation_provider_is_apimaster():
    provider = get_generation_provider()
    assert isinstance(provider, APIMasterGenerationProvider)


def test_openrouter_generation_still_registered_not_active():
    """Registered for provider='openrouter' historical rows / config
    re-selection — but never selected while generation=apimaster."""
    assert ai_config.providers.generation == "apimaster"
    p = OpenRouterGenerationProvider()
    assert p.name == "openrouter"


def test_research_roles_absent_from_generation_section():
    raw = _raw_config()
    gen_routing = raw["generation_providers"]["apimaster"]["routing"]
    assert not (set(gen_routing) & RESEARCH_ROLES)


def test_research_section_declares_engine_type():
    raw = _raw_config()
    res = raw["research_providers"]["truecrime"]
    assert res["type"] == "search_engine"
    assert "routing" not in res or not res["routing"]
    assert "models" not in res or not res["models"]


def test_generation_routing_unknown_role_fails():
    raw = _raw_config()
    raw["generation_providers"]["apimaster"]["routing"][
        "nonexistent_role"
    ] = "cheap"
    with pytest.raises(Exception):
        AIConfig.model_validate(raw)


def test_generation_routing_unknown_alias_fails():
    raw = _raw_config()
    raw["generation_providers"]["apimaster"]["routing"]["writer"] = \
        "missing_alias"
    with pytest.raises(Exception):
        AIConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# 10-13 — APIMaster provider behaviour (mocked HTTP)
# ---------------------------------------------------------------------------


def _provider() -> APIMasterGenerationProvider:
    p = APIMasterGenerationProvider()
    p.api_key = "apk-test-key"
    return p


def test_apimaster_health_ok():
    provider = _provider()

    async def fake_available():
        return {"gpt-5.6-luna", "gemini-3.8-flash", "claude-sonnet-5-5"}, set()

    with patch.object(provider, "_models_available", side_effect=fake_available):
        status = asyncio.run(provider.provider_status())
    assert status["provider"] == "apimaster"
    assert status["role"] == "generation"
    assert status["status"] == "ok"
    assert status["authorized"] is True
    assert status["models_missing"] == []


def test_apimaster_missing_key():
    provider = APIMasterGenerationProvider()
    provider.api_key = ""
    status = asyncio.run(provider.provider_status())
    assert status["status"] == "missing_key"
    assert status["configured"] is False


def test_apimaster_missing_model_flagged():
    provider = _provider()

    async def fake_available():
        return {"gpt-5.6-luna"}, {"gemini-3.8-flash", "claude-sonnet-5-5"}

    with patch.object(provider, "_models_available", side_effect=fake_available):
        status = asyncio.run(provider.provider_status())
    assert status["status"] == "models_missing"
    assert "gemini-3.8-flash" in status["models_missing"]


def test_apimaster_structured_output():
    provider = _provider()

    async def good(model, system, user, gen):
        return json.dumps({"claims": []}), model, {
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
        }

    with patch.object(provider, "_chat", side_effect=good):
        data, res = asyncio.run(
            provider.generate_structured("fact_extractor", "sys", "user")
        )
    assert data == {"claims": []}
    assert res.provider == "apimaster"
    assert res.total_tokens == 15


def test_apimaster_json_repair_then_parse():
    """The shared OpenAI-compat layer repairs malformed JSON via a
    follow-up call before giving up."""
    provider = _provider()
    calls = []

    async def flaky(model, system, user, gen):
        calls.append(model)
        if len(calls) == 1:
            return "not json", model, {}
        return '{"ok": true}', model, {}

    with patch.object(provider, "_chat", side_effect=flaky):
        data, res = asyncio.run(
            provider.generate_structured("fact_extractor", "sys", "user")
        )
    assert data == {"ok": True}
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 14-17 — Fallback is provider-scoped
# ---------------------------------------------------------------------------


def test_generation_fallback_within_apimaster_models():
    gen = ai_config.generation_provider()
    for role in GENERATION_ROLES:
        for fb in ai_config.fallback_models_for(role):
            assert fb in gen.models.values()
            assert "/" not in fb


def test_fallbacks_stay_within_generation_models():
    gen = ai_config.generation_provider()
    for role in GENERATION_ROLES:
        for fb in ai_config.fallback_models_for(role):
            assert fb in gen.models.values()


def test_all_role_models_are_declared_generation_models():
    """Every routed role resolves to a declared APIMaster alias —
    no model ID can silently appear from elsewhere."""
    gen = ai_config.generation_provider()
    for role in REQUIRED_ROLES:
        assert ai_config.model_for(role) in gen.models.values()


# ---------------------------------------------------------------------------
# 18-20 — Telemetry / historical compat
# ---------------------------------------------------------------------------


def test_agent_run_stamps_apimaster(db_session):
    from app.services.tracking import record_run, stamp_run

    run = record_run(db_session, None, "T", "completed")
    res = GenerationResult(
        text="x", model="gemini-3.8-flash", provider="apimaster",
        input_tokens=10, output_tokens=5, total_tokens=15,
        cost_usd=0.0002,
    )
    stamp_run(run, res, "writer")
    db_session.commit()
    assert run.provider == "apimaster"
    assert run.model == "gemini-3.8-flash"
    assert run.estimated_cost_usd == 0.0002


def test_historical_provider_rows_render(client, db_session):
    """provider='devin'/'openrouter' rows remain listable."""
    from app.services.tracking import record_run

    for prov in ("devin", "openrouter", "apimaster"):
        run = record_run(db_session, None, "T", "completed")
        run.provider = prov
        run.model = "legacy"
    db_session.commit()
    rows = client.get("/api/agent-runs").json()
    providers = {r["provider"] for r in rows}
    assert {"devin", "openrouter", "apimaster"} <= providers


def test_settings_shows_engine_and_llm(client):
    body = client.get("/api/settings/status").json()
    assert body["generation"]["provider"] == "apimaster"
    assert body["generation"]["base_url"].startswith("https://apimaster")
    assert body["research"]["provider"] == "truecrime"


# ---------------------------------------------------------------------------
# 21-22 — Env surface / cost split
# ---------------------------------------------------------------------------


def test_env_example_declares_only_current_keys():
    from pathlib import Path

    env_example = (
        Path(__file__).resolve().parents[1] / ".env.example"
    ).read_text()
    assert "TrueCrime_APIMASTER_API_KEY" in env_example
    assert "TRUECRIME_SEARXNG_URL" in env_example
    # No active OpenRouter/Devin credentials (Part 51-52).
    assert "TrueCrime_OPENROUTER_API_KEY" not in env_example
    assert "DEVIN_API_KEY" not in env_example


def test_cost_split_by_provider(db_session):
    from app.db.base import SessionLocal
    from app.main import _generation_usage
    from app.services.tracking import record_run
    from app.utils import slugify
    from app.db.models import Case
    import uuid

    title = f"CostSplit {uuid.uuid4().hex[:8]}"
    case = Case(canonical_title=title, slug=slugify(title), language="en")
    db_session.add(case)
    db_session.commit()

    for prov, cost in (("openrouter", 1.0), ("apimaster", 0.5), ("apimaster", 0.25)):
        run = record_run(db_session, case.id, "T", "completed")
        stamp = GenerationResult(
            text="x", model="m", provider=prov,
            input_tokens=1, output_tokens=1, total_tokens=2,
            cost_usd=cost,
        )
        from app.services.tracking import stamp_run
        stamp_run(run, stamp, "writer")
    db_session.commit()

    usage = _generation_usage(db_session, case.id)
    assert usage["cost_by_provider"]["openrouter"]["estimated_cost_usd"] == 1.0
    assert usage["cost_by_provider"]["apimaster"]["calls"] == 2
    assert usage["cost_by_provider"]["apimaster"]["estimated_cost_usd"] == 0.75


# ---------------------------------------------------------------------------
# 23-25 — Hardcoding / audit
# ---------------------------------------------------------------------------


def test_no_hardcoded_provider_names_in_agents():
    from pathlib import Path

    agents_dir = Path(__file__).resolve().parents[1] / "app" / "agents"
    for path in agents_dir.glob("*.py"):
        code = path.read_text()
        assert "apimaster" not in code, f"{path.name} names APIMaster"
        # 'openrouter' allowed only inside comments mentioning research —
        # agents must never instantiate or select providers anyway.
        assert "OpenRouterGenerationProvider" not in code
        assert "APIMasterGenerationProvider" not in code
        assert "get_research_provider" not in code


def test_no_model_ids_hardcoded_in_app():
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    model_ids = (
        list(ai_config.generation_provider().models.values())
        + list(ai_config.research_provider().models.values())
    )
    for path in app_dir.rglob("*.py"):
        code = path.read_text()
        for mid in model_ids:
            assert mid not in code, f"{path.relative_to(app_dir)} hardcodes {mid}"


def test_research_provider_is_engine_not_apimaster():
    """get_research_provider() returns the search engine — discovery is
    infrastructure, never an LLM vendor call."""
    from app.providers import get_research_provider
    from app.research_engine.provider import TrueCrimeSearchProvider

    assert isinstance(get_research_provider(), TrueCrimeSearchProvider)
    assert ai_config.providers.research == "truecrime"
    assert ai_config.providers.generation == "apimaster"
