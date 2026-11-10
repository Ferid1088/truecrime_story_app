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


def _case_with_persian_performance(db_session):
    import json
    from uuid import uuid4

    from app.db.models import Case, StoryVersion, VoicePerformance

    case = Case(canonical_title="Reference", slug=f"ref-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.flush()
    version = StoryVersion(case_id=case.id, version=1, kind="spoken", language="fa", story_text="x", narrative_angle="a", status="ready")
    db_session.add(version)
    db_session.flush()
    beats = [{"beat_id": "B01", "paragraphs": [[{"speech": "یه شب، تو خونه بودم.", "tts": "[slowly] یه شب، تو خونه بودم."}, {"speech": "بعد در زدن."}]]}]
    db_session.add(VoicePerformance(case_id=case.id, story_version_id=version.id, language="fa", status="ok", performance_json=json.dumps({"beats": beats})))
    db_session.commit()
    return case


def test_the_arabic_director_is_told_to_use_the_persian_text_only_as_a_guide():
    from app.core.ai_config import ai_config
    from app.documentary.voice_performance import director_system

    cfg = ai_config.voice_performance
    assert cfg.punctuation_reference == {"ar": "fa"}
    assert "REFERENCE TEXT" in director_system("ar", cfg) and 'the language "fa"' in director_system("ar", cfg)
    assert "REFERENCE TEXT" not in director_system("de", cfg) and "REFERENCE TEXT" not in director_system("fa", cfg)


def test_the_reference_of_arabic_is_the_persian_spoken_text_without_tags(db_session):
    from app.documentary.voice_performance import reference_beats

    case = _case_with_persian_performance(db_session)
    assert reference_beats(db_session, case.id, "ar") == {"B01": "یه شب، تو خونه بودم. بعد در زدن."}
    assert reference_beats(db_session, case.id, "de") == {}, "a language without a reference gets none"


def test_arabic_has_no_reference_while_the_persian_version_has_no_performance(db_session):
    from uuid import uuid4

    from app.db.models import Case
    from app.documentary.voice_performance import reference_beats

    case = Case(canonical_title="No reference yet", slug=f"nr-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    assert reference_beats(db_session, case.id, "ar") == {}


def test_the_arabic_director_sends_the_reference_of_each_beat_and_the_others_none(db_session, monkeypatch):
    import asyncio
    import contextlib
    import json

    from app.agents import voice_performance as agent_module

    seen = []

    async def run_agent(name, gen, task, system=None):
        seen.append(json.loads(task.split("INPUT:\n", 1)[1]))
        return {"sentences": []}, object()

    monkeypatch.setattr(agent_module, "get_generation_provider", lambda: object())
    monkeypatch.setattr(agent_module, "run_agent", run_agent)
    monkeypatch.setattr(agent_module, "track_run", lambda *a, **k: contextlib.nullcontext(object()))
    monkeypatch.setattr(agent_module, "stamp_run", lambda *a, **k: None)
    director = agent_module.VoicePerformanceDirector()
    chunk = [{"i": 0, "beat_id": "B01", "speech": "جملة"}, {"i": 1, "beat_id": "B02", "speech": "أخرى"}]
    info = {"B01": {"level": 1, "peak": 2}, "B02": {"level": 1, "peak": 2}}

    asyncio.run(director._direct_chunk(db_session, 1, "ar", chunk, info, {"B01": "یه شب، تو خونه بودم."}))

    beats = {b["beat_id"]: b for b in seen[0]["beats"]}
    assert beats["B01"]["reference"] == "یه شب، تو خونه بودم." and "reference" not in beats["B02"]


def test_an_ellipsis_is_never_put_where_a_comma_was():
    from app.documentary.voice_performance import validate_line

    speech = "كانت هناك طفلتان في البيت، حين قُتلت أمهما."
    replaced, issues = validate_line(speech, "كانت هناك طفلتان في البيت... حين قُتلت أمهما.", 0, "ar")
    added, _ = validate_line(speech, "كانت هناك... طفلتان في البيت، حين قُتلت أمهما.", 0, "ar")
    assert replaced == speech and "ellipsis_replaces_a_mark" in issues
    assert added == "كانت هناك... طفلتان في البيت، حين قُتلت أمهما.", "an ellipsis next to the comma it keeps is allowed"
