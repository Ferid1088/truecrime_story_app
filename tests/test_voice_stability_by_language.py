"""Stability 0.2 is the voice's setting for fa, en and ar; German has its own (stability 0.7, speed 0.9); speed and post-speed of 1.2 / 1.1 belong to fa alone."""

import pytest

from app.core.ai_config import ai_config


@pytest.mark.parametrize("language", ["fa", "en", "ar"])
def test_stability_is_02_in_every_style_host_included(language):
    for style in ai_config.voice.styles:
        assert ai_config.voice.settings_for(style, language)["stability"] == 0.2


def test_german_has_stability_07_and_speed_09_in_every_style_host_included():
    for style in ai_config.voice.styles:
        settings = ai_config.voice.settings_for(style, "de")
        assert (settings["stability"], settings["speed"]) == (0.7, 0.9)
    required = ai_config.voice.review_required["de"]
    assert (required.speed, required.stability, required.post_speed) == (0.9, 0.7, 1.0), "a block voiced in the review is refused at another setting"
    assert "de" not in ai_config.voice.post_speed


@pytest.mark.parametrize("language", ["en", "ar"])
def test_other_languages_keep_their_own_speed(language):
    for style, base in ai_config.voice.styles.items():
        if style == ai_config.host.voice_style:         # the host speaks at the story voice's pace (test_host_voice_settings)
            assert ai_config.voice.settings_for(style, language)["speed"] == ai_config.voice.settings_for(ai_config.voice.default_style, language)["speed"]
            continue
        assert ai_config.voice.settings_for(style, language)["speed"] == base.speed
    assert language not in ai_config.voice.post_speed
    required = ai_config.voice.review_required[language]
    assert (required.speed, required.stability, required.post_speed) == (None, 0.2, 1.0), "a block voiced in the review is refused at another stability or post speed"


def test_only_fa_has_the_fast_pace():
    assert ai_config.voice.settings_for("factual", "fa")["speed"] == 1.2
    assert ai_config.voice.post_speed == {"fa": 1.1}


def test_the_voice_config_has_no_key_the_app_ignores():
    """A style written into the wrong section (the host's style under asr_check) is ignored silently: the config must not carry such a key."""
    from app.core.ai_config import unknown_config_keys

    assert [k for k in unknown_config_keys() if k.startswith(("audio.json", "documentary.json", "story.json"))] == []
