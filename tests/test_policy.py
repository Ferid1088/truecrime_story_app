import asyncio
from dataclasses import FrozenInstanceError

from fastapi import Request

from app.core.policy import PolicyDecision, PolicyError, policy_exception_handler, warning_for


def test_policy_decision_is_immutable_and_json_serialisable():
    decision = PolicyDecision("production", "degraded", "missing intro", "INTRO_MISSING",
                              "warning", True, ("continue", "review"), {"language": "fa"})
    assert decision.to_dict()["actions"] == ["continue", "review"]
    try:
        decision.reason = "changed"
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("PolicyDecision must be immutable")


def test_error_catalog_and_warning_shape():
    error = PolicyError("MEDIA_INVALID", context={"asset_id": "V1"})
    assert error.structured() == {
        "code": "MEDIA_INVALID", "severity": "error", "recoverable": False,
        "actions": ["review", "omit"], "message": "Media Invalid",
        "context": {"asset_id": "V1"},
    }
    warning = warning_for("INTRO_MISSING", "intro unavailable", {"language": "de"})
    assert warning["code"] == "INTRO_MISSING" and warning["context"]["language"] == "de"


def test_fastapi_handler_returns_structured_policy_error():
    request = Request({"type": "http", "method": "GET", "path": "/"})
    response = asyncio.run(policy_exception_handler(request, PolicyError("AUDIO_PLAN_INVALID")))
    assert response.status_code == 409
    assert response.body and b'"code":"AUDIO_PLAN_INVALID"' in response.body
