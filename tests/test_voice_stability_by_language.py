"""Stability 0.2 is the voice's setting for fa, en, de and ar; speed and post-speed of 1.2 / 1.1 belong to fa alone."""

import pytest

from app.core.ai_config import ai_config


@pytest.mark.parametrize("language", ["fa", "en", "de", "ar"])
def test_stability_is_02_in_every_narration_style(language):
    for style in ai_config.voice.styles:
        if language != "fa" and style == ai_config.host.voice_style:
            continue                    # the Host's voice is not touched outside fa
        assert ai_config.voice.settings_for(style, language)["stability"] == 0.2


@pytest.mark.parametrize("language", ["en", "de", "ar"])
def test_other_languages_keep_their_own_speed(language):
    for style, base in ai_config.voice.styles.items():
        assert ai_config.voice.settings_for(style, language)["speed"] == base.speed
    assert language not in ai_config.voice.post_speed
    required = ai_config.voice.review_required[language]
    assert (required.speed, required.stability, required.post_speed) == (None, 0.2, 1.0), "a block voiced in the review is refused at another stability or post speed"


def test_only_fa_has_the_fast_pace():
    assert ai_config.voice.settings_for("factual", "fa")["speed"] == 1.2
    assert ai_config.voice.post_speed == {"fa": 1.1}
