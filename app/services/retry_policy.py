"""One auditable retry loop shared by production stages."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from app.core.policy import PolicyDecision
from app.services.policy_events import record_decision


class ExhaustedAction(StrEnum):
    STOP = "STOP"
    OMIT = "OMIT"
    DEGRADE = "DEGRADE"


@dataclass(frozen=True, slots=True)
class AuditResult:
    approved: bool
    reasons: tuple[Any, ...] = ()
    failed_item_ids: tuple[str, ...] = ()
    terminal: bool = False


@dataclass(frozen=True, slots=True)
class StageOutcome:
    status: str
    attempts: int
    reasons: tuple[Any, ...] = ()
    failed_item_ids: tuple[str, ...] = ()
    history: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "attempts": self.attempts,
            "reasons": list(self.reasons),
            "failed_item_ids": list(self.failed_item_ids),
            "history": [dict(item) for item in self.history],
        }


Produce = Callable[[dict[str, Any]], Any | Awaitable[Any]]
Audit = Callable[[Any], AuditResult | dict[str, Any] | bool | Awaitable[Any]]


def _audit_result(raw: AuditResult | dict[str, Any] | bool) -> AuditResult:
    if isinstance(raw, AuditResult):
        return raw
    if isinstance(raw, bool):
        return AuditResult(approved=raw)
    if not isinstance(raw, dict):
        return AuditResult(approved=False, reasons=("auditor returned no decision",))
    reasons = raw.get("reasons")
    if reasons is None:
        reasons = raw.get("failures") or raw.get("errors") or []
    failed = raw.get("failed_item_ids")
    if failed is None:
        failed = raw.get("failed_ids") or raw.get("item_ids") or []
    approved = raw.get("approved")
    if approved is None:
        approved = raw.get("ok")
    if approved is None:
        approved = raw.get("status") in ("approved", "valid", "ok")
    return AuditResult(
        approved=bool(approved),
        reasons=tuple(reasons if isinstance(reasons, list | tuple) else [reasons]),
        failed_item_ids=tuple(str(item) for item in failed),
        terminal=bool(raw.get("terminal")),
    )


async def _call(callback: Callable[..., Any], *args: Any) -> Any:
    result = callback(*args)
    return await result if inspect.isawaitable(result) else result


class RetryAuditPolicy:
    """Run initial production plus at most ``max_redos`` targeted repairs."""

    def __init__(self, db: Session | None = None, *, case_id: int | None = None,
                 production_id: int | None = None, language: str | None = None):
        self.db = db
        self.case_id = case_id
        self.production_id = production_id
        self.language = language

    def _record(self, stage: str, decision: str, reason: str, context: dict[str, Any]) -> None:
        if self.db is None:
            return
        record_decision(
            self.db,
            PolicyDecision(
                policy="retry_audit",
                decision=decision,
                reason=reason,
                rule_id=f"retry.{stage}",
                severity="info" if decision == "approved" else "warning",
                recoverable=decision not in ("stopped", "omitted"),
                actions=("continue",) if decision == "approved" else ("redo", "review"),
                context=context,
            ),
            case_id=self.case_id,
            production_id=self.production_id,
            language=self.language,
        )

    async def run_with_audit(self, produce: Produce, audit: Audit, max_redos: int,
                             on_exhausted: ExhaustedAction | str, *, stage: str = "stage"
                             ) -> StageOutcome:
        action = ExhaustedAction(on_exhausted)
        feedback: dict[str, Any] = {"reasons": [], "failed_item_ids": []}
        history: list[dict[str, Any]] = []
        attempts = max(0, int(max_redos)) + 1
        for attempt in range(1, attempts + 1):
            produced = await _call(produce, feedback)
            result = _audit_result(await _call(audit, produced))
            entry = {
                "attempt": attempt,
                "reasons": list(result.reasons),
                "failed_item_ids": list(result.failed_item_ids),
                "approved": result.approved,
            }
            history.append(entry)
            self._record(stage, "approved" if result.approved else "rejected",
                         "audit passed" if result.approved else "audit rejected",
                         {"attempt": attempt, "reasons": list(result.reasons),
                          "failed_item_ids": list(result.failed_item_ids)})
            if result.approved:
                return StageOutcome("approved", attempt, result.reasons,
                                    result.failed_item_ids, tuple(history))
            feedback = {"reasons": list(result.reasons),
                        "failed_item_ids": list(result.failed_item_ids)}
            if result.terminal:
                break

        status = {ExhaustedAction.STOP: "stopped", ExhaustedAction.OMIT: "omitted",
                  ExhaustedAction.DEGRADE: "degraded"}[action]
        reason = "; ".join(str(r) for r in feedback["reasons"]) or "audit rejected"
        used_attempts = len(history)
        self._record(stage, status, reason, {"attempts": used_attempts,
                                             "reasons": feedback["reasons"],
                                             "failed_item_ids": feedback["failed_item_ids"]})
        return StageOutcome(status, used_attempts, tuple(feedback["reasons"]),
                            tuple(feedback["failed_item_ids"]), tuple(history))
