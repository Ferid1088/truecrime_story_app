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
