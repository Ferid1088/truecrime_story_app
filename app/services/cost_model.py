"""Config-backed, no-send production cost estimates.

The estimator deliberately exposes the units used for every line.  It is used
by the dry run, stage-control preview, and the per-call budget gate so a UI
estimate cannot silently disagree with the amount reserved by a real stage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, DocumentaryJob, ProviderCall, StoryVersion

PRICING_PATH = Path(__file__).resolve().parents[2] / "config" / "parameters" / "pricing.json"
SCENARIOS = ("low", "expected", "high")


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
        spec = pricing["tts"].get("eleven_v3")
    elif stage == "thumbnail":
        spec = pricing["image"].get("thumbnail")
    elif stage.startswith("host"):
        spec = pricing.get("heygen")
    price_status, warning = (("verified", None) if status == "skipped" and spec is None
                             else _price_state(spec, stage))
    return CostLine(stage, status, reason, units, prices, _round_range(eur), model,
                    price_status, warning)


def _words(text: str | None) -> int:
    return len((text or "").split())


def _metrics(db: Session, case: Case, target_minutes: float | None,
             pricing: dict[str, Any]) -> dict[str, Any]:
    assumptions = pricing["assumptions"]
    multipliers = assumptions["range_multipliers"]
    source_words = sum(_words(source.raw_text or source.summary or source.notes)
                       for source in case.sources)
    stories = [row for row in case.stories if row.story_text]
    story_words = max((_words(row.story_text) for row in stories), default=0)
    expected_minutes = float(target_minutes or assumptions["target_minutes"]["expected"])
    expected_source = max(source_words, int(assumptions["source_words_floor"]))
    expected_story = max(story_words, int(assumptions["story_words_floor"]),
                         int(expected_minutes * assumptions["words_per_minute"]))
    result: dict[str, Any] = {
        "target_minutes": {key: float(target_minutes or value)
                            for key, value in assumptions["target_minutes"].items()},
        "source_words": {key: expected_source * float(multipliers[key])
                         for key in SCENARIOS},
        "story_words": {key: expected_story * float(multipliers[key])
                         for key in SCENARIOS},
    }
    words_to_tokens = float(assumptions["tokenization"]["words_to_tokens"])
    result["tokens"] = {
        "research_input": {key: result["source_words"][key] * words_to_tokens for key in SCENARIOS},
        "research_output": {key: result["source_words"][key] * 0.45 * words_to_tokens for key in SCENARIOS},
        "master_story_input": {key: result["source_words"][key] * 0.45 * words_to_tokens for key in SCENARIOS},
        "master_story_output": {key: result["story_words"][key] * words_to_tokens for key in SCENARIOS},
        "spoken_input": {key: result["story_words"][key] * 1.2 * words_to_tokens for key in SCENARIOS},
        "spoken_output": {key: result["story_words"][key] * 1.05 * words_to_tokens for key in SCENARIOS},
        "performance_input": {key: result["story_words"][key] * 1.1 * words_to_tokens for key in SCENARIOS},
        "performance_output": {key: 900 * float(multipliers[key]) for key in SCENARIOS},
    }
    return result


def _scale(metrics: dict[str, float], key: str, factor: float) -> dict[str, float]:
    return {scenario: metrics[scenario] * factor for scenario in SCENARIOS}


def estimate_lines(db: Session, case: Case, languages: list[str] | None = None,
                   target_minutes: float | None = None,
                   pilot_seconds: float | None = None,
                   pricing: dict[str, Any] | None = None) -> tuple[list[CostLine], dict[str, dict[str, float]]]:
    pricing = pricing or load_pricing()
    languages = languages or ["en"]
    metrics = _metrics(db, case, target_minutes, pricing)
    source = metrics["source_words"]
    story = metrics["story_words"]
    assumptions = pricing["assumptions"]
    chars = _scale(story, "story_words", float(assumptions["narration_characters_per_word"]))
    lines: list[CostLine] = []
    tokens = metrics["tokens"]
    lines.append(_llm_line(pricing, "research", "cheap",
                           tokens["research_input"], tokens["research_output"]))
    lines.append(_llm_line(pricing, "master_story", "writer",
                           tokens["master_story_input"], tokens["master_story_output"]))
    lines.append(_llm_line(pricing, "spoken:en", "premium",
                           tokens["spoken_input"], tokens["spoken_output"]))
    lines.append(_llm_line(pricing, "performance:en", "premium",
                           tokens["performance_input"], tokens["performance_output"]))
    tts_price = float(pricing["tts"]["eleven_v3"]["eur_per_1m_characters"])
    if pilot_seconds:
        chars = {key: float(pilot_seconds) / 60 * float(assumptions["words_per_minute"])
                 * float(assumptions["narration_characters_per_word"])
                 * float(assumptions["range_multipliers"][key]) for key in SCENARIOS}
    else:
        chars = _scale(story, "story_words", float(assumptions["narration_characters_per_word"]))
    lines.append(_single_unit_line(
        pricing, "tts:en", "run", None,
        {"characters": chars["expected"]}, {"eur_per_1m_characters": tts_price},
        {key: chars[key] / 1_000_000 * tts_price for key in SCENARIOS},
        pricing["tts"]["eleven_v3"].get("provider_model")))
    image_price = float(pricing["image"]["thumbnail"]["eur_per_image"])
    lines.append(_single_unit_line(
        pricing, "thumbnail", "run", None, {"images": 1}, {"eur_per_image": image_price},
        {key: image_price for key in SCENARIOS}, pricing["image"]["thumbnail"].get("provider_model")))

    if ai_config.host.enabled and ai_config.avatar.enabled:
        host_price = float(pricing["heygen"]["eur_per_minute"])
        lines.append(_single_unit_line(
            pricing, "host:en", "run", None,
            {"minutes": float(assumptions["host_minutes"]["expected"])},
            {"eur_per_minute": host_price},
            {key: float(assumptions["host_minutes"][key]) * host_price for key in SCENARIOS},
            pricing["heygen"].get("provider_model")))
    else:
        lines.append(_single_unit_line(pricing, "host:en", "skipped",
                                       "Host/HeyGen is disabled in configuration.", {}, {},
                                       {key: 0.0 for key in SCENARIOS}))

    if any(language != "en" for language in languages):
        lines.append(_llm_line(pricing, "translation", "writer",
                               _scale(story, "story_words", 1.1),
                               _scale(story, "story_words", 1.05)))
    else:
        lines.append(_single_unit_line(pricing, "translation", "skipped",
                                       "English is the canonical language; no translation call.",
                                       {}, {}, {key: 0.0 for key in SCENARIOS}))

    short_count = int(assumptions["short_count"])
    lines.append(_llm_line(pricing, "short_selection", "writer",
                           _scale(story, "story_words", 1.0),
                           {key: 1200 * float(assumptions["range_multipliers"][key])
                            for key in SCENARIOS}))
    lines.append(_single_unit_line(
        pricing, "short_render:en", "skipped",
        "FFmpeg short render is local; no provider charge is included.",
        {"renders": short_count}, {}, {key: 0.0 for key in SCENARIOS}, "local FFmpeg"))
    lines.append(_single_unit_line(pricing, "render:en", "skipped",
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


def model_cost_eur(model: str | None, input_tokens: int | None,
                   output_tokens: int | None) -> float | None:
    """Price provider-reported token usage using the configured model rate."""
    if not model or input_tokens is None or output_tokens is None:
        return None
    pricing = load_pricing()
    spec = next((row for row in pricing["models"].values()
                 if row.get("provider_model") == model), None)
    if not spec:
        return None
    rate = float(pricing["exchange_rates"]["usd_to_eur"])
    return round((int(input_tokens) / 1_000_000 * float(spec["input_usd_per_1m_tokens"])
                  + int(output_tokens) / 1_000_000 * float(spec["output_usd_per_1m_tokens"])) * rate, 8)


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
    rows = []
    for line in estimate["lines"]:
        matched = [call for call in calls if _stage_matches(call.stage, line["stage"])]
        actual = round(sum(float(call.cost_eur or 0.0) for call in matched), 6)
        expected = float(line["estimated_cost_eur"])
        difference = None if expected == 0 else round((actual - expected) / expected * 100, 2)
        rows.append({
            "stage": line["stage"],
            "estimated_eur": round(expected, 6),
            "actual_eur": actual,
            "difference_percent": difference,
            "estimated": any(bool(call.estimated) for call in matched),
            "status": line["status"],
            "reason": line["reason"],
        })
    return {
        "rows": rows,
        "totals": {
            "estimated_eur": round(sum(row["estimated_eur"] for row in rows), 6),
            "actual_eur": round(sum(row["actual_eur"] for row in rows), 6),
        },
        "pricing_last_verified": estimate["pricing_last_verified"],
    }
