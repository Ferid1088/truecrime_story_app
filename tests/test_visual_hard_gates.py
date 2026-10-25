"""Regression tests for hard visual admission and configured rights enforcement.

This guards a safety-critical boundary, not a substitute for end-to-end
media-rights or semantic scene verification.
"""
from types import SimpleNamespace

from app.core.ai_config import ai_config
from app.documentary.visuals.director import usable


def _asset(status: str, rights: str = "public_domain"):
    return SimpleNamespace(
        verification_status=status,
        rights_status=rights,
        local_path="media/example.jpg",
        provider="wikimedia",
        asset_type="image",
    )


def test_visual_rights_enforcement_is_enabled():
    assert ai_config.rights.enforce is True


def test_visual_director_rejects_unknown_or_pending_verification():
    for status in ("unverified", "needs_review", "rejected", "error"):
        assert usable(_asset(status), "publish") is False


def test_visual_director_does_not_accept_missing_local_media():
    candidate = _asset("verified")
    candidate.local_path = None
    assert usable(candidate, "publish") is False
