"""T1/T12/T15/T16 at the provider: the reasoning effort is sent and proven by the response,
a shared prefix is cached the way each model declares, and cached/reasoning tokens are
captured and priced."""

import asyncio
import functools
import json

import httpx
import pytest

from app.core.ai_config import ai_config
from app.providers.generation import openai_compat
from app.providers.generation.apimaster import APIMasterGenerationProvider
from app.providers.generation.base import GenerationError
from app.services.cost_model import model_cost_eur


def _provider(monkeypatch, handler):
    monkeypatch.setattr(openai_compat.httpx, "AsyncClient", functools.partial(
        httpx.AsyncClient, transport=httpx.MockTransport(handler)))
    provider = object.__new__(APIMasterGenerationProvider)
    provider.base_url = ai_config.generation_provider().base_url.rstrip("/")
    provider.api_key = "test-key"
    return provider


def _reply(usage):
    """What the gateway sends for a streaming model: SSE chunks, usage in the last one."""
    chunks = [
        {"id": "resp_1", "model": "gpt-6.1-sol", "choices": [{"delta": {"content": "o"}, "finish_reason": None}]},
        {"id": "resp_1", "model": "gpt-6.1-sol", "choices": [{"delta": {"content": "k"}, "finish_reason": "stop"}]},
        {"id": "resp_1", "model": "gpt-6.1-sol", "choices": [], "usage": usage},
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def test_reasoning_effort_is_sent_and_proven_by_the_response(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return _reply({"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140,
                       "prompt_tokens_details": {"cached_tokens": 64},
                       "completion_tokens_details": {"reasoning_tokens": 30}})

    result = asyncio.run(_provider(monkeypatch, handler).generate_text(
        "language_inspector", "sys", "user"))
    assert bodies[0]["model"] == "gpt-6.1-sol" and bodies[0]["reasoning_effort"] == "high"
    assert (result.reasoning_effort, result.reasoning_tokens, result.cached_tokens) == ("high", 30, 64)


def test_each_stage_role_sends_its_own_configured_effort(monkeypatch):
    sent = {}

    def handler(request):
        body = json.loads(request.content)
        sent[body["messages"][-1]["content"]] = body.get("reasoning_effort")
        return _reply({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    provider = _provider(monkeypatch, handler)
    original = ai_config.generation["language_rewrite_writer"]
    monkeypatch.setitem(ai_config.generation, "language_rewrite_writer",
                        original.model_copy(update={"reasoning_effort": "low"}))
    for role in ("language_rewrite_writer", "language_inspector", "language_final_writer"):
        asyncio.run(provider.generate_text(role, "sys", role))
    assert sent == {"language_rewrite_writer": "low", "language_inspector": "high",
                    "language_final_writer": "high"}


def test_a_model_without_the_capability_gets_no_effort(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "x", "model": "gemini-3.8-flash",
                                         "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    provider = _provider(monkeypatch, handler)
    original = ai_config.generation["grounding_validator"]
    monkeypatch.setitem(ai_config.generation, "grounding_validator",
                        original.model_copy(update={"reasoning_effort": "high"}))
    asyncio.run(provider.generate_text("grounding_validator", "s", "u"))
    assert "reasoning_effort" not in bodies[0]


def test_a_sonnet_role_with_a_low_effort_asks_for_low_thinking_so_the_token_budget_goes_to_the_text(monkeypatch):
    """The English draft used to be this case (Sonnet always thinks; low keeps it from eating the token
    budget). The draft now runs on Gemini Flash, which has no effort setting; the adapter behavior for a
    Sonnet role is checked on a role that is still routed to Sonnet."""
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return _reply({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    original = ai_config.generation["narrative_director"]
    monkeypatch.setitem(ai_config.generation, "narrative_director", original.model_copy(update={"reasoning_effort": "low"}))
    asyncio.run(_provider(monkeypatch, handler).generate_text("narrative_director", "s", "u"))
    assert bodies[0]["model"] == "claude-sonnet-5-5" and bodies[0]["reasoning_effort"] == "low"


def test_sonnet_gets_the_cache_marker_on_the_shared_prefix_only(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return _reply({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    asyncio.run(_provider(monkeypatch, handler).generate_text(
        "narrative_director", "rules", "task", prefix="SHARED", cache_key="k"))
    system = bodies[0]["messages"][0]["content"]
    assert system[0] == {"type": "text", "text": "SHARED", "cache_control": {"type": "ephemeral"}}
    assert system[1] == {"type": "text", "text": "rules"}
    assert "prompt_cache_key" not in bodies[0]


def test_sol_gets_the_prefix_first_and_the_cache_key(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return _reply({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    asyncio.run(_provider(monkeypatch, handler).generate_text(
        "language_rewrite_writer", "rules", "task", prefix="SHARED", cache_key="case-8:rewrite"))
    assert bodies[0]["messages"][0]["content"] == "SHARED\n\nrules"
    assert bodies[0]["messages"][1] == {"role": "user", "content": "task"}
    assert bodies[0]["prompt_cache_key"] == "case-8:rewrite"


def test_no_prefix_keeps_the_plain_request(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return _reply({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    asyncio.run(_provider(monkeypatch, handler).generate_text("language_rewrite_writer", "rules", "task"))
    assert bodies[0]["messages"][0] == {"role": "system", "content": "rules"}
    assert "prompt_cache_key" not in bodies[0]


def test_sol_cost_charges_cached_input_at_the_cached_rate():
    plain = model_cost_eur("gpt-6.1-sol", 1_000_000, 100_000)
    cached = model_cost_eur("gpt-6.1-sol", 1_000_000, 100_000, cached_tokens=1_000_000)
    # 1M input at $2 + 100k output at $10 = $3.00; fully cached input is $0.10 + $1.00
    assert plain == pytest.approx(3.0 * 0.8938, abs=1e-6)
    assert cached == pytest.approx(1.1 * 0.8938, abs=1e-6)


def test_a_streaming_model_is_asked_to_stream_with_usage_and_the_chunks_are_joined(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return _reply({"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9})

    result = asyncio.run(_provider(monkeypatch, handler).generate_text("language_rewrite_writer", "s", "u"))
    assert bodies[0]["stream"] is True and bodies[0]["stream_options"] == {"include_usage": True}
    assert (result.text, result.input_tokens, result.output_tokens) == ("ok", 7, 2)


def test_a_model_that_does_not_stream_keeps_the_plain_request(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "x", "model": "gemini-3.8-flash",
                                         "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    result = asyncio.run(_provider(monkeypatch, handler).generate_text("grounding_validator", "s", "u"))
    assert "stream" not in bodies[0] and result.text == "hi"


def _cut_reply():
    chunks = [{"id": "resp_1", "model": "gpt-6.1-sol", "choices": [{"delta": {"content": "half an ans"}, "finish_reason": None}]}]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def test_a_stream_cut_off_without_a_finish_reason_is_never_taken_for_the_whole_answer(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return _cut_reply()

    with pytest.raises(GenerationError) as error:
        asyncio.run(_provider(monkeypatch, handler).generate_text("language_rewrite_writer", "s", "u"))
    assert error.value.kind == "invalid_output" and "cut off" in str(error.value)
    assert len(calls) == 2          # one retry on the same model, as for any provider hiccup


def test_a_cut_stream_that_is_complete_on_the_retry_gives_the_answer(monkeypatch):
    replies = iter([_cut_reply(), _reply({"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9})])
    result = asyncio.run(_provider(monkeypatch, lambda request: next(replies)).generate_text(
        "language_rewrite_writer", "s", "u"))
    assert result.text == "ok"


# ----------------------------------------------------------------------------- cache and cost telemetry
def test_the_gateway_cache_creation_counters_and_request_id_are_captured_not_invented(monkeypatch):
    def handler(request):
        reply = _reply({"prompt_tokens": 9000, "completion_tokens": 5, "total_tokens": 9005,
                        "usage_source": "anthropic",
                        "prompt_tokens_details": {"cached_tokens": 100},
                        "claude_cache_creation_5_m_tokens": 8000, "claude_cache_creation_1_h_tokens": 50})
        return httpx.Response(200, content=reply.content, headers={
            "content-type": "text/event-stream", "x-oneapi-request-id": "req-123"})

    result = asyncio.run(_provider(monkeypatch, handler).generate_text(
        "narrative_director", "system text", "user", prefix="the stable evidence block"))
    assert result.cache_write_tokens == 8050 and result.cached_tokens == 100
    assert result.gateway_request_id == "req-123" and result.usage_source == "anthropic"
    import hashlib
    assert result.prefix_sha256 == hashlib.sha256(b"the stable evidence block").hexdigest()


def test_without_a_prefix_the_system_prompt_is_the_reusable_start_and_missing_values_stay_none(monkeypatch):
    def handler(request):
        return _reply({"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11})

    result = asyncio.run(_provider(monkeypatch, handler).generate_text("language_inspector", "sys prompt", "u"))
    import hashlib
    assert result.prefix_sha256 == hashlib.sha256(b"sys prompt").hexdigest()
    assert result.cache_write_tokens is None and result.gateway_request_id is None and result.usage_source is None


def test_the_openai_style_cache_write_detail_wins_over_the_claude_counters():
    from app.providers.generation.openai_compat import cache_write_tokens

    assert cache_write_tokens({"prompt_tokens_details": {"cache_write_tokens": 7},
                               "claude_cache_creation_5_m_tokens": 99}) == 7
    assert cache_write_tokens({"claude_cache_creation_5_m_tokens": 0, "claude_cache_creation_1_h_tokens": 0}) == 0
    assert cache_write_tokens({}) is None


def test_cache_writes_are_priced_at_the_write_rate_when_the_model_has_one_and_never_below_zero_input():
    plain = model_cost_eur("claude-sonnet-5-5", 100_000, 0, cached_tokens=0)
    written = model_cost_eur("claude-sonnet-5-5", 100_000, 0, cached_tokens=0, cache_write_tokens=100_000)
    assert written > plain                                                  # 2.5 USD per 1M against 2.0
    assert model_cost_eur("claude-sonnet-5-5", 1_000, 0, cached_tokens=900, cache_write_tokens=500) is not None


def test_the_ledger_row_records_the_prefix_hash_the_cache_write_and_where_the_cost_comes_from(db_session):
    import uuid

    from app.db.models import AgentRun, Case, ProviderCall
    from app.providers.generation.base import GenerationResult
    from app.services.tracking import _record_provider_call, stamp_run

    case = Case(canonical_title="Telemetry", slug=f"t-{uuid.uuid4().hex[:8]}", language="en", status="researched")
    db_session.add(case)
    db_session.commit()
    result = GenerationResult(text="x", model="claude-sonnet-5-5", provider="apimaster", input_tokens=1000,
                              output_tokens=10, total_tokens=1010, cached_tokens=900, cache_write_tokens=50,
                              prefix_sha256="a" * 64, gateway_request_id="gw-1")
    run = AgentRun(case_id=case.id, agent_name="t", status="completed", pipeline_stage="telemetry_test")
    stamp_run(run, result, "language_draft_writer")
    db_session.add(run)
    db_session.commit()
    _record_provider_call(db_session, run)
    row = db_session.query(ProviderCall).filter_by(case_id=case.id, host="budget-ledger").one()
    assert (row.tokens_cached, row.cache_write_tokens, row.prompt_prefix_sha256, row.request_id) == (900, 50, "a" * 64, "gw-1")
    assert row.cost_source == "computed_from_tokens" and row.cost_eur > 0


def test_the_draft_request_to_gemini_flash_carries_the_model_and_only_what_flash_supports(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "g1", "model": "gemini-3.8-flash", "choices": [{"message": {"content": "A scene."}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 3, "total_tokens": 53}},
            headers={"x-oneapi-request-id": "gw-flash"})

    result = asyncio.run(_provider(monkeypatch, handler).generate_text(
        "language_draft_writer", "system text", "user", prefix="the evidence block", cache_key="case8"))
    body = bodies[0]
    assert body["model"] == "gemini-3.8-flash" and result.model == "gemini-3.8-flash"
    assert body["max_tokens"] == 64000 and body["temperature"] == 0.7                       # the draft's settings stay
    assert "reasoning_effort" not in body and "prompt_cache_key" not in body and "stream" not in body
    system = body["messages"][0]
    assert system == {"role": "system", "content": "the evidence block\n\nsystem text"}      # no cache marker: Flash has none
    assert result.gateway_request_id == "gw-flash" and result.reasoning_effort is None
