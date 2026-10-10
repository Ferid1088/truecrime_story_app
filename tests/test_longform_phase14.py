"""Phase 14 publisher boundary and credential-isolation checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.shortform.operations import (
    ExportPackagePublisher,
    PlatformExport,
    transition_publish_status,
)
from app.shortform.publishers import (
    FacebookReelsPublisher,
    InstagramReelsPublisher,
    TikTokPublisher,
    YouTubeShortsPublisher,
)


EXPORT = PlatformExport(
    concept_id="C6-S01", platform="youtube_short", language="en", duration=35,
    cta="Follow the timeline.", destination="episode://case-6", campaign_id="C6-S01-youtube",
)


def test_export_package_is_working_but_all_outward_statuses_need_approval():
    publisher = ExportPackagePublisher()
    with pytest.raises(PermissionError):
        publisher.publish(EXPORT, approved=False)
    assert publisher.publish(EXPORT, approved=True) == "exported"

    with pytest.raises(PermissionError):
        transition_publish_status("approved", "scheduled", human_approved=False)
    with pytest.raises(PermissionError):
        transition_publish_status("review", "publishing", human_approved=True)
    assert transition_publish_status("approved", "scheduled", human_approved=True) == "scheduled"
    assert transition_publish_status("approved", "publishing", human_approved=True) == "publishing"


def test_platform_publishers_are_explicit_non_posting_stubs():
    for publisher in (YouTubeShortsPublisher(), InstagramReelsPublisher(), FacebookReelsPublisher(), TikTokPublisher()):
        with pytest.raises(NotImplementedError, match="stub"):
            publisher.publish(EXPORT, approved=True)


def test_generation_and_director_code_contain_no_credential_access_or_literals():
    root = Path(__file__).resolve().parents[1]
    files = list((root / "app" / "shortform").rglob("*.py"))
    forbidden = ("os.environ", "SECRET_KEY", "secret_key")
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not any(token in text for token in forbidden), path
        if path.name != "publishers.py":
            assert not any(token in text for token in ("API_KEY", "api_key", "api-key")), path
