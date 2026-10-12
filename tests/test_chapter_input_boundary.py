import json

from app.agents.chapters import ChapterWriter


def test_shared_chapter_context_has_no_per_language_inputs(db_session):
    from test_chapters import _setup

    case, master, bp = _setup(db_session)
    context = ChapterWriter().context(db_session, case, bp, master, ["en", "de"], {})
    encoded = json.dumps(context, ensure_ascii=False)
    assert "excerpts" not in context and "narration" not in encoded
    assert "languages" not in context
    assert "film_title" not in context


def test_chapter_card_payload_contains_only_its_language_excerpt(db_session):
    from test_chapters import _setup

    from app.db.models import StoryVersion

    case, master, bp = _setup(db_session)
    de = StoryVersion(
        case_id=case.id,
        language="de",
        kind="spoken",
        version=2,
        status="approved",
        narrative_angle="",
        story_text="DE ONLY",
        narrative_structure="{}",
    )
    db_session.add(de)
    db_session.commit()
    context = ChapterWriter().context(db_session, case, bp, master, ["de"], {"de": de})
    payload = ChapterWriter()._payload(context, "de", de)
    assert payload["language"] == "de"
    assert payload["narration_excerpt"] == "DE ONLY"
    assert "narration_excerpts" not in payload
    assert payload["narration_excerpt"] == "DE ONLY"
