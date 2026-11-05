"""The second gateway behind the generation provider: a call moves to it only when the active provider is not available (as configured), with
the same role, and stays there for the cooldown; a content refusal, an invalid request or invalid output never moves; the result says which
provider made it and keeps the usage a failed first attempt was billed. The key is read from the environment only."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core.ai_config import GenerationFailoverConfig, ai_config
from app.providers.generation.base import GenerationError, GenerationResult
from app.providers.generation.failover import FailoverGenerationProvider, failure_of

CFG = GenerationFailoverConfig(name="apiko", label="Apiko", base_url="https://example.invalid/v1", secret_env="X", cooldown_s=300,
                               on=["unreachable", "timeout", "provider_error", "model_unavailable", "rate_limited", "unauthorized", "quota"])


class Fake:
    def __init__(self, name: str, error: GenerationError | None = None, configured: bool = True):
        self.name, self.label, self.error, self.configured, self.calls = name, name, error, configured, []

    def is_configured(self) -> bool:
        return self.configured

    async def generate_text(self, role, system, user, images=None, *, prefix=None, cache_key=None):
        self.calls.append(role)
        if self.error:
            raise self.error
        return GenerationResult(text="ok", model="gpt-6.1-sol", provider=self.name, input_tokens=10, output_tokens=5, cost_usd=0.01)

    async def generate_structured(self, role, system, user, images=None, *, prefix=None, cache_key=None):
        return {"ok": True}, await self.generate_text(role, system, user, images, prefix=prefix, cache_key=cache_key)


@pytest.fixture(autouse=True)
def fresh_cooldown():
    FailoverGenerationProvider._until.clear()
    yield
    FailoverGenerationProvider._until.clear()


def error(kind: str, message: str = "x", **usage) -> GenerationError:
    e = GenerationError(kind, message)
    for k, v in usage.items():
        setattr(e, k, v)
    return e


def test_an_empty_balance_answered_with_a_403_moves_but_another_403_never_does():
    cfg = CFG.model_copy(update={"quota_messages": ["insufficient balance"]})
    empty = error("forbidden", "APIMaster permission/quota failure (403): Insufficient balance for this request. Remaining: $0.0001.")
    assert failure_of(empty, cfg) == "quota" and failure_of(error("forbidden", "x (403): content risk"), cfg) == "forbidden"
    primary, secondary = Fake("apimaster", empty), Fake("apiko")
    result = asyncio.run(FailoverGenerationProvider(primary, secondary, cfg).generate_text("reading_checker", "s", "u"))
    assert (result.provider, secondary.calls) == ("apiko", ["reading_checker"])


@pytest.mark.parametrize("failure", [error("unreachable"), error("timeout"), error("provider_error", "APIMaster error (HTTP 503)"),
                                     error("model_unavailable"), error("rate_limited"), error("unauthorized"),
                                     error("forbidden", "APIMaster permission/quota failure (402): no balance")])
def test_a_call_moves_to_the_second_gateway_when_the_active_provider_is_not_available(failure):
    primary, secondary = Fake("apimaster", failure), Fake("apiko")
    result = asyncio.run(FailoverGenerationProvider(primary, secondary, CFG).generate_text("reading_checker", "s", "u"))
    assert (result.provider, result.fallback_used, secondary.calls) == ("apiko", True, ["reading_checker"])


@pytest.mark.parametrize("failure", [error("forbidden", "APIMaster permission/quota failure (403): content risk"), error("invalid_request"),
                                     error("invalid_output")])
def test_a_refusal_an_invalid_request_or_invalid_output_never_moves(failure):
    primary, secondary = Fake("apimaster", failure), Fake("apiko")
    with pytest.raises(GenerationError):
        asyncio.run(FailoverGenerationProvider(primary, secondary, CFG).generate_text("r", "s", "u"))
    assert secondary.calls == []


def test_after_a_move_calls_go_to_the_second_gateway_for_the_cooldown_and_usage_of_the_failed_attempt_is_kept():
    primary, secondary = Fake("apimaster", error("timeout", input_tokens=7, output_tokens=0, cost_usd=0.002)), Fake("apiko")
    provider = FailoverGenerationProvider(primary, secondary, CFG)
    first = asyncio.run(provider.generate_text("r", "s", "u"))
    assert (first.input_tokens, first.cost_usd) == (17, pytest.approx(0.012))
    data, second = asyncio.run(provider.generate_structured("r", "s", "u"))
    assert data == {"ok": True} and second.provider == "apiko" and second.fallback_used and primary.calls == ["r"], "no wait on a dead primary"
    FailoverGenerationProvider._until.clear()
    primary.error = None
    assert asyncio.run(provider.generate_text("r", "s", "u")).provider == "apimaster", "after the cooldown the active provider is tried again"


def test_without_a_key_for_the_second_gateway_nothing_moves_and_the_quota_is_told_from_a_refusal():
    primary, secondary = Fake("apimaster", error("timeout")), Fake("apiko", configured=False)
    with pytest.raises(GenerationError):
        asyncio.run(FailoverGenerationProvider(primary, secondary, CFG).generate_text("r", "s", "u"))
    assert failure_of(error("forbidden", "x (402)"), CFG) == "quota" and failure_of(error("forbidden", "x (403)"), CFG) == "forbidden"


def test_a_model_the_second_gateway_names_differently_is_called_by_its_name_there(monkeypatch):
    from app.providers.generation.openai_compat import OpenAICompatibleGenerationProvider

    monkeypatch.delenv("TRUECRIME_TEST_NO_REAL_PROVIDERS", raising=False)
    renamed = CFG.model_copy(update={"models": {"gpt-6.1-sol": "gpt-6.1-sol-x"}, "quota_http_status": [402, 429]})
    assert OpenAICompatibleGenerationProvider(renamed).model_map == {"gpt-6.1-sol": "gpt-6.1-sol-x"}
    assert failure_of(error("forbidden", "x (429)"), renamed) == "quota", "the quota statuses come from the config"


def test_the_configured_gateway_lists_its_key_by_name_only_and_serves_the_models_of_the_active_provider():
    cfg = ai_config.generation_failover
    assert cfg is not None and cfg.secret_env == "TRUECRIME_APICO" and cfg.base_url == "https://api.apiko.app/v1"
    connections = (Path(__file__).resolve().parents[1] / "config" / "connections.json").read_text(encoding="utf-8")
    assert "sk-" not in connections and json.loads(connections)["generation_failover"]["name"] == "apiko"
