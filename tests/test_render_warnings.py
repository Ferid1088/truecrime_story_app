from app.documentary.render import engine
from app.documentary.jobs import status_for_result


def test_failed_channel_intro_returns_reason_without_failing_render(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("intro generator unavailable")

    monkeypatch.setattr("app.documentary.intros.ensure_intro", fail)
    intro, reason = engine._channel_intro("fa", 160, 90, 10)
    assert intro is None
    assert reason == "RuntimeError: intro generator unavailable"
    assert status_for_result({"warnings": [{"stage": "render", "reason": reason}]}) == \
        "completed_warnings"
    assert status_for_result({"errors": {"fa": "failed"}, "warnings": []}) == "partial"
