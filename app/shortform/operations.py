"""Offline planning and publishing contracts for short-form operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Protocol

from app.core.ai_config import ai_config


@dataclass(frozen=True)
class CriticDecision:
    concept_id: str
    approved: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlatformExport:
    concept_id: str
    platform: str
    language: str
    duration: float
    cta: str
    destination: str
    campaign_id: str
    disclosure_required: bool = True
    rights_status: str = "cleared"
    policy_status: str = "pass"


@dataclass(frozen=True)
class PublishSlot:
    day: int
    platform: str
    approved_only: bool = True
    status: str = "planned"


class SocialPublisher(Protocol):
    def publish(self, export: PlatformExport, approved: bool) -> str: ...


class ExportPackagePublisher:
    """The only working publisher: it produces an export handoff, never posts."""

    def publish(self, export: PlatformExport, approved: bool) -> str:
        if not approved:
            raise PermissionError("human approval is required before export handoff")
        return "exported"


PUBLISHABLE_STATUSES = {"scheduled", "publishing", "published"}


def transition_publish_status(
    current: str,
    target: str,
    *,
    human_approved: bool = False,
) -> str:
    """Guard every outward-facing status transition behind human approval."""
    if target not in {"draft", "review", "approved", *PUBLISHABLE_STATUSES}:
        raise ValueError(f"unknown publishing status: {target}")
    if target in PUBLISHABLE_STATUSES and not human_approved:
        raise PermissionError("human approval is required before scheduling or publishing")
    if target in PUBLISHABLE_STATUSES and current != "approved":
        raise PermissionError("only an approved export can be scheduled or published")
    return target


def resolve_voice_ids(languages: tuple[str, ...] = ("en", "de", "fa", "ar")) -> dict[str, str]:
    return {language: ai_config.voice.for_language(language).voice_id for language in languages}


def native_script(concept: dict, language: str) -> dict:
    """Create a language-specific script record without calling translation APIs."""
    hooks = concept.get("native_hooks", {})
    hook = hooks.get(language) or concept.get("hook", "")
    return {"concept_id": concept.get("id", ""), "language": language,
            "hook": hook, "native_authored": language in hooks,
            "direction": "rtl" if language in {"fa", "ar"} else "ltr"}


def derive_variants(concept_id: str, language: str, cta: str,
                    destination: str) -> list[PlatformExport]:
    variants = []
    for platform, cfg in ai_config.short_form.distribution.items():
        if not cfg.enabled or cfg.count == 0:
            continue
        variants.append(PlatformExport(
            concept_id=concept_id, platform=platform, language=language,
            duration=float(cfg.target_seconds), cta=cta,
            destination=destination, campaign_id=f"{concept_id}-{platform}-{language}",
        ))
    return variants


def build_publish_plan(start: date, counts: dict[str, int], days: int = 14) -> list[PublishSlot]:
    if days < 10 or days > 14:
        raise ValueError("publishing window must be 10 to 14 days")
    if any(count < 0 for count in counts.values()):
        raise ValueError("publish counts cannot be negative")
    total = sum(counts.values())
    if not total:
        return []
    # Interleave platforms before spreading the queue. This keeps one
    # platform from monopolizing the opening days and lets the configured
    # totals fit a 10-14 day plan without dumping everything on day one.
    remaining = dict(sorted(counts.items()))
    queue: list[str] = []
    while any(remaining.values()):
        for platform in remaining:
            if remaining[platform]:
                queue.append(platform)
                remaining[platform] -= 1
    slots = []
    for index, platform in enumerate(queue):
        day = 1 + (index * days // total)
        slots.append(PublishSlot(day=day, platform=platform))
    return slots


def repetition_check(items: list[dict], max_repeats: int = 1) -> tuple[bool, list[str]]:
    fields = ("hook_structure", "cta", "opening_visual", "host_pose")
    reasons = []
    for field in fields:
        seen = {}
        for item in items:
            value = item.get(field)
            if value:
                seen[value] = seen.get(value, 0) + 1
        repeated = [value for value, count in seen.items() if count > max_repeats]
        if repeated:
            reasons.append(f"{field} repeats: {', '.join(repeated)}")
    return not reasons, reasons


def metrics_summary(rows: list[dict], minimum_views: int = 100) -> dict:
    eligible = [row for row in rows if row.get("views", 0) >= minimum_views]
    return {"eligible": len(eligible), "attribution_status": "unavailable",
            "conversion_claim": None, "platforms": sorted({r["platform"] for r in eligible})}
