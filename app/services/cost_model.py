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
from app.db.models import Case, StoryVersion

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
            "eur": {key: round(value, 2) for key, value in self.eur.items()},
            "estimated_cost_eur": round(self.expected, 2),
            "model": self.model,
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


def _llm_line(pricing: dict[str, Any], stage: str, model: str,
              input_tokens: dict[str, float], output_tokens: dict[str, float]) -> CostLine:
    input_price, output_price, provider_model = _model_price(pricing, model)
    eur = _round_range({
        key: input_tokens[key] / 1_000_000 * input_price
        + output_tokens[key] / 1_000_000 * output_price
        for key in SCENARIOS
    })
    return CostLine(stage, "run", None,
                    {"input_tokens": input_tokens["expected"],
                     "output_tokens": output_tokens["expected"]},
                    {"input_eur_per_1m_tokens": input_price,
                     "output_eur_per_1m_tokens": output_price}, eur, provider_model)


def _single_unit_line(pricing: dict[str, Any], stage: str, status: str,
                      reason: str | None, units: dict[str, float], prices: dict[str, float],
                      eur: dict[str, float], model: str | None = None) -> CostLine:
    return CostLine(stage, status, reason, units, prices, _round_range(eur), model)


def _words(text: str | None) -> int:
    return len((text or "").split())


def _metrics(db: Session, case: Case, target_minutes: float | None,
             pricing: dict[str, Any]) -> dict[str, dict[str, float]]:
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
    return {
        "target_minutes": {key: float(target_minutes or value)
                            for key, value in assumptions["target_minutes"].items()},
        "source_words": {key: expected_source * float(multipliers[key])
                         for key in SCENARIOS},
        "story_words": {key: expected_story * float(multipliers[key])
                        for key in SCENARIOS},
    }


def _scale(metrics: dict[str, float], key: str, factor: float) -> dict[str, float]:
    return {scenario: metrics[scenario] * factor for scenario in SCENARIOS}


def estimate_lines(db: Session, case: Case, languages: list[str] | None = None,
                   target_minutes: float | None = None,
                   pricing: dict[str, Any] | None = None) -> tuple[list[CostLine], dict[str, dict[str, float]]]:
    pricing = pricing or load_pricing()
    languages = languages or ["en"]
    metrics = _metrics(db, case, target_minutes, pricing)
    source = metrics["source_words"]
    story = metrics["story_words"]
    assumptions = pricing["assumptions"]
    chars = _scale(story, "story_words", float(assumptions["narration_characters_per_word"]))
    lines: list[CostLine] = []
    lines.append(_llm_line(pricing, "research", "cheap",
                           _scale(source, "source_words", 1.3),
                           _scale(source, "source_words", 0.45)))
    lines.append(_llm_line(pricing, "master_story", "writer",
                           _scale(source, "source_words", 1.5),
                           _scale(story, "story_words", 1.3)))
    lines.append(_llm_line(pricing, "spoken:en", "premium",
                           _scale(story, "story_words", 1.2),
                           _scale(story, "story_words", 1.05)))
    lines.append(_llm_line(pricing, "performance:en", "premium",
                           _scale(story, "story_words", 1.1),
                           {key: 900 * float(assumptions["range_multipliers"][key])
                            for key in SCENARIOS}))
    tts_price = float(pricing["tts"]["eleven_v3"]["eur_per_1m_characters"])
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
    short_price = float(pricing["image"]["short_render"]["eur_per_render"])
    lines.append(_single_unit_line(
        pricing, "short_render:en", "run", None, {"renders": short_count},
        {"eur_per_render": short_price},
        {key: short_count * short_price * float(assumptions["range_multipliers"][key])
         for key in SCENARIOS}, pricing["image"]["short_render"].get("provider_model")))
    lines.append(_single_unit_line(pricing, "render:en", "skipped",
                                   "FFmpeg render is local and has no provider charge.",
                                   {}, {}, {key: 0.0 for key in SCENARIOS}))
    return lines, metrics


def documentary_estimate(db: Session, case: Case, languages: list[str] | None = None,
                         target_minutes: float | None = None) -> dict[str, Any]:
    lines, metrics = estimate_lines(db, case, languages, target_minutes)
    totals = {key: round(sum(line.eur[key] for line in lines), 2) for key in SCENARIOS}
    return {"lines": [line.as_dict() for line in lines],
            "totals": totals, "metrics": metrics,
            "pricing_last_verified": load_pricing()["last_verified"]}


def estimate_stage_eur(db: Session, case_id: int, stage: str) -> float:
    case = db.get(Case, case_id)
    if case is None:
        return 0.0
    lines, _ = estimate_lines(db, case, ["en"])
    base = stage.split(":", 1)[0]
    aliases = {"voice": "tts:en", "short_render": "short_render:en"}
    lookup = aliases.get(base, stage)
    candidates = [line for line in lines if line.stage == lookup or line.stage == stage
                  or line.stage.split(":", 1)[0] == base]
    return round(sum(line.expected for line in candidates), 6)
