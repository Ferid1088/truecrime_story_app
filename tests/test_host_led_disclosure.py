from app.shortform.host_led_disclosure import check_host_led_disclosure, host_led_assets


def test_avatar_always_needs_disclosure_even_without_music():
    result = check_host_led_disclosure(platform="youtube_short", disclosure_flags={})
    assert result.passed is False
    assert result.metadata["requires_disclosure"] is True


def test_disclosure_passes_once_the_platform_flag_is_set():
    result = check_host_led_disclosure(
        platform="youtube_short", disclosure_flags={"youtube_short": True})
    assert result.passed is True


def test_music_asset_only_added_when_music_is_actually_used():
    without_music = host_led_assets(uses_generated_music=False)
    with_music = host_led_assets(uses_generated_music=True)
    assert len(without_music) == 1
    assert len(with_music) == 2
    assert any(a.asset_id == "generated_music" for a in with_music)
    assert not any(a.asset_id == "generated_music" for a in without_music)


def test_non_disclosure_platform_is_not_blocked_by_missing_flag():
    # "unknown_platform" is not in the disclosure_checker's platforms_requiring set.
    result = check_host_led_disclosure(platform="unknown_platform", disclosure_flags={})
    assert result.passed is True
