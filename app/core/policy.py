"""Shared policy decisions and the stable error vocabulary.

Policy objects are deliberately small and serialisable so API responses, job
results, audit events and UI warnings use the same contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import JSONResponse

Severity = Literal["info", "warning", "error"]
Action = Literal["continue", "redo", "review", "stop", "omit", "degrade"]


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    policy: str
    decision: str
    reason: str
    rule_id: str
    severity: Severity
    recoverable: bool
    actions: tuple[Action, ...]
    context: dict[str, Any]
    code: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["actions"] = list(self.actions)
        value["message"] = self.message or self.reason
        return value


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    severity: Severity
    recoverable: bool
    actions: tuple[Action, ...]


ERROR_CATALOG: dict[str, ErrorSpec] = {
    "RIGHTS_CONFIRMATION_REQUIRED": ErrorSpec("error", True, ("review", "continue")),
    "MEDIA_INVALID": ErrorSpec("error", False, ("review", "omit")),
    "MEDIA_DUPLICATE": ErrorSpec("warning", True, ("review", "omit")),
    "MEDIA_REUSE_BLOCKED": ErrorSpec("warning", True, ("review", "continue")),
    "AUDIO_PLAN_INVALID": ErrorSpec("error", True, ("redo", "review", "continue")),
    "AUDIO_PLAN_DEGRADED": ErrorSpec("warning", True, ("continue", "review")),
    "TIMELINE_PRECISION_UNSUPPORTED": ErrorSpec("warning", True, ("review", "redo")),
    "TIMELINE_LAYOUT_CONFLICT": ErrorSpec("warning", True, ("review", "redo")),
    "SHORTFORM_PROTOTYPE_ONLY": ErrorSpec("info", True, ("continue", "review")),
    "INTRO_MISSING": ErrorSpec("warning", True, ("continue", "redo", "review")),
    "STAGE_DEGRADED": ErrorSpec("warning", True, ("continue", "review")),
    "STAGE_WARNING": ErrorSpec("warning", True, ("continue", "review")),
    "ARTIFACT_REGISTRATION_FAILED": ErrorSpec("warning", True, ("continue", "review")),
    "ARTIFACT_REGISTRY_DRIFT": ErrorSpec("warning", True, ("continue", "review", "redo")),
    "SEARCH_UNAVAILABLE": ErrorSpec("error", True, ("review", "continue")),
    "INCREMENTAL_UNPROVEN": ErrorSpec("error", False, ("review",)),
}


class PolicyError(RuntimeError):
    def __init__(self, code: str, message: str | None = None,
                 context: dict[str, Any] | None = None, status_code: int = 409):
        if code not in ERROR_CATALOG:
            raise ValueError(f"unknown policy error code: {code}")
        self.code = code
        self.context = context or {}
        self.status_code = status_code
        self.spec = ERROR_CATALOG[code]
        self.message = message or code.replace("_", " ").title()
        super().__init__(self.message)

    def structured(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.spec.severity,
            "recoverable": self.spec.recoverable,
            "actions": list(self.spec.actions),
            "message": self.message,
            "context": self.context,
        }


def warning_for(code: str, reason: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the structured warning shape used by job results and the UI."""
    spec = ERROR_CATALOG[code]
    decision = PolicyDecision(
        policy="production",
        decision="degraded",
        reason=reason,
        rule_id=code,
        severity=spec.severity,
        recoverable=spec.recoverable,
        actions=spec.actions,
        context=context or {},
        code=code,
        message=reason,
    )
    return decision.to_dict()


async def policy_exception_handler(_request: Request, exc: PolicyError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.structured())
