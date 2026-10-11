import json

from app.core.policy import PolicyDecision
from app.db.models import OverrideLog, PolicyEvent
from app.services.policy_events import record_decision, record_override, revert_override


def test_policy_events_and_overrides_redact_context_and_log(db_session, caplog):
    decision = PolicyDecision(
        "production", "degraded", "asset omitted", "MEDIA_INVALID", "error", False,
        ("review", "omit"), {"absolute_path": "/Users/secret/file.mp4", "email": "x@y.test"},
    )
    event = record_decision(db_session, decision, case_id=4, language="fa")
    context = json.loads(event.context_json)
    assert context == {"absolute_path": "<redacted-path>", "email": "<redacted>"}
    assert "policy_event" in caplog.text and "context_json" in caplog.text

    override = record_override(db_session, actor="editor", scope_type="asset", scope_id="V1",
                               field="title", old_value={"name": "old"},
                               new_value={"name": "new"}, reason="corrected label")
    assert db_session.get(OverrideLog, override.id).old_value_json == '{"name": "<redacted>"}'
    reverted = revert_override(db_session, override.id, reverted_by="editor")
    assert reverted.reverted_at is not None and reverted.reverted_by == "editor"
    assert db_session.get(PolicyEvent, event.id) is not None
