"""Owner decision 2026-10-18: the Persian and Arabic writers (rewrite, final, length correction) run on
gpt-5.6-luna; English and German keep their writers. The stage code takes the role from the language's
own agent, so each language reaches the model its agent names. The routing and the price are in
test_language_pipeline_config."""

import asyncio

import pytest
from test_language_pipeline_flow import FakeGen, _pipeline, setup  # noqa: F401  (the flow tests' fixture)

from app.core.ai_config import ai_config

WRITER_ROLES = {"en": {"language_rewrite_writer", "language_final_writer"},
                "de": {"language_rewrite_writer", "language_final_writer"},
                "fa": {"language_rewrite_writer_fa_ar", "language_final_writer_fa_ar"},
                "ar": {"language_rewrite_writer_fa_ar", "language_final_writer_fa_ar"}}


@pytest.fixture
def own_roles(setup, monkeypatch):  # noqa: F811 - the flow fixture gives the Persian and Arabic agents the shared
    for language in ("fa", "ar"):   # writer roles; this test wants the configured ones back
        monkeypatch.setattr(ai_config.agents[f"language.rewrite.{language}"], "role", "language_rewrite_writer_fa_ar")
        monkeypatch.setattr(ai_config.agents[f"language.final.{language}"], "role", "language_final_writer_fa_ar")
    return setup


def test_each_language_writer_is_called_with_the_role_of_its_agent(own_roles):
    gen = FakeGen()
    out = asyncio.run(_pipeline(gen).run_case(own_roles.id, ["en", "fa", "de", "ar"], 60))
    assert [lg["status"] for lg in out["languages"]] == ["done"] * 4
    writers = {role for language in WRITER_ROLES for role in WRITER_ROLES[language]}
    seen: dict[str, set[str]] = {}
    for call in gen.calls:
        if call["role"] in writers:
            seen.setdefault(call["user"]["language"], set()).add(call["role"])
    assert seen == WRITER_ROLES
    assert {ai_config.model_for(role) for role in WRITER_ROLES["fa"]} == {"gpt-5.6-luna"}
    assert {ai_config.model_for(role) for role in WRITER_ROLES["de"]} == {"gpt-6.1-sol"}
