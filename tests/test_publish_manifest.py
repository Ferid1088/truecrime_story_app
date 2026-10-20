import pytest

from app.shortform.publish_manifest import build_publish_manifest


def test_youtube_manifest_has_related_video_checklist_item():
    m = build_publish_manifest("youtube_short", film_id=8, short_id="SH_1", language="en")
    steps = {s["action"] for s in m.manual_steps}
    assert "set_related_video" in steps
    item = next(s for s in m.manual_steps if s["action"] == "set_related_video")
    assert item["short_id"] == "SH_1"
    assert item["film_id"] == 8


def test_instagram_manifest_never_promises_a_video_link():
    m = build_publish_manifest("instagram_reel", film_id=8, short_id="SH_1", language="en")
    assert "link" not in m.automatic
    assert any("never carry a tappable URL" in s["reason"] for s in m.manual_steps)


def test_tiktok_manifest_does_not_claim_a_caption_link():
    m = build_publish_manifest("tiktok_video", film_id=8, short_id="SH_1", language="en")
    assert any("never clickable" in s["reason"] for s in m.manual_steps)


def test_facebook_manifest_flags_the_claim_as_unverified():
    m = build_publish_manifest("facebook_reel", film_id=8, short_id="SH_1", language="en")
    assert any("not confirmed" in s["reason"] for s in m.manual_steps)


def test_caption_carries_channel_name_per_language():
    m = build_publish_manifest("youtube_short", film_id=8, short_id="SH_1", language="fa")
    assert "رد خاموش" in m.caption


def test_unknown_platform_rejected():
    with pytest.raises(ValueError):
        build_publish_manifest("snapchat", film_id=8, short_id="SH_1", language="en")


def test_no_music_attribution_when_music_not_used():
    m = build_publish_manifest("youtube_short", film_id=8, short_id="SH_1", language="en")
    assert m.music_attribution is None
    assert "ElevenLabs" not in m.caption


def test_music_attribution_rides_along_when_music_is_used():
    m = build_publish_manifest("youtube_short", film_id=8, short_id="SH_1", language="en",
                               uses_generated_music=True)
    assert m.music_attribution == "Music created in collaboration with ElevenLabs"
    assert "ElevenLabs" in m.caption


def test_generated_music_is_flagged_synthetic_in_disclosure():
    from app.shortform.publish_manifest import build_publish_manifest

    m = build_publish_manifest("youtube_short", film_id=8, short_id="s1", language="en",
                               uses_generated_music=True)
    assert m.disclosure["synthetic_music"] is True
    assert m.disclosure["synthetic_avatar_voice_and_video"] is True
    assert any(step["action"] == "set_ai_disclosure_toggle" for step in m.manual_steps)


def test_no_generated_music_is_not_flagged_synthetic():
    from app.shortform.publish_manifest import build_publish_manifest

    m = build_publish_manifest("youtube_short", film_id=8, short_id="s1", language="en",
                               uses_generated_music=False)
    assert m.disclosure["synthetic_music"] is False
    assert m.disclosure["synthetic_avatar_voice_and_video"] is True
