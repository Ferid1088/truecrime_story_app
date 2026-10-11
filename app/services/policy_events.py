"""Persistence for policy decisions and explicit human overrides."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import PurePath
from typing import Any

from sqlalchemy.orm import Session

from app.core.policy import PolicyDecision
from app.db.models import OverrideLog, PolicyEvent
from app.utils import utc_now

log = logging.getLogger(__name__)
_SENSITIVE_KEY_PARTS = ("email", "phone", "address", "name", "person", "token", "secret")


def _safe(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(k): _safe(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v, key) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and os.path.isabs(value):
            return "<redacted-path>"
        return value
    if isinstance(value, PurePath):
        return "<redacted-path>"
    return str(value)


def _json(value: Any) -> str:
    return json.dumps(_safe(value), ensure_ascii=False, sort_keys=True, default=str)


def record_decision(db: Session, decision: PolicyDecision, *, case_id: int | None = None,
                    production_id: int | None = None, language: str | None = None) -> PolicyEvent:
    context = _safe(decision.context)
    row = PolicyEvent(
        case_id=case_id, production_id=production_id, language=language,
        policy=decision.policy, rule_id=decision.rule_id, decision=decision.decision,
        reason=_safe(decision.reason), context_json=_json(context),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _log_event(row)
    return row


def record_override(db: Session, *, actor: str, scope_type: str, scope_id: str,
                    field: str, old_value: Any, new_value: Any, reason: str) -> OverrideLog:
    row = OverrideLog(
        actor=_safe(actor), scope_type=_safe(scope_type), scope_id=_safe(scope_id),
        field=_safe(field), old_value_json=_json(old_value), new_value_json=_json(new_value),
        reason=_safe(reason),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("policy_override %s", json.dumps({
        "id": row.id, "created_at": row.created_at.isoformat(), "actor": row.actor,
        "scope_type": row.scope_type, "scope_id": row.scope_id, "field": row.field,
        "old_value_json": row.old_value_json, "new_value_json": row.new_value_json,
        "reason": row.reason, "reverted_at": None, "reverted_by": None,
    }, ensure_ascii=False, sort_keys=True))
    return row


def revert_override(db: Session, override_id: int, *, reverted_by: str) -> OverrideLog:
    row = db.get(OverrideLog, override_id)
    if row is None:
        raise LookupError(f"override {override_id} not found")
    row.reverted_at = utc_now()
    row.reverted_by = _safe(reverted_by)
    db.commit()
    db.refresh(row)
    log.info("policy_override_reverted %s", json.dumps({
        "id": row.id, "reverted_at": row.reverted_at.isoformat(),
        "reverted_by": row.reverted_by,
    }, ensure_ascii=False, sort_keys=True))
    return row


def _log_event(row: PolicyEvent) -> None:
    log.info("policy_event %s", json.dumps({
        "id": row.id, "created_at": row.created_at.isoformat(), "case_id": row.case_id,
        "production_id": row.production_id, "language": row.language,
        "policy": row.policy, "rule_id": row.rule_id, "decision": row.decision,
        "reason": row.reason, "context_json": row.context_json,
    }, ensure_ascii=False, sort_keys=True))
