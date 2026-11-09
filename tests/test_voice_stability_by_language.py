"""Stability 0.7 and speed 0.9 are the voice's setting for de, en and ar; Persian alone has stability 0.2, speed 1.2 and post-speed 1.1; German and Arabic are played 1.05 times faster after the voice (their pauses too)."""

import pytest

from app.core.ai_config import ai_config


def test_persian_has_stability_02_in_every_style_host_included():
    for style in ai_config.voice.styles:
        assert ai_config.voice.settings_for(style, "fa")["stability"] == 0.2


@pytest.mark.parametrize("language", ["de", "en", "ar"])
def test_german_english_and_arabic_have_stability_07_and_speed_09_in_every_style_host_included(language):
    for style in ai_config.voice.styles:
        settings = ai_config.voice.settings_for(style, language)
        assert (settings["stability"], settings["speed"]) == (0.7, 0.9)
    required = ai_config.voice.review_required[language]
    post = ai_config.voice.post_speed.get(language, 1.0)
    assert (required.speed, required.stability, required.post_speed) == (0.9, 0.7, post), "a block voiced in the review is refused at another setting"
    assert post == (1.0 if language == "en" else 1.05)


def test_only_fa_has_the_fast_pace():
    assert ai_config.voice.settings_for("factual", "fa")["speed"] == 1.2
    assert ai_config.voice.post_speed == {
        "fa": 1.1, "ar": 1.05, "de": 1.05, "es": 0.9,
        "it": 1.0, "fr": 1.0, "pt": 1.0, "tr": 1.0,
    }
    assert ai_config.voice.post_speed_gaps == ["ar", "de", "es"], "only calibrated pause speeds alter the planned gaps"


def test_the_voice_config_has_no_key_the_app_ignores():
    """A style written into the wrong section (the host's style under asr_check) is ignored silently: the config must not carry such a key."""
    from app.core.ai_config import unknown_config_keys

    assert [k for k in unknown_config_keys() if k.startswith(("audio.json", "documentary.json", "story.json"))] == []
