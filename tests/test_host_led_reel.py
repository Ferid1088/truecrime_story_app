import pytest

from app.core.ai_config import ai_config
from app.shortform.host_led import (
    HookRejected,
    ReelTimingError,
    build_timing,
    next_variant,
    validate_hook_text,
    variant_sequence,
)


def test_valid_timing_within_caps():
    t = build_timing(hook_seconds=6, film_cut_seconds=35, outro_seconds=6)
    assert t.avatar_seconds == 12
    assert t.total_seconds <= 60


def test_avatar_cap_exceeded_rejected(monkeypatch):
    cfg = ai_config.shorts_host_led
    monkeypatch.setattr(cfg, "avatar_max_seconds", cfg.hook_seconds_max + cfg.outro_seconds_max - 1)
    with pytest.raises(ReelTimingError, match="avatar time"):
        build_timing(hook_seconds=cfg.hook_seconds_max, film_cut_seconds=20,
                     outro_seconds=cfg.outro_seconds_max)


def test_hard_duration_cap_exceeded_rejected():
    with pytest.raises(ReelTimingError):
        build_timing(hook_seconds=6, film_cut_seconds=55, outro_seconds=6)


def test_hook_out_of_range_rejected():
    with pytest.raises(ReelTimingError):
        build_timing(hook_seconds=2, film_cut_seconds=35, outro_seconds=6)


def test_hook_word_limit():
    long_hook = " ".join(["word"] * 25)
    with pytest.raises(HookRejected):
        validate_hook_text(long_hook, "en")


def test_hook_banned_greeting_rejected():
    with pytest.raises(HookRejected):
        validate_hook_text("Hi everyone, let's get into it.", "en")


def test_hook_ok_passes_through():
    assert validate_hook_text("One detail changes the whole timeline.", "en")


def test_variant_never_repeats_consecutively():
    available = [("stand", "jacket", "front"), ("sit", "coat", "side")]
    seq = variant_sequence(available, 5)
    for a, b in zip(seq, seq[1:], strict=False):
        assert a != b


def test_variant_sequence_cycles_through_registry():
    available = [("a", "a", "a"), ("b", "b", "b"), ("c", "c", "c")]
    seq = variant_sequence(available, 6)
    assert set(seq) == set(available)


def test_single_variant_raises_instead_of_silently_repeating():
    available = [("only", "one", "combo")]
    with pytest.raises(ValueError):
        next_variant(available, ("only", "one", "combo"), 1)


def test_hook_camera_zoom_end_is_a_modest_push_in():
    """Owner feedback 2026-10-20: the hook needs a subtle camera move, not too fast or slow.
    The configured end scale must sit in a sane Ken-Burns range (never a no-op, never jarring)."""
    from app.core.ai_config import ai_config

    zoom = ai_config.shorts_host_led.hook_camera_zoom_end
    assert 1.0 < zoom <= 1.3, "zoom should be a visible but gentle push-in, not absent or extreme"


def test_end_card_seconds_matches_owner_approved_value():
    from app.core.ai_config import ai_config

    assert ai_config.shorts_host_led.end_card_seconds == 4.0
