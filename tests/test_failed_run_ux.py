from app.documentary.jobs import failure_details, plain_failure


def test_failure_reason_is_plain_language():
    assert plain_failure(RuntimeError("research input is empty")) == "research input is empty"


def test_no_relevant_research_disables_resume_with_next_action():
    details = failure_details("No relevant sources found for 'Example'. Add details.")
    assert details["resume_allowed"] is False
    assert "start a new run" in details["next_action"]
    assert details["resume_disabled_reason"]


def test_transient_failure_can_resume():
    details = failure_details("search service unavailable")
    assert details["resume_allowed"] is True


def test_awaiting_outfit_is_resumable_with_clear_next_action():
    details = failure_details(
        "no outfit is selected for case 9 in de", error_code="awaiting_outfit")
    assert details["resume_allowed"] is True
    assert details["resume_disabled_reason"] is None
    assert "outfit" in details["next_action"].lower()


def test_no_outfit_raises_awaiting_outfit_not_generic_error(db_session):
    """The avatar step waits for the owner's outfit choice — never a silent default."""
    import uuid

    from app.db.models import Case, HostScene
    from app.documentary.host_scenes import look_for_avatar
    from app.documentary.pipeline.errors import AwaitingOutfit

    case = Case(canonical_title="outfit wait test", slug=f"outfit-wait-{uuid.uuid4().hex[:8]}")
    db_session.add(case)
    db_session.commit()
    # not persisted: the missing-outfit check only needs the case/language on the object, and a
    # stray row would collide with real HostSegments ids other tests create
    scene = HostScene(case_id=case.id, language="en")
    try:
        look_for_avatar(db_session, scene)
        raise AssertionError("expected AwaitingOutfit")
    except AwaitingOutfit as e:
        assert "outfit" in str(e).lower()
        assert "Outfits panel" in str(e)
