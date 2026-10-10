from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re


class Modality(str, Enum):
    ESTABLISHED = "ESTABLISHED"
    BELIEVED_BY_INVESTIGATORS = "BELIEVED_BY_INVESTIGATORS"
    ALLEGED = "ALLEGED"
    ABSENCE_OF_EVIDENCE = "ABSENCE_OF_EVIDENCE"
    DISPUTED = "DISPUTED"


MODALITY_STRENGTH = {
    Modality.DISPUTED: 0,
    Modality.ABSENCE_OF_EVIDENCE: 1,
    Modality.ALLEGED: 2,
    Modality.BELIEVED_BY_INVESTIGATORS: 3,
    Modality.ESTABLISHED: 4,
}


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    offending_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ClaimUse:
    source_claim_id: str
    source_modality: Modality | str
    short_modality: Modality | str
    text: str = ""


@dataclass(frozen=True)
class AssetUse:
    asset_id: str
    rights_status: str = "unknown"
    platform_claim_risk: str = "low"
    media_kind: str = "visual"
    reveals: list[str] = field(default_factory=list)
    human_signoff: bool = False
    requires_ai_disclosure: bool = False


@dataclass(frozen=True)
class PolicySignal:
    signal: str
    severity: str = "medium"
    reason: str = ""


def reveal_firewall(
    *,
    allowed_reveals: list[str],
    text_reveals: list[str] | None = None,
    audio_reveals: list[str] | None = None,
    visual_reveals: list[str] | None = None,
) -> GateResult:
    allowed = set(allowed_reveals)
    buckets = {
        "text": text_reveals or [],
        "audio": audio_reveals or [],
        "visual": visual_reveals or [],
    }
    offending: list[str] = []
    reasons: list[str] = []
    for bucket, reveals in buckets.items():
        bad = [r for r in reveals if r not in allowed]
        if bad:
            offending.extend(bad)
            reasons.append(f"{bucket} exposes forbidden reveal(s): {', '.join(bad)}")
    return GateResult(
        name="RevealFirewall",
        passed=not offending,
        reasons=reasons,
        offending_ids=sorted(set(offending)),
        metadata={
            "allowed_reveals": sorted(allowed),
            "forbidden_reveals": sorted(set(offending)),
            "spoiler_check_result": "pass" if not offending else "fail",
        },
    )


def epistemic_checker(claims: list[ClaimUse]) -> GateResult:
    reasons: list[str] = []
    offending: list[str] = []
    for claim in claims:
        source = _modality(claim.source_modality)
        short = _modality(claim.short_modality)
        if MODALITY_STRENGTH[short] > MODALITY_STRENGTH[source]:
            offending.append(claim.source_claim_id)
            reasons.append(
                f"{claim.source_claim_id} is strengthened from {source.value} to {short.value}"
            )
    return GateResult(
        name="EpistemicChecker",
        passed=not offending,
        reasons=reasons,
        offending_ids=offending,
    )


def rights_checker(assets: list[AssetUse], platform: str) -> GateResult:
    reasons: list[str] = []
    offending: list[str] = []
    for asset in assets:
        stricter = platform in {"instagram_reel", "tiktok_video"}
        if asset.rights_status == "unknown":
            offending.append(asset.asset_id)
            reasons.append(f"{asset.asset_id} has unknown rights")
        if asset.platform_claim_risk == "high" and not asset.human_signoff:
            offending.append(asset.asset_id)
            reasons.append(f"{asset.asset_id} has high platform claim risk")
        if stricter and asset.rights_status in {"fair_use_review", "editorial_review_required"}:
            if not asset.human_signoff:
                offending.append(asset.asset_id)
                reasons.append(f"{asset.asset_id} needs {platform} rights sign-off")
    return GateResult(
        name="RightsChecker",
        passed=not offending,
        reasons=reasons,
        offending_ids=sorted(set(offending)),
        metadata={"platform": platform},
    )


def policy_risk_checker(signals: list[PolicySignal], platform: str) -> GateResult:
    high = [s for s in signals if s.severity == "high"]
    return GateResult(
        name="PolicyRiskChecker",
        passed=not high,
        reasons=[s.reason or s.signal for s in high],
        offending_ids=[s.signal for s in high],
        metadata={"platform": platform, "policy_risk": [s.__dict__ for s in signals]},
    )


def disclosure_checker(
    *,
    assets: list[AssetUse],
    synthetic_voice: bool = False,
    disclosure_flags: dict[str, bool] | None = None,
    platform: str,
) -> GateResult:
    flags = disclosure_flags or {}
    needs = synthetic_voice or any(a.requires_ai_disclosure for a in assets)
    platforms_requiring = {"youtube_short", "tiktok_video", "instagram_reel", "facebook_reel"}
    missing = needs and platform in platforms_requiring and not flags.get(platform)
    return GateResult(
        name="DisclosureChecker",
        passed=not missing,
        reasons=[f"{platform} AI disclosure metadata is missing"] if missing else [],
        metadata={"platform": platform, "requires_disclosure": needs},
    )


def duration_checker(duration_seconds: float, *, min_seconds: float, max_seconds: float, hard_max_seconds: float = 60) -> GateResult:
    reasons: list[str] = []
    if duration_seconds < min_seconds:
        reasons.append(f"duration {duration_seconds:g}s is below minimum {min_seconds:g}s")
    if duration_seconds > max_seconds:
        reasons.append(f"duration {duration_seconds:g}s exceeds platform max {max_seconds:g}s")
    if duration_seconds > hard_max_seconds:
        reasons.append(f"duration {duration_seconds:g}s exceeds hard max {hard_max_seconds:g}s")
    return GateResult(
        name="DurationChecker",
        passed=not reasons,
        reasons=reasons,
        metadata={
            "duration_seconds": duration_seconds,
            "min_seconds": min_seconds,
            "max_seconds": max_seconds,
            "hard_max_seconds": hard_max_seconds,
        },
    )


BANNED_HOOK_PATTERNS = [
    re.compile(r"\byou won't believe\b", re.I),
    re.compile(r"\bthis case is crazy\b", re.I),
    re.compile(r"\bwhat happened next shocked everyone\b", re.I),
    re.compile(r"\bthe truth will blow your mind\b", re.I),
]


def automation_feel_checker(*, hook: str, cta: str) -> GateResult:
    reasons = []
    for pattern in BANNED_HOOK_PATTERNS:
        if pattern.search(hook):
            reasons.append(f"banned hook pattern: {pattern.pattern}")
    if re.search(r"\b(like now|subscribe now|link in bio|you have to watch this)\b", cta, re.I):
        reasons.append("banned CTA pattern")
    return GateResult(
        name="AutomationFeelChecker",
        passed=not reasons,
        reasons=reasons,
    )


def _modality(value: Modality | str) -> Modality:
    if isinstance(value, Modality):
        return value
    return Modality(value)

