import pytest

from app.services.upload_adapter import (
    PartialUpload,
    QuotaExceeded,
    RecordedYouTubeAdapter,
    TokenExpired,
)


def test_recorded_youtube_adapter_handles_oauth_languages_and_success():
    adapter = RecordedYouTubeAdapter(["success"])
    connection = adapter.connect(language="en", authorization_code="recorded-code")
    receipt = adapter.upload(bundle_id=7, bundle_hash="a" * 64, visibility="private",
                             schedule_at="2030-01-01T00:00:00Z")
    assert connection.channel_id == "recorded-en"
    assert receipt.visibility == "private"
    assert receipt.scheduled_at


@pytest.mark.parametrize("response, error", [
    ("quota_exceeded", QuotaExceeded),
    ("token_expired", TokenExpired),
])
def test_recorded_upload_failures_are_retryable(response, error):
    adapter = RecordedYouTubeAdapter([response])
    with pytest.raises(error) as exc:
        adapter.upload(bundle_id=2, bundle_hash="b" * 64, visibility="draft")
    if response == "token_expired":
        assert exc.value.reconnect_required


def test_partial_upload_can_resume():
    adapter = RecordedYouTubeAdapter(["partial", "success"])
    with pytest.raises(PartialUpload) as exc:
        adapter.upload(bundle_id=3, bundle_hash="c" * 64, visibility="private")
    receipt = adapter.retry(upload_id=exc.value.upload_id, resume_token=exc.value.resume_token)
    assert receipt.upload_id.startswith("recorded-0-")
