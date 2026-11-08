import json

import pytest
from conftest import _delete_case_tree

from app.autopilot import service as SV
from app.autopilot import state as S
from app.core.ai_config import ai_config
from app.db.models import AutopilotRun, Case, DocumentaryJob, StoryVersion

LANGS = ["en", "de", "fa", "ar"]


@pytest.fixture
def case(db_session):
    for stale in db_session.query(Case).filter_by(slug="autopilot-service-case").all():
        _delete_case_tree(db_session, stale.id)
    row = Case(canonical_title="Autopilot service case", slug="autopilot-service-case", language="en")
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.expire_all()
    _delete_case_tree(db_session, row.id)


def make_run(db, case, languages=LANGS) -> AutopilotRun:
    run = AutopilotRun(case_id=case.id, languages_json=json.dumps(languages), steps_json=json.dumps(S.initial_steps()))
    db.add(run)
    db.commit()
    return run


def test_the_estimate_is_printed_for_the_chosen_languages_of_a_case(db_session, case):
    est = SV.estimate_for_case(db_session, case, LANGS)
    assert est["languages"] == LANGS
    assert est["cap_eur"] == ai_config.budget.total_cap_eur
    assert est["estimated_eur"] > 0 and est["range_eur"]["high"] >= est["range_eur"]["low"]


def test_english_is_required_and_unknown_languages_are_refused():
    with pytest.raises(SV.StartError, match="English is required"):
        SV.check_languages(["de", "fa"])
    with pytest.raises(SV.StartError, match="Not a language"):
        SV.check_languages(["en", "xx"])
    assert SV.check_languages(["tr", "en", "fa"]) == ["en", "fa", "tr"]


def test_the_runs_steps_are_the_case_opening_and_every_phase(db_session, case):
    run = make_run(db_session, case)
    assert [s["name"] for s in S.steps(run)] == ["case", *S.PHASES]


def test_cancel_stops_the_run_and_its_active_films(db_session, case):
    run = make_run(db_session, case)
    job = DocumentaryJob(case_id=run.case_id, mode="full", status="running", languages_json='["en"]')
    db_session.add(job)
    db_session.commit()
    run.film_job_ids_json = json.dumps({"en": job.id})
    db_session.commit()
    SV.cancel_run(db_session, run)
    db_session.refresh(job)
    assert run.status == "cancelled" and job.status == "cancelling"


def test_the_view_shows_languages_spend_against_the_cap_and_the_needs_you_list(db_session, case):
    run = make_run(db_session, case)
    S.add_need(run, "k", "film_stopped", "The de film stopped.", language="de")
    db_session.commit()
    view = SV.run_view(db_session, run)
    assert view["id"] == run.id and view["case"]["id"] == run.case_id
    assert view["budget"]["cap_eur"] == ai_config.budget.total_cap_eur
    assert view["budget"]["spent_eur"] == 0
    assert set(view["languages"]) == set(LANGS)
    assert view["needs_you"][0]["message"] == "The de film stopped."
    assert view["publish_requests"] == 0


def test_the_texts_step_detail_is_recomputed_live_not_frozen_at_the_last_autopilot_pass(db_session, case):
    """Case 15: manual /revise and /improve rounds moved English forward long after the autopilot's
    own attempts were exhausted; the stored step detail stayed frozen at the last pass while the real
    state kept changing underneath it. run_view() must recompute it from the current StoryVersion,
    not trust the stored snapshot, whenever the step is not settled."""
    run = make_run(db_session, case)
    S.set_step(run, "texts", "waiting", "stale detail from hours ago")
    db_session.commit()
    final = StoryVersion(case_id=run.case_id, kind="final", language="en", version=1,
                         narrative_angle="{}", story_text="[[ACT:act1]]\n\ntext", status="open_issues",
                         narrative_structure="{}",
                         critic_notes=json.dumps({"checks": {"open_issues": [
                             {"severity": "high", "kind": "unsupported", "problem": "invented detail"}]}}),
                         text_hash="h1")
    db_session.add(final)
    db_session.commit()

    texts_step = next(s for s in SV.run_view(db_session, run)["steps"] if s["name"] == "texts")
    assert "stale detail" not in texts_step["detail"]
    assert "invented detail" in texts_step["detail"]
    for lang in ("de", "fa", "ar"):
        assert f"{lang}: not yet approved" in texts_step["detail"]
