import pytest

from app.shortform.operations import (
    ExportPackagePublisher, build_publish_plan, derive_variants, metrics_summary,
    repetition_check, resolve_voice_ids,
)


def test_voice_ids_resolve_from_central_config():
    ids = resolve_voice_ids()
    assert set(ids) == {"en", "de", "fa", "ar"}
    assert all(ids.values())


def test_variants_share_campaign_contract_but_keep_platform_settings():
    variants = derive_variants("c1", "en", "Watch", "episode://1")
    assert variants
    assert {v.platform for v in variants} >= {"youtube_short", "tiktok_video"}
    assert all(v.disclosure_required and v.campaign_id for v in variants)


def test_publisher_requires_human_approval():
    publisher = ExportPackagePublisher()
    with pytest.raises(PermissionError):
        publisher.publish(derive_variants("c1", "en", "Watch", "episode://1")[0], False)
    assert publisher.publish(derive_variants("c1", "en", "Watch", "episode://1")[0], True) == "exported"


def test_publish_plan_respects_window_and_counts():
    plan = build_publish_plan(__import__("datetime").date.today(), {"youtube_short": 2, "tiktok_video": 2})
    assert len(plan) == 4
    assert all(slot.approved_only for slot in plan)


def test_repetition_is_rejected_and_metrics_do_not_invent_conversion():
    ok, reasons = repetition_check([{"cta": "Watch", "hook_structure": "question"},
                                    {"cta": "Watch", "hook_structure": "question"}])
    assert not ok and reasons
    summary = metrics_summary([{"platform": "tiktok_video", "views": 10_000}])
    assert summary["conversion_claim"] is None
    assert summary["attribution_status"] == "unavailable"
