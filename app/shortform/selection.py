from __future__ import annotations

from collections import Counter
import math
import re

from app.core.ai_config import ai_config
from app.shortform.gates import (
    AssetUse,
    ClaimUse,
    Modality,
    automation_feel_checker,
    disclosure_checker,
    duration_checker,
    epistemic_checker,
    reveal_firewall,
    rights_checker,
)


def evaluate_candidate(candidate: dict, *, platform: str = "youtube_short") -> dict:
    platform_cfg = ai_config.short_form.distribution[platform]
    assets = [_asset_use(a) for a in candidate.get("asset_details", candidate.get("asset_ids", []))]
    claims = [
        ClaimUse(
            source_claim_id=str(c.get("source_claim_id")),
            source_modality=c.get("source_modality") or c.get("modality") or Modality.ESTABLISHED,
            short_modality=c.get("short_modality") or c.get("modality") or Modality.ESTABLISHED,
            text=c.get("text", ""),
        )
        for c in candidate.get("claims", [])
    ]
    visual_reveals = [
        reveal for asset in assets for reveal in asset.reveals
    ] or candidate.get("visual_reveals", [])
    gates = [
        reveal_firewall(
            allowed_reveals=candidate.get("allowed_reveals", []),
            text_reveals=candidate.get("text_reveals", candidate.get("allowed_reveals", [])),
            audio_reveals=candidate.get("audio_reveals", []),
            visual_reveals=visual_reveals,
        ),
        epistemic_checker(claims),
        rights_checker(assets, platform=platform),
        disclosure_checker(
            assets=assets,
            synthetic_voice=bool(candidate.get("synthetic_voice", False)),
            disclosure_flags=candidate.get("disclosure_flags", {}),
            platform=platform,
        ),
        duration_checker(
            float(candidate.get("duration", platform_cfg.target_seconds)),
            min_seconds=platform_cfg.min_seconds,
            max_seconds=platform_cfg.max_seconds,
            hard_max_seconds=ai_config.short_form.hard_max_seconds,
        ),
        automation_feel_checker(
            hook=candidate.get("hook", ""),
            cta=candidate.get("cta", ""),
        ),
    ]
    passed = all(g.passed for g in gates)
    rejection_reasons = [
        reason for gate in gates if not gate.passed for reason in gate.reasons
    ]
    soft_scores = candidate.get("soft_scores") or score_candidate(candidate)
    return {
        **candidate,
        "gate_results": {
            gate.name: {
                "passed": gate.passed,
                "reasons": gate.reasons,
                "offending_ids": gate.offending_ids,
                "metadata": gate.metadata,
            }
            for gate in gates
        },
        "soft_scores": soft_scores,
        "status": "quality_checked" if passed else "needs_review",
        "passed_hard_gates": passed,
        "rejection_reasons": rejection_reasons,
    }


def score_candidate(candidate: dict) -> dict:
    hook = candidate.get("hook", "")
    evidence = candidate.get("evidence_ids", [])
    assets = candidate.get("asset_ids", [])
    hook_strength = min(1.0, 0.35 + len(hook) / 140)
    case_specificity = min(1.0, 0.25 + 0.2 * len(evidence) + 0.1 * len(assets))
    brand_fit = 0.8 if not re.search(r"crazy|shocking|insane", hook, re.I) else 0.2
    repetition_score = 0.0
    return {
        "hook_strength": round(hook_strength, 3),
        "case_specificity": round(case_specificity, 3),
        "brand_fit": brand_fit,
        "repetition_score": repetition_score,
        "total": round(
            hook_strength * 0.4 + case_specificity * 0.35 + brand_fit * 0.25,
            3,
        ),
    }


def select_candidates(
    candidates: list[dict],
    *,
    target_count: int | None = None,
    platform: str = "youtube_short",
) -> dict:
    target = target_count or ai_config.short_form.publish_target
    evaluated = [evaluate_candidate(c, platform=platform) for c in candidates]
    survivors = [c for c in evaluated if c["passed_hard_gates"]]
    rejected = [c for c in evaluated if not c["passed_hard_gates"]]
    selected = mmr_select(survivors, target)
    diversity = diversity_report(selected)
    if len(selected) > 1 and diversity["concept_type_count"] < 2:
        rejected.extend(selected[1:])
        for c in selected[1:]:
            c["rejection_reasons"] = ["diversity gate: selected set repeats one concept type"]
        selected = selected[:1]
        diversity = diversity_report(selected)
    return {
        "requested": target,
        "qualified": len(survivors),
        "selected": selected,
        "rejected": rejected,
        "shortfall": max(0, target - len(selected)),
        "shortfall_reasons": shortfall_reasons(target, selected, rejected),
        "diversity": diversity,
    }


def mmr_select(candidates: list[dict], target: int) -> list[dict]:
    remaining = sorted(
        candidates,
        key=lambda c: c.get("soft_scores", {}).get("total", 0),
        reverse=True,
    )
    selected: list[dict] = []
    while remaining and len(selected) < target:
        if not selected:
            selected.append(remaining.pop(0))
            continue
        best_idx = 0
        best_score = -math.inf
        for idx, candidate in enumerate(remaining):
            base = candidate.get("soft_scores", {}).get("total", 0)
            sim = max(similarity(candidate, s) for s in selected)
            score = base - sim * 0.45
            if score > best_score:
                best_idx = idx
                best_score = score
        selected.append(remaining.pop(best_idx))
    return selected


def similarity(a: dict, b: dict) -> float:
    parts = []
    if a.get("concept_type") == b.get("concept_type"):
        parts.append(0.3)
    if set(a.get("source_beat_ids", [])) & set(b.get("source_beat_ids", [])):
        parts.append(0.25)
    if set(a.get("asset_ids", [])) & set(b.get("asset_ids", [])):
        parts.append(0.2)
    hook_a = set(re.findall(r"\w+", a.get("hook", "").lower()))
    hook_b = set(re.findall(r"\w+", b.get("hook", "").lower()))
    if hook_a and hook_b:
        parts.append(0.25 * len(hook_a & hook_b) / len(hook_a | hook_b))
    return sum(parts)


def diversity_report(selected: list[dict]) -> dict:
    types = Counter(c.get("concept_type") for c in selected)
    hooks = Counter(hook_structure(c.get("hook", "")) for c in selected)
    return {
        "concept_type_count": len(types),
        "concept_types": dict(types),
        "hook_structures": dict(hooks),
        "passes": len(selected) <= 1 or len(types) > 1,
    }


def hook_structure(hook: str) -> str:
    hook = hook.strip()
    if hook.endswith("?"):
        return "question"
    if ":" in hook:
        return "colon_fact"
    if hook.lower().startswith(("one ", "the ")):
        return "declarative_detail"
    return "statement"


def shortfall_reasons(target: int, selected: list[dict], rejected: list[dict]) -> list[str]:
    if len(selected) >= target:
        return []
    reasons = Counter(
        reason
        for c in rejected
        for reason in c.get("rejection_reasons", [])
    )
    if not reasons:
        return [f"Requested {target}, qualified {len(selected)}"]
    return [f"{reason}: {count}" for reason, count in reasons.most_common()]


def _asset_use(asset) -> AssetUse:
    if not isinstance(asset, dict):
        return AssetUse(asset_id=str(asset), rights_status="owned")
    return AssetUse(
        asset_id=str(asset.get("asset_id") or asset.get("id") or asset),
        rights_status=asset.get("rights_status", "owned"),
        platform_claim_risk=asset.get("platform_claim_risk", "low"),
        reveals=asset.get("reveals", []),
        human_signoff=asset.get("human_signoff", False),
        requires_ai_disclosure=asset.get("requires_ai_disclosure", False),
    )
