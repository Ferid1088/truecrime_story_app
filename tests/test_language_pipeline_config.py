"""T1/T2/T15: the four language-pipeline roles, the Sol alias with its reasoning effort,
and the narrow reviewer-on-author-model exception."""

import copy

import pytest

from app.core.ai_config import AIConfig, ai_config, load_raw_config

STAGE_ROLES = ("language_rewrite_writer", "language_inspector", "language_final_writer")


def test_the_four_roles_are_routed_as_decided():
    assert ai_config.alias_for("language_draft_writer") == "writer"          # Gemini Flash (owner decision 2026-10-18)
    for role in STAGE_ROLES:
        assert ai_config.alias_for(role) == "sol"
    assert ai_config.model_for("language_draft_writer") == "gemini-3.8-flash"
    assert ai_config.model_for("language_rewrite_writer") == "gpt-6.1-sol"


def test_every_stage_role_has_its_own_reasoning_effort_default_high():
    for role in STAGE_ROLES:
        assert ai_config.generation_for(role).reasoning_effort == "high", role
    # the draft's effort setting is kept as it was; Gemini Flash has no effort setting, so none is sent
    assert ai_config.generation_for("language_draft_writer").reasoning_effort == "low"
    assert ai_config.capabilities_of(ai_config.model_for("language_draft_writer")).reasoning_effort is False


def test_sol_declares_reasoning_and_prompt_caching_from_the_live_probe():
    caps = ai_config.capabilities_of("gpt-6.1-sol")
    assert caps.reasoning_effort and caps.prompt_caching == "prompt_cache_key"
    assert ai_config.capabilities_of("claude-sonnet-5-5").prompt_caching == "cache_control"


def test_each_role_is_one_agent_with_its_own_prompt():
    # the Persian/Arabic length correction is the same prompt under a role of its own (see
    # test_the_persian_and_arabic_writers_run_on_luna...), so it is not counted as another prompt
    agents = {n: d for n, d in ai_config.agents.items()
              if n.startswith("language.") and n not in ("language.glossary", "language.length_correction.fa_ar")}
    assert {d.role for d in agents.values()} == {
        "language_draft_writer", *STAGE_ROLES, "language_rewrite_writer_fa_ar", "language_final_writer_fa_ar",
        "english_evidence_auditor"}                                              # the fidelity check uses the audit role
    assert len({d.prompt for d in agents.values()}) == len(agents) == 15


def _raw_with_same_model_group(**group):
    raw = copy.deepcopy(load_raw_config())
    raw["review_independence"]["groups"] = [
        g for g in raw["review_independence"]["groups"] if g["name"] != "language_pipeline"]
    raw["review_independence"]["groups"].append({
        "name": "language_pipeline", "authors": ["language_rewrite_writer"],
        "reviewers": ["language_inspector"], **group})
    return raw


def test_same_model_exception_works_for_the_language_pipeline_group():
    raw = _raw_with_same_model_group(same_model_allowed=True, owner_decision="owner said so")
    AIConfig.model_validate(raw)


def test_same_model_without_the_flag_is_a_config_error():
    with pytest.raises(ValueError, match="also writes the narration"):
        AIConfig.model_validate(_raw_with_same_model_group())


def test_the_flag_needs_a_recorded_owner_decision():
    with pytest.raises(ValueError, match="owner_decision"):
        AIConfig.model_validate(_raw_with_same_model_group(same_model_allowed=True))


def test_the_exception_does_not_cover_another_group():
    raw = copy.deepcopy(load_raw_config())
    group = next(g for g in raw["review_independence"]["groups"]
                 if g["name"] == "documentary_host")
    raw["generation_providers"]["apimaster"]["routing"]["host_critic"] = \
        raw["generation_providers"]["apimaster"]["routing"]["host_writer"]
    assert group.get("same_model_allowed", False) is False
    with pytest.raises(ValueError, match="host_critic"):
        AIConfig.model_validate(raw)


def test_the_glossary_agent_is_a_cheap_validator_role_with_its_own_prompt():
    agent = ai_config.agents["language.glossary"]
    assert agent.role == "evidence_normalizer" and agent.prompt == "language_pipeline/glossary"
    assert ai_config.language_pipeline.cross_language.glossary.seed_from_evidence == ["en"]


def test_the_persian_and_arabic_writers_run_on_luna_and_every_other_language_agent_keeps_sol():
    """Owner decision 2026-10-18: gpt-5.6-luna writes the Persian and Arabic texts (rewrite, final, length
    correction); the inspectors, the meaning check, the audit and the English and German writers stay on sol."""
    sonnet_free = {"language.draft": "gemini-3.8-flash"}
    luna = ["language.length_correction.fa_ar", *(f"language.{stage}.{lg}" for stage in ("rewrite", "final")
                                                  for lg in ("fa", "ar"))]
    sol = ["english.evidence_audit", "english.audit_fix", "language.fidelity", "language.length_correction",
           *(f"language.inspect.{lg}" for lg in ("en", "fa", "de", "ar")),
           *(f"language.{stage}.{lg}" for stage in ("rewrite", "final") for lg in ("en", "de"))]
    expected = {**sonnet_free, **dict.fromkeys(sol, "gpt-6.1-sol"), **dict.fromkeys(luna, "gpt-5.6-luna")}
    for name, model in expected.items():
        assert ai_config.model_for(ai_config.agents[name].role) == model, name
    assert [n for n, d in ai_config.agents.items() if d.role == "language_draft_writer"] == ["language.draft"]
    assert ai_config.generation_for("language_draft_writer").max_tokens == 64000        # settings kept
    assert ai_config.generation_for("language_draft_writer").temperature == 0.7


def test_luna_is_declared_priced_and_has_no_fallback():
    from app.services.cost_model import load_pricing, model_cost_eur

    caps = ai_config.capabilities_of("gpt-5.6-luna")
    assert (caps.reasoning_effort, caps.streaming) == (True, True)
    for role in ("language_rewrite_writer_fa_ar", "language_final_writer_fa_ar"):
        assert ai_config.generation_for(role).reasoning_effort == "high"
        assert ai_config.fallback_models_for(role) == []             # a failure stays visible; no silent switch
    # priced from the gateway's list (0.2 USD in, 1.2 USD out, 0.02 USD cached per 1M tokens), not the
    # conservative stand-in for a model without a price
    rate = load_pricing()["exchange_rates"]["usd_to_eur"]
    assert model_cost_eur("gpt-5.6-luna", 1_000_000, 0) == round(0.2 * rate, 8)
    assert model_cost_eur("gpt-5.6-luna", 0, 1_000_000) == round(1.2 * rate, 8)
    assert model_cost_eur("gpt-5.6-luna", 1_000_000, 0, cached_tokens=1_000_000) == round(0.02 * rate, 8)



def test_the_safety_retry_of_the_review_uses_its_own_model_route_and_the_neutral_preamble(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.core.prompts import prompt
    from app.language_pipeline import stages as ST

    seen = {}

    async def fake_run_agent(name, gen, task, **kw):
        seen.update(name=name, **kw)
        return {}, SimpleNamespace()

    async def fake_native(self, db, case, material, text, language, run, agent, stage=None, safety_mode=False):
        await run()
        return "output"

    monkeypatch.setattr(ST, "run_agent", fake_run_agent)
    monkeypatch.setattr(ST.LanguageStages, "_native_inspect", fake_native)
    material = SimpleNamespace(inspector_plan_view=lambda: {}, cache_key=lambda: "k")
    span = SimpleNamespace(words_min=1, words_max=2)
    stages = ST.LanguageStages(gen=object())
    for safety in (False, True):
        seen.clear()
        asyncio.run(stages._inspect_en(None, None, material, "A text.", "en", span, "language_review", safety))
        assert seen["role"] == ("language_safety_inspector" if safety else None)
        assert seen["system"].startswith(prompt("language_pipeline/safety_preamble").strip()[:40]) is safety
