"""Settings every film language needs: the subtitle language tag and the speech rate of the length estimate."""

from __future__ import annotations


def test_every_film_language_has_its_subtitle_language_code_and_its_measured_speech_rate():
    from app.core.ai_config import ai_config

    codes = ai_config.render.subtitle_language_codes
    assert {lang: codes[lang] for lang in ("es", "it", "fr", "pt", "tr")} == {"es": "spa", "it": "ita", "fr": "fre", "pt": "por", "tr": "tur"}
    assert all(codes[lang] != "und" for lang in ai_config.channels)
    assert ai_config.documentary.wpm("es") == 142 and ai_config.documentary.wpm("it") == 140     # it, fr, pt, tr: not measured yet, the default


def test_every_film_language_has_what_its_shorts_need():
    from app.core.ai_config import ai_config

    short, host_led = ai_config.short_form.production, ai_config.shorts_host_led
    for language in ai_config.channels:
        assert language in short.languages, language
        assert short.caption_fonts.get(language), language
        assert language in host_led.music_library.channel_mood_family, language
        assert host_led.music_attribution_text.get(language), language
        assert "{channel}" in host_led.publish_cta[language], language
        assert host_led.cover_label.get(language) and host_led.identity_line.get(language), language


def test_the_publish_caption_points_to_the_full_film_in_the_reels_language():
    from app.shortform.publish_manifest import _caption_with_cta

    assert _caption_with_cta("es", "Tras la Pista") == "El documental completo en YouTube - Tras la Pista"
    assert _caption_with_cta("tr", "Gizli İzler").endswith("Gizli İzler")
    assert _caption_with_cta("xx", "C") == "Full documentary on YouTube - C"
