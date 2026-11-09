"""The voice director punctuates every sentence it directs, with its language's own rules; the words never change."""


def test_every_language_is_punctuated_by_its_voice_director_with_its_own_rules():
    from app.core.ai_config import ai_config
    from app.documentary.voice_performance import director_system

    cfg = ai_config.voice_performance
    assert not hasattr(ai_config, "semantic_punctuation"), "no separate punctuation layer is left"
    for code in ("en", "de", "es", "it", "fr", "pt", "tr", "fa", "ar"):
        assert code in cfg.punctuation_languages and cfg.punctuation_notes[code]
        assert "PUNCTUATION FOR THE VOICE" in director_system(code, cfg)
    assert "mandatory grammar" in director_system("de", cfg)
    assert "FORBIDDEN" in director_system("fa", cfg) and "ezafe" in director_system("fa", cfg)
    assert "PUNCTUATION FOR THE VOICE" in director_system("ar", cfg) and "PUNCTUATION FOR THE VOICE" not in director_system("zz", cfg)


def test_the_director_keeps_a_comma_only_change_and_drops_one_that_changes_a_word():
    from app.documentary.voice_performance import validate_line

    speech = "So patrol officers came out that night."
    kept, _ = validate_line(speech, "So, patrol officers came out that night.", 0, "en")
    refused, issues = validate_line(speech, "So, the patrol officers came out that night.", 0, "en")
    assert kept == "So, patrol officers came out that night."
    assert refused == speech and issues


def test_every_punctuation_language_has_a_voice_model_the_director_works_for():
    from app.core.ai_config import ai_config

    for code in ai_config.voice_performance.punctuation_languages:
        assert ai_config.voice.languages[code].model_id in ai_config.voice.elevenlabs.audio_tag_models, f"{code} would get no punctuation at all"
