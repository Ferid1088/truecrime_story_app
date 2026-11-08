"""Settings every film language needs: the subtitle language tag and the speech rate of the length estimate."""

from __future__ import annotations


def test_every_film_language_has_its_subtitle_language_code_and_its_measured_speech_rate():
    from app.core.ai_config import ai_config

    codes = ai_config.render.subtitle_language_codes
    assert {lang: codes[lang] for lang in ("es", "it", "fr", "pt", "tr")} == {"es": "spa", "it": "ita", "fr": "fre", "pt": "por", "tr": "tur"}
    assert all(codes[lang] != "und" for lang in ai_config.channels)
    assert ai_config.documentary.wpm("es") == 142 and ai_config.documentary.wpm("it") == 140     # it, fr, pt, tr: not measured yet, the default
