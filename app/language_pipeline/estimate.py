"""The cost estimate printed before a run. Planning figures from config.language_pipeline.estimate
and the prices in config/parameters/pricing.json; the real spend is only ever the ledger."""

from __future__ import annotations

from app.core.ai_config import ai_config
from app.language_pipeline.lengths import draft_words, final_range
from app.language_pipeline.materials import SharedMaterial, dump
from app.language_pipeline.stages import LanguageStages
from app.services.cost_model import load_pricing

NOTE = "ESTIMATE from planning figures, not a price and not a spend. The actual cost is the ledger."


def _price(model: str, tokens_in: float, tokens_out: float, cached_in: float = 0.0) -> float:
    pricing = load_pricing()
    spec = next(s for s in pricing["models"].values() if s["provider_model"] == model)
    rate = float(pricing["exchange_rates"]["usd_to_eur"])
    cached_rate = spec.get("cached_input_usd_per_1m_tokens", spec["input_usd_per_1m_tokens"])
    return round(((tokens_in - cached_in) * spec["input_usd_per_1m_tokens"]
                  + cached_in * cached_rate
                  + tokens_out * spec["output_usd_per_1m_tokens"]) / 1_000_000 * rate, 4)


def _add_audit(add, material: SharedMaterial, per_token, text_tokens: float, stage: str = "audit") -> None:
    """The one-pass evidence audit: one call per group of acts, each carrying all the evidence
    (the same in every call, so calls 2..n read it from the cache) and its own acts."""
    cfg = ai_config.language_pipeline
    est, audit = cfg.estimate, cfg.evidence_audit
    calls = max(1, -(-len(material.acts) // audit.acts_per_call))
    evidence = per_token(dump(material.audit_evidence()))
    add(stage, None, "english_evidence_auditor", (evidence + text_tokens / calls) * calls,
        est.audit_output_tokens_per_call * calls,
        est.audit_output_tokens_per_call * est.reasoning_tokens_ratio["english_evidence_auditor"] * calls,
        calls=calls, cached=evidence * (calls - 1))


def estimate(material: SharedMaterial, languages: list[str], requested_minutes: float | None = None,
             existing: frozenset[str] = frozenset(), cache: dict | None = None) -> dict:
    """Planning figures for a run. A stage in `existing` ("draft", "audit", "draft_review") already has its
    row and is not planned: a resumed run does not call it again. `cache` is the measured cache
    share of the draft's input (calibration.measure_cache_share); the draft is priced at the overall
    measured share and, as a comparison, at the worst measured run. With no measurement the figure
    is the configured one and the estimate says so."""
    cfg = ai_config.language_pipeline
    est = cfg.estimate
    cache = cache or {}
    cache_share: float = cache["overall"] if cache.get("overall") is not None else est.cache_share_without_data
    worst_share: float = cache["worst_run"] if cache.get("worst_run") is not None else est.cache_share_without_data
    per_token = lambda text: len(text) / est.chars_per_token  # noqa: E731
    plan_missing = est.plan_tokens if not material.plan else 0
    draft_in = per_token(material.draft_block()) + plan_missing
    lines = []

    def add(stage, language, role, tin, tout, reasoning, calls=1, cached=0.0):
        model = ai_config.model_for(role)
        lines.append({"stage": stage, "language": language, "model": model, "calls": calls,
                      "tokens_in": round(tin), "tokens_out": round(tout),
                      "reasoning_tokens": round(reasoning),
                      "eur": _price(model, tin, tout + reasoning, cached)})

    # the draft: one call per key beat, and the calls failed or short scenes cost again; every call
    # after the first reads the evidence block cached
    scenes = max(sum(len(LanguageStages.scenes(a)) for a in material.acts), len(material.acts) or 1, 1)
    draft_calls = round(scenes * est.draft_attempt_factor)
    draft_out = draft_words() * est.tokens_per_word[cfg.draft_language]
    draft_in = draft_in + draft_out / 2 * scenes / 2     # the story so far grows with every scene
    conservative_extra = 0.0
    if "draft" not in existing:
        add("draft", None, "language_draft_writer", draft_in * draft_calls,
            draft_out * est.draft_attempt_factor, 0, calls=draft_calls,
            cached=draft_in * (draft_calls - 1) * cache_share)
        model = ai_config.model_for("language_draft_writer")
        worst_eur = _price(model, draft_in * draft_calls, draft_out * est.draft_attempt_factor,
                           draft_in * (draft_calls - 1) * worst_share)
        conservative_extra = worst_eur - lines[-1]["eur"]
    draft_tokens = draft_out
    if "audit" not in existing and cfg.evidence_audit.enabled:
        _add_audit(add, material, per_token, draft_tokens)
    if "draft_review" not in existing and cfg.draft_review.enabled:
        # the English inspector reads the plan and the whole draft once
        add("draft_review", None, "language_inspector",
            per_token(dump(material.inspector_plan_view())) + draft_tokens, est.critique_tokens,
            est.critique_tokens * est.reasoning_tokens_ratio["language_inspector"])
    for language in languages:
        span = final_range(language, requested_minutes)
        share, ratio = est.words_share_of_max, est.reasoning_tokens_ratio
        # every language reads the master and the plan, no evidence pack, and is held to markers and
        # length only
        plan_in = per_token(dump(material.film_plan_view()))
        agents = cfg.agents_for(language)
        rewrite_role, final_role = ai_config.agents[agents.rewrite].role, ai_config.agents[agents.final].role
        rewrite_out = span.words_max * share[rewrite_role] * est.tokens_per_word[language]
        out = span.words_max * share[final_role] * est.tokens_per_word[language]
        add("rewrite", language, rewrite_role, plan_in + draft_tokens, rewrite_out,
            rewrite_out * ratio[rewrite_role])
        add("inspect", language, "language_inspector",
            per_token(dump(material.inspector_plan_view())) + rewrite_out,
            est.critique_tokens, est.critique_tokens * ratio["language_inspector"])
        # a language is written twice and inspected once: one final call
        add("final", language, final_role,
            plan_in + draft_tokens + rewrite_out + est.critique_tokens, out,
            out * ratio[final_role])
        # then the delivered text is reviewed by the inspector, and checked: English against the evidence
        # (the audit of the final, below), every other language for its meaning against the English text
        add("review", language, "language_inspector", per_token(dump(material.inspector_plan_view())) + out,
            est.critique_tokens, est.critique_tokens * ratio["language_inspector"])
        if cfg.language_stages[language].check == "fidelity":
            add("fidelity", language, "english_evidence_auditor", 2 * out + 2000, est.fidelity_output_tokens,
                est.fidelity_output_tokens * ratio["english_evidence_auditor"])
        elif cfg.evidence_audit.enabled:
            _add_audit(add, material, per_token, out, stage="audit_final")
            add("audit_fix", None, "english_evidence_auditor", est.audit_fix_input_tokens,
                est.audit_output_tokens_per_call, est.audit_output_tokens_per_call * ratio["english_evidence_auditor"])
    calls = sum(lg["calls"] for lg in lines)
    large = sum(lg["calls"] for lg in lines)
    total = round(sum(lg["eur"] for lg in lines), 2)
    return {"note": NOTE, "estimate": True, "assumptions": est.note, "large_calls": large,
            "calls": calls,
            "cache": {"source": "ledger" if cache.get("overall") is not None else "none measured: configured figure",
                      "runs_measured": cache.get("runs", 0), "share_used": round(cache_share, 3),
                      "worst_run_share": round(worst_share, 3)},
            "scenarios": {"measured_cache": total, "worst_measured_run": round(total + conservative_extra, 2)},
            "contingencies": ("not included: a revision round (one final call and its checks) and the bounded "
                              "length correction (one call), each at most once per language"),
            "lines": lines, "total_eur": total,
            "total_tokens_in": sum(lg["tokens_in"] for lg in lines),
            "total_tokens_out": sum(lg["tokens_out"] + lg["reasoning_tokens"] for lg in lines)}
