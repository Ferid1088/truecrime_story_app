"""Config-backed, no-send production cost estimates.

The estimator deliberately exposes the units used for every line. It is used
by the dry run, the stage-control preview, and the estimate-vs-actual report
— information only. The only thing that can stop a run over cost is the
case's actual recorded spend against its EUR cap (app.services.budget).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, DocumentaryJob, ProviderCall

PRICING_PATH = Path(__file__).resolve().parents[2] / "config" / "parameters" / "pricing.json"
SCENARIOS = ("low", "expected", "high")


def configured_stage_time_budget_seconds(stage: str, target_minutes: float) -> float | None:
    """Return the target-scaled wall-clock budget for a stage."""
    base = stage.split(":", 1)[0]
    spec = ai_config.cost_control.stage_time_budgets.get(base)
    if spec is None:
        return None
    return float(spec.base_seconds + spec.seconds_per_target_minute * max(target_minutes, 0.0))


@dataclass(frozen=True)
class CostLine:
    stage: str
    status: str
    reason: str | None
    units: dict[str, float]
    unit_price_eur: dict[str, float]
    eur: dict[str, float]
    model: str | None = None
    price_status: str = "verified"
    price_warning: str | None = None

    @property
    def expected(self) -> float:
        return self.eur["expected"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "reason": self.reason,
            "units": {key: round(value, 2) for key, value in self.units.items()},
            "unit_price_eur": {key: round(value, 8) for key, value in self.unit_price_eur.items()},
            "eur": {key: round(value, 4) for key, value in self.eur.items()},
            "estimated_cost_eur": round(self.expected, 4),
            "model": self.model,
            "price_status": self.price_status,
            "price_warning": self.price_warning,
        }


def load_pricing(path: str | Path | None = None) -> dict[str, Any]:
    with Path(path or PRICING_PATH).open(encoding="utf-8") as handle:
        return json.load(handle)


def _round_range(values: dict[str, float]) -> dict[str, float]:
    return {key: round(float(values[key]), 6) for key in SCENARIOS}


def _model_price(pricing: dict[str, Any], model: str) -> tuple[float, float, str]:
    spec = pricing["models"][model]
    rate = float(pricing["exchange_rates"]["usd_to_eur"])
    return (
        float(spec["input_usd_per_1m_tokens"]) * rate,
        float(spec["output_usd_per_1m_tokens"]) * rate,
        str(spec.get("provider_model") or model),
    )


def _price_state(spec: dict[str, Any] | None, stage: str) -> tuple[str, str | None]:
    """Return the explicit verification state for a priced stage."""
    if not spec or spec.get("verification_status") != "verified":
        return "unverified", f"Price for {stage} is unverified; verify the configured provider/model before a paid run."
    return "verified", None


def _llm_line(pricing: dict[str, Any], stage: str, model: str,
              input_tokens: dict[str, float], output_tokens: dict[str, float]) -> CostLine:
    input_price, output_price, provider_model = _model_price(pricing, model)
    eur = _round_range({
        key: input_tokens[key] / 1_000_000 * input_price
        + output_tokens[key] / 1_000_000 * output_price
        for key in SCENARIOS
    })
    status, warning = _price_state(pricing["models"].get(model), stage)
    return CostLine(stage, "run", None,
                    {"input_tokens": input_tokens["expected"],
                     "output_tokens": output_tokens["expected"]},
                    {"input_eur_per_1m_tokens": input_price,
                     "output_eur_per_1m_tokens": output_price}, eur, provider_model,
                    status, warning)


def _single_unit_line(pricing: dict[str, Any], stage: str, status: str,
                      reason: str | None, units: dict[str, float], prices: dict[str, float],
                      eur: dict[str, float], model: str | None = None) -> CostLine:
    spec = None
    if stage.startswith("tts"):
        spec = pricing["tts"].get("eleven_v4")
    elif stage == "thumbnail":
        # Thumbnails are deterministic/local; they have no provider price.
        spec = None
    elif stage.startswith("host"):
        spec = pricing.get("heygen")
    price_status, warning = (("verified", None) if status == "skipped" and spec is None
                             else _price_state(spec, stage))
    return CostLine(stage, status, reason, units, prices, _round_range(eur), model,
                    price_status, warning)


def _words(text: str | None) -> int:
    return len((text or "").split())


def _source_words(source) -> int:
    """Count the collected page text, with summary/notes as the fallback.

    Research pages may be persisted as normalized SourceChunk rows rather than
    in Source.raw_text; using those chunks keeps the estimate tied to what was
    actually collected instead of silently applying the fixed assumption.
    """
    if source.raw_text:
        return _words(source.raw_text)
    chunks = sum(_words(chunk.text) for chunk in (source.chunks or []))
    return chunks or _words(source.summary or source.notes)


def _metrics(db: Session, case: Case, target_minutes: float | None,
             pricing: dict[str, Any], pilot_seconds: float | None = None) -> dict[str, Any]:
    assumptions = pricing["assumptions"]
    multipliers = assumptions["range_multipliers"]
    source_words = sum(_source_words(source) for source in case.sources)
    stories = [row for row in case.stories if row.story_text]
    story_words = max((_words(row.story_text) for row in stories), default=0)
    pilot_minutes = float(pilot_seconds) / 60 if pilot_seconds else None
    expected_minutes = float(pilot_minutes or target_minutes or
                             assumptions["target_minutes"]["expected"])
    source_fallback = int(assumptions["source_words_floor"])
    source_basis = "collected_source_pages" if source_words else "assumed"
    expected_source = source_words or source_fallback
    target_story_words = max(1, int(round(expected_minutes * assumptions["words_per_minute"])))
    # A pilot is a genuinely shorter editorial target. It must not inherit the
    # full-film floor merely because the case already has a long master.
    expected_story = (target_story_words if pilot_minutes is not None else
                      max(story_words, int(assumptions["story_words_floor"]),
                          target_story_words))
    result: dict[str, Any] = {
        "target_minutes": {key: expected_minutes for key in SCENARIOS},
        "source_pages": len(case.sources),
        "source_words_basis": source_basis,
        "source_words_assumed": not bool(source_words),
        "source_words_observed": source_words,
        "source_words": {key: expected_source * float(multipliers[key])
                         for key in SCENARIOS},
        "target_story_words": {key: target_story_words * float(multipliers[key])
                                for key in SCENARIOS},
        "story_words": {key: expected_story * float(multipliers[key])
                         for key in SCENARIOS},
    }
    words_to_tokens = float(assumptions["tokenization"]["words_to_tokens"])
    result["tokens"] = {
        "story_tokens": {key: result["story_words"][key] * words_to_tokens for key in SCENARIOS},
        "research_input": {key: result["source_words"][key] * words_to_tokens for key in SCENARIOS},
        "research_output": {key: result["target_story_words"][key] * 0.45 * words_to_tokens for key in SCENARIOS},
        "spoken_input": {key: result["story_words"][key] * 1.2 * words_to_tokens for key in SCENARIOS},
        "spoken_output": {key: result["story_words"][key] * 1.05 * words_to_tokens for key in SCENARIOS},
        "performance_input": {key: result["story_words"][key] * 1.1 * words_to_tokens for key in SCENARIOS},
        "performance_output": {key: result["story_words"][key] * 0.09 * words_to_tokens
                                for key in SCENARIOS},
        "short_selection_output": {
            key: result["story_words"][key] / max(float(assumptions["story_words_floor"]), 1)
            * 1200 for key in SCENARIOS
        },
    }
    return result


def _scale(metrics: dict[str, float], key: str, factor: float) -> dict[str, float]:
    return {scenario: metrics[scenario] * factor for scenario in SCENARIOS}




def _language_pipeline_lines(db: Session, case: Case, languages: list[str],
                             target_minutes: float | None, pilot_seconds: float | None,
                             multipliers: dict[str, float]) -> list[CostLine]:
    """The shared draft and each language's rewrite, inspection and final text, from the
    language pipeline's own planning estimate (a pilot writes no long text of its own here)."""
    from app.language_pipeline import estimate as pipeline_estimate
    from app.language_pipeline.materials import load_material
    from app.services.story_plan_service import StoryPlanService

    cfg = ai_config.language_pipeline
    plan = StoryPlanService(db).latest(case.id, cfg.draft_language)
    requested = None if pilot_seconds else target_minutes
    planned = pipeline_estimate.estimate(load_material(db, case, plan), languages, requested)
    lines = []
    for item in planned["lines"]:
        name = f"language_{item['stage']}" + (f":{item['language']}" if item["language"] else "")
        eur = {key: item["eur"] * float(multipliers[key]) for key in SCENARIOS}
        lines.append(CostLine(name, "run", None,
                              {"input_tokens": item["tokens_in"],
                               "output_tokens": item["tokens_out"] + item["reasoning_tokens"]},
                              {}, _round_range(eur), item["model"], "verified", None))
    return lines


def _visual_research_lines(pricing: dict[str, Any], film_seconds: float) -> list[CostLine]:
    """The picture research of one film: the searches (Brave image requests), the vision check of
    every candidate, and the second check of the places and objects the first left open. The counts
    come from the supply target of the film and the measured assumptions in pricing.json."""
    import math

    from app.documentary.visuals import supply

    a = pricing["assumptions"]["visual_research"]
    mult = pricing["assumptions"]["range_multipliers"]
    need = supply.target(film_seconds).total
    checks = math.ceil(need / float(a["pass_rate"]))
    second = math.ceil(checks * float(a["second_pass_share"]))
    requests = (int(a["initial_queries"]) + (ai_config.supply.max_searches - 1) * int(a["queries_per_round"]))
    price = pricing["search"]["brave_web"]
    search = {key: requests * float(price["eur_per_request"]) * float(mult[key]) for key in SCENARIOS}
    lines = [CostLine("visual_search", "run", None, {"requests": float(requests)},
                      {"eur_per_request": float(price["eur_per_request"])}, _round_range(search),
                      "Brave image search", "assumed",
                      "The image-search price is not published separately: the web-search price is assumed.")]

    def check_line(stage: str, model: str, n: int, tin: float, tout: float) -> CostLine:
        scaled_in = {key: n * tin * float(mult[key]) for key in SCENARIOS}
        scaled_out = {key: n * tout * float(mult[key]) for key in SCENARIOS}
        line = _llm_line(pricing, stage, model, scaled_in, scaled_out)
        return CostLine(line.stage, line.status, line.reason, {**line.units, "checks": float(n)},
                        line.unit_price_eur, line.eur, line.model, line.price_status, line.price_warning)

    lines.append(check_line("visual_check", "premium", checks, float(a["vision_input_tokens"]),
                            float(a["vision_output_tokens"])))
    lines.append(check_line("visual_second_pass", "sol", second, float(a["second_input_tokens"]),
                            float(a["second_output_tokens"])))
    return lines


def estimate_lines(db: Session, case: Case, languages: list[str] | None = None,
                   target_minutes: float | None = None,
                   pilot_seconds: float | None = None,
                   pricing: dict[str, Any] | None = None) -> tuple[list[CostLine], dict[str, dict[str, float]]]:
    pricing = pricing or load_pricing()
    languages = languages or ["en"]
    metrics = _metrics(db, case, target_minutes, pricing, pilot_seconds)
    story = metrics["story_words"]
    assumptions = pricing["assumptions"]
    chars = _scale(story, "story_words", float(assumptions["narration_characters_per_word"]))
    lines: list[CostLine] = []
    tokens = metrics["tokens"]
    lines.append(_llm_line(pricing, "research", "cheap",
                           tokens["research_input"], tokens["research_output"]))
    lines.extend(_language_pipeline_lines(db, case, languages, target_minutes, pilot_seconds,
                                          assumptions["range_multipliers"]))
    film_seconds = float(pilot_seconds) if pilot_seconds else 60.0 * float(
        target_minutes or assumptions["target_minutes"]["expected"])
    lines.extend(_visual_research_lines(pricing, film_seconds))
    for language in languages:
        lines.append(_llm_line(pricing, f"spoken:{language}", "premium",
                               tokens["spoken_input"], tokens["spoken_output"]))
        lines.append(_llm_line(pricing, f"performance:{language}", "premium",
                               tokens["performance_input"], tokens["performance_output"]))
    tts_price = float(pricing["tts"]["eleven_v4"]["eur_per_1m_characters"])
    if pilot_seconds:
        chars = {key: float(pilot_seconds) / 60 * float(assumptions["words_per_minute"])
                 * float(assumptions["narration_characters_per_word"])
                 * float(assumptions["range_multipliers"][key]) for key in SCENARIOS}
    else:
        chars = _scale(story, "story_words", float(assumptions["narration_characters_per_word"]))
    for language in languages:
        lines.append(_single_unit_line(
            pricing, f"tts:{language}", "run", None,
            {"characters": chars["expected"]}, {"eur_per_1m_characters": tts_price},
            {key: chars[key] / 1_000_000 * tts_price for key in SCENARIOS},
            pricing["tts"]["eleven_v4"].get("provider_model")))
    lines.append(_single_unit_line(
        pricing, "thumbnail", "skipped",
        "Thumbnail generation is local; no provider charge is included.",
        {"images": 0}, {}, {key: 0.0 for key in SCENARIOS}, "local thumbnail"))

    if ai_config.host.enabled and ai_config.avatar.enabled:
        host_price = float(pricing["heygen"]["eur_per_minute"])
        for language in languages:
            lines.append(_single_unit_line(
                pricing, f"host:{language}", "run", None,
                {"minutes": float(assumptions["host_minutes"]["expected"])},
                {"eur_per_minute": host_price},
                {key: float(assumptions["host_minutes"][key]) * host_price for key in SCENARIOS},
                pricing["heygen"].get("provider_model")))
    else:
        for language in languages:
            lines.append(_single_unit_line(pricing, f"host:{language}", "skipped",
                                           "Host/HeyGen is disabled in configuration.", {}, {},
                                           {key: 0.0 for key in SCENARIOS}))

    short_count = int(assumptions["short_count"])
    lines.append(_llm_line(pricing, "short_selection", "writer",
                           tokens["story_tokens"],
                           tokens["short_selection_output"]))
    for language in languages:
        lines.append(_single_unit_line(
            pricing, f"short_render:{language}", "skipped",
            "FFmpeg short render is local; no provider charge is included.",
            {"renders": short_count}, {}, {key: 0.0 for key in SCENARIOS}, "local FFmpeg"))
        lines.append(_single_unit_line(pricing, f"render:{language}", "skipped",
                                       "FFmpeg render is local and has no provider charge.",
                                       {}, {}, {key: 0.0 for key in SCENARIOS}))
    return lines, metrics


def documentary_estimate(db: Session, case: Case, languages: list[str] | None = None,
                         target_minutes: float | None = None,
                         pilot_seconds: float | None = None) -> dict[str, Any]:
    lines, metrics = estimate_lines(db, case, languages, target_minutes, pilot_seconds)
    totals = {key: round(sum(line.eur[key] for line in lines), 2) for key in SCENARIOS}
    return {"lines": [line.as_dict() for line in lines],
            "totals": totals, "metrics": metrics,
            "pilot_seconds": pilot_seconds,
            "price_warnings": [{"stage": line.stage, "reason": line.price_warning}
                               for line in lines if line.price_warning],
            "token_formulas": load_pricing()["assumptions"]["tokenization"],
            "pricing_last_verified": load_pricing()["last_verified"]}


def estimate_stage_eur(db: Session, case_id: int, stage: str,
                       pilot_seconds: float | None = None) -> float:
    case = db.get(Case, case_id)
    if case is None:
        return 0.0
    lines, _ = estimate_lines(db, case, ["en"], pilot_seconds=pilot_seconds)
    base = stage.split(":", 1)[0]
    aliases = {"voice": "tts:en", "short_render": "short_render:en"}
    lookup = aliases.get(base, stage)
    candidates = [line for line in lines if line.stage == lookup or line.stage == stage
                  or line.stage.split(":", 1)[0] == base]
    return round(sum(line.expected for line in candidates), 6)


_DATED_SNAPSHOT_SUFFIX = re.compile(r"-\d{8}$")


def _price_spec(pricing: dict[str, Any], model: str) -> dict[str, Any] | None:
    spec = next((row for row in pricing["models"].values()
                 if row.get("provider_model") == model), None)
    if spec:
        return spec
    # Some gateway responses echo a dated snapshot of the model we actually
    # requested (an -YYYYMMDD suffix on the configured model id) instead of
    # the resolved model string itself. openai_compat.py now bills against
    # the resolved model going forward (see its _chat), but this keeps
    # historical rows stored under the dated variant priceable too, on the
    # documented assumption that a dated snapshot suffix never changes the
    # provider's per-token rate.
    base = _DATED_SNAPSHOT_SUFFIX.sub("", model)
    if base == model:
        return None
    return next((row for row in pricing["models"].values()
                 if row.get("provider_model") == base), None)


def model_cost_eur(model: str | None, input_tokens: int | None,
                   output_tokens: int | None, cached_tokens: int | None = None,
                   cache_write_tokens: int | None = None) -> float | None:
    """Price provider-reported token usage using the configured model rate.

    `input_tokens` includes the cached ones (the provider's prompt_tokens);
    those are charged at the model's cached-input rate when it has one. Tokens the provider says it
    wrote to the cache are charged at the model's cache-write rate when it has one (the part of
    input_tokens that is neither read nor written is charged at the plain input rate)."""
    if not model or input_tokens is None or output_tokens is None:
        return None
    pricing = load_pricing()
    spec = _price_spec(pricing, model)
    if not spec:
        return None
    rate = float(pricing["exchange_rates"]["usd_to_eur"])
    input_rate = float(spec["input_usd_per_1m_tokens"])
    cached = min(int(cached_tokens or 0), int(input_tokens))
    cached_rate = float(spec.get("cached_input_usd_per_1m_tokens", input_rate))
    write_rate = float(spec.get("cache_write_usd_per_1m_tokens", input_rate))
    written = min(int(cache_write_tokens or 0), int(input_tokens) - cached)
    usd = ((int(input_tokens) - cached - written) / 1_000_000 * input_rate
           + written / 1_000_000 * write_rate
           + cached / 1_000_000 * cached_rate
           + int(output_tokens) / 1_000_000 * float(spec["output_usd_per_1m_tokens"]))
    return round(usd * rate, 8)


def conservative_unpriced_cost_eur(input_tokens: int, output_tokens: int) -> float:
    """A deliberately pessimistic cost for real, reported usage from a model
    with no config/parameters/pricing.json entry: the EUR-20/case actual-
    spend cap must never silently go uncounted just because a price lookup
    failed, so this prices the real tokens at the most expensive configured
    model's rate (never under-counts against any currently known model).
    The caller still records why (a missing-price error), so the gap gets
    fixed rather than hidden; this only keeps the cap meaningful meanwhile.
    """
    pricing = load_pricing()
    input_rate = max(float(spec["input_usd_per_1m_tokens"]) for spec in pricing["models"].values())
    output_rate = max(float(spec["output_usd_per_1m_tokens"]) for spec in pricing["models"].values())
    rate = float(pricing["exchange_rates"]["usd_to_eur"])
    return round((int(input_tokens) / 1_000_000 * input_rate
                  + int(output_tokens) / 1_000_000 * output_rate) * rate, 8)


def _stage_matches(call_stage: str | None, estimate_stage: str) -> bool:
    if not call_stage:
        return False
    return call_stage == estimate_stage or call_stage.split(":", 1)[0] == estimate_stage.split(":", 1)[0]


def estimate_vs_actual_report(db: Session, job: DocumentaryJob) -> dict[str, Any]:
    """Build the durable per-run estimate-vs-actual EUR table."""
    case = db.get(Case, job.case_id)
    if case is None:
        return {"rows": [], "totals": {"estimated_eur": 0.0, "actual_eur": 0.0}}
    estimate = documentary_estimate(
        db, case, json.loads(job.languages_json or '["en"]'),
        target_minutes=job.target_minutes,
        pilot_seconds=job.pilot_seconds if job.mode == "pilot" else None,
    )
    calls = db.query(ProviderCall).filter(
        ProviderCall.case_id == job.case_id,
        ProviderCall.documentary_job_id == job.id,
    ).all()
    saved_stages = json.loads(job.stages_json or "[]")

    def stage_state(name: str) -> str | None:
        for item in saved_stages:
            if _stage_matches(item.get("name"), name):
                return item.get("status")
        return None

    rows = []
    for line in estimate["lines"]:
        matched = [call for call in calls if _stage_matches(call.stage, line["stage"])]
        actual = round(sum(float(call.cost_eur or 0.0) for call in matched), 6)
        expected = float(line["estimated_cost_eur"])
        state = stage_state(line["stage"])
        not_run = state in (None, "pending", "blocked", "skipped")
        difference = None if expected == 0 or not_run else round((actual - expected) / expected * 100, 2)
        estimated = any(bool(call.estimated) for call in matched)
        rows.append({
            "stage": line["stage"],
            "estimated_eur": round(expected, 6),
            "actual_eur": None if not_run else actual,
            "difference_percent": difference,
            "estimated": estimated,
            "estimated_reason": (
                "Provider response did not report usage; the configured estimate was recorded."
                if estimated else None
            ),
            "status": "not run" if not_run else (state or line["status"]),
            "reason": "not run before the job stopped" if not_run else line["reason"],
        })
    return {
        "rows": rows,
        "totals": {
            "estimated_eur": round(sum(row["estimated_eur"] for row in rows), 6),
            "actual_eur": round(sum(row["actual_eur"] or 0.0 for row in rows), 6),
        },
        "pricing_last_verified": estimate["pricing_last_verified"],
    }
