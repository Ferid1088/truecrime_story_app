"""Validation for the Film Experience Reviewer's structured output (Section J OUTPUT envelope).
Same discipline as schema.py (Section C) / design_schema.py (Section G): catch a schema-valid but
semantically dishonest response before anything is persisted or routed."""

from __future__ import annotations

_VALID_OUTCOMES = {"pass", "revise", "needs_attention"}
_VALID_MODES = {"plan_audit", "rendered_review", "repair_check"}
_VALID_INSPECTION_MODES = {"plan", "frames", "motion", "audio", "transcript_only"}
_VALID_SEVERITIES = {"blocking", "major", "minor"}
_VALID_REPAIR_OWNERS = {"detective_director", "visual_design_director",
                        "scheduler_renderer", "narration_workflow"}
_REQUIRED_ENVELOPE = ("schema_version", "run_id", "film_id", "language", "text_version",
                     "mode", "outcome")

# A mode can only claim the inspection types it could actually have performed. plan_audit never
# sees rendered media at all; rendered_review without audio in its own inspected_media can't claim
# it checked audio intelligibility.
_MAX_MODES_FOR = {
    "plan_audit": {"plan", "transcript_only"},
    "rendered_review": {"plan", "frames", "motion", "audio", "transcript_only"},
    "repair_check": {"plan", "frames", "motion", "audio", "transcript_only"},
}


class ReviewOutputError(ValueError):
    """The Film Experience Reviewer's structured output violates a Section J invariant."""


def validate_review_output(raw: dict) -> list[str]:
    violations: list[str] = []
    for key in _REQUIRED_ENVELOPE:
        if key not in raw:
            raise ReviewOutputError(f"missing required envelope field {key!r}")
    mode = raw["mode"]
    if mode not in _VALID_MODES:
        raise ReviewOutputError(f"unknown mode {mode!r}")
    if raw["outcome"] not in _VALID_OUTCOMES:
        violations.append(f"outcome {raw['outcome']!r} not in {_VALID_OUTCOMES}")

    claimed_modes = set(raw.get("inspection_modes") or [])
    unknown = claimed_modes - _VALID_INSPECTION_MODES
    if unknown:
        violations.append(f"unknown inspection_modes {sorted(unknown)}")
    overclaimed = claimed_modes - _MAX_MODES_FOR.get(mode, _VALID_INSPECTION_MODES)
    if overclaimed:
        violations.append(
            f"mode {mode!r} cannot honestly claim inspection_modes {sorted(overclaimed)} "
            "— reviewer honesty requires scope to match what was actually examined"
        )
    if mode == "plan_audit" and ("motion" in claimed_modes or "audio" in claimed_modes):
        violations.append("plan_audit must never claim motion or audio was inspected")

    for finding in raw.get("findings") or []:
        owner = finding.get("repair_owner")
        if owner not in _VALID_REPAIR_OWNERS:
            violations.append(f"finding {finding.get('id')!r} has invalid repair_owner {owner!r}")
        severity = finding.get("severity")
        if severity not in _VALID_SEVERITIES:
            violations.append(f"finding {finding.get('id')!r} has invalid severity {severity!r}")
        if not finding.get("id"):
            violations.append("a finding is missing its stable id")

    if raw["outcome"] in ("pass", "needs_attention") and raw.get("findings") and raw["outcome"] == "pass":
        violations.append("outcome 'pass' with non-empty findings — a pass must have no open findings")

    return violations
