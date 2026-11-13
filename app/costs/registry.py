"""Which step, language, vendor and object a ledger row belongs to, from the rules in config/cost_steps.json. One function for new rows (written with the row)
and for the old rows (nachgetragen once), so both are labelled the same way."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[2] / "config" / "cost_steps.json"
UNKNOWN_LANGUAGE = "_unknown"
SHARED = "_shared"


@lru_cache(maxsize=1)
def registry() -> dict:
    raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    raw["_steps"] = [(s["key"], [re.compile(p) for p in s["stage_patterns"]]) for s in raw["steps"]]
    raw["_vendors"] = [(v["key"], v["hosts"], [re.compile(p) for p in v["models"]], [re.compile(p) for p in v["stages"]]) for v in raw["vendors"]]
    raw["_language"] = re.compile(raw["language_pattern"])
    return raw


def step_labels() -> dict[str, str]:
    return {s["key"]: s["label"] for s in registry()["steps"]}


def vendor_labels() -> dict[str, str]:
    return {v["key"]: v["label"] for v in registry()["vendors"]}


def shared_steps() -> list[str]:
    return [s["key"] for s in registry()["steps"] if s["shared"]]


@dataclass(frozen=True)
class Label:
    step: str
    language: str | None
    vendor: str | None
    subject: str | None
    accuracy: str


def step_of(stage: str | None) -> str:
    text = (stage or "").strip().lower()
    for key, patterns in registry()["_steps"]:
        if any(p.search(text) for p in patterns):
            return key
    return "other"


def language_of(stage: str | None) -> str | None:
    found = registry()["_language"].search((stage or "").lower())
    return found.group(1) if found else None


def vendor_of(host: str | None, model: str | None, stage: str | None) -> str | None:
    text, name = (stage or "").strip().lower(), (model or "").strip().lower()
    for key, hosts, models, stages in registry()["_vendors"]:
        if host in hosts or any(p.search(name) for p in models) or any(p.search(text) for p in stages):
            return key
    return None


def subject_of(stage: str | None) -> str | None:
    """What follows the step's own name in a stage ("reading: write fa_b01_g01_01" is about "write fa_b01_g01_01"), else nothing."""
    text = (stage or "").strip()
    return text.split(":", 1)[1].strip()[:120] if ":" in text and text.split(":", 1)[1].strip() else None


def accuracy_of(cost_eur: float | None, estimated: bool | None, cost_source: str | None) -> str:
    """none = no price (counted as 0), estimated = a guess or an assumed price, reported = the provider's own figure, computed = the reported usage priced from the table."""
    if cost_eur is None:
        return "none"
    if estimated or cost_source in registry()["estimated_sources"]:
        return "estimated"
    return "reported" if cost_source == "provider_reported" else "computed"


def label(*, host: str | None, stage: str | None, model: str | None, cost_eur: float | None, estimated: bool | None, cost_source: str | None,
          language: str | None = None) -> Label:
    """`language` is what the running pipeline step said (it wins); else the stage's own name may carry it."""
    return Label(step_of(stage), language or language_of(stage), vendor_of(host, model, stage), subject_of(stage), accuracy_of(cost_eur, estimated, cost_source))
