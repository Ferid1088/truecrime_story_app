import json

import pytest
from conftest import _delete_case_tree

from app.autopilot import service as SV
from app.autopilot import state as S
from app.core.ai_config import ai_config
from app.db.models import AutopilotRun, Case, DiscoveryCandidate, DocumentaryJob, StoryVersion
from app.services import research_jobs

LANGS = ["en", "de", "fa", "ar"]


@pytest.fixture(autouse=True)
def no_launch(monkeypatch):
    launched = []
    monkeypatch.setattr(SV, "launch", launched.append)
    return launched


@pytest.fixture
def candidate(db_session):
    job = research_jobs.create_job(db_session, job_type="discovery",
                                   input_data={"count": 1, "languages": ["en"]})
    row = DiscoveryCandidate(title="Autopilot service case", query="q", rationale="why",
                             fingerprint="fp-autopilot-service", state="suggested",
                             discovery_job_id=job.id, resolution_status="SOLVED")
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.expire_all()
    candidate_id, case_id = row.id, row.case_id
    if case_id:
        _delete_case_tree(db_session, case_id)
    db_session.query(DiscoveryCandidate).filter_by(id=candidate_id).delete()
    db_session.commit()


def test_the_estimate_is_printed_for_the_chosen_languages_without_creating_a_case(
        db_session, candidate):
    before = db_session.query(Case).count()
    est = SV.estimate_for_candidate(db_session, candidate, LANGS)
    assert est["languages"] == LANGS
    assert est["cap_eur"] == ai_config.budget.total_cap_eur
    assert est["estimated_eur"] > 0 and est["range_eur"]["high"] >= est["range_eur"]["low"]
    assert db_session.query(Case).count() == before


def test_start_opens_the_case_records_the_estimate_and_launches_the_run(
        db_session, candidate, no_launch):
    run = SV.start_run(db_session, candidate.id, LANGS)
    assert run.status == "running" and json.loads(run.languages_json) == LANGS
    assert json.loads(run.estimate_json)["languages"] == LANGS
    case = db_session.get(Case, run.case_id)
    assert case.canonical_title == "Autopilot service case"
    db_session.refresh(candidate)
    assert candidate.case_id == case.id and candidate.state == "accepted"
    assert [s["name"] for s in S.steps(run)] == ["case", *S.PHASES]
    assert S.step_status(run, "case") == "done"
    assert no_launch == [run.id]


def test_start_persists_one_outfit_choice_per_language_before_launch(
        db_session, candidate, no_launch):
    from app.documentary import outfits as OF
    from app.lifecycle.candidates import open_case_from_candidate
    from app.outfits.registry import OutfitRegistry

    case = open_case_from_candidate(db_session, candidate, "en")
    choices = {}
    for lang in LANGS:
        registered = OutfitRegistry().all(lang)[0]
        outfit = OF.create_outfit(
            db_session, case.id, lang, registered.label,
            reference_image=registered.reference_pose("SEATED").path,
        )
        choices[lang] = f"existing:{outfit.id}"
    run = SV.start_run(db_session, candidate.id, LANGS, choices, {})

    case_id = run.case_id
    assert all(OF.selected_outfit(db_session, case_id, lang) is not None for lang in LANGS)
    assert [OF.selected_outfit(db_session, case_id, lang).language for lang in LANGS] == LANGS


def test_english_is_required_and_unknown_languages_are_refused(db_session, candidate):
    with pytest.raises(SV.StartError, match="English"):
        SV.start_run(db_session, candidate.id, ["de", "fa"])
    with pytest.raises(SV.StartError, match="language"):
        SV.start_run(db_session, candidate.id, ["en", "xx"])


def test_an_archived_or_deleted_suggestion_cannot_be_started(db_session, candidate):
    candidate.state = "archived"
    db_session.commit()
    with pytest.raises(SV.StartError, match="archived"):
        SV.start_run(db_session, candidate.id, LANGS)


def test_a_second_start_while_a_run_is_active_is_refused(db_session, candidate):
    SV.start_run(db_session, candidate.id, LANGS)
    with pytest.raises(SV.StartError, match="already"):
        SV.start_run(db_session, candidate.id, LANGS)


def test_cancel_stops_the_run_and_its_active_films(db_session, candidate):
    run = SV.start_run(db_session, candidate.id, LANGS)
    job = DocumentaryJob(case_id=run.case_id, mode="full", status="running",
                         languages_json='["en"]')
    db_session.add(job)
    db_session.commit()
    run.film_job_ids_json = json.dumps({"en": job.id})
    db_session.commit()
    SV.cancel_run(db_session, run)
    db_session.refresh(job)
    assert run.status == "cancelled" and job.status == "cancelling"


def test_the_view_shows_languages_spend_against_the_cap_and_the_needs_you_list(
        db_session, candidate):
    run = SV.start_run(db_session, candidate.id, LANGS)
    S.add_need(run, "k", "film_stopped", "The de film stopped.", language="de")
    db_session.commit()
    view = SV.run_view(db_session, run)
    assert view["id"] == run.id and view["case"]["id"] == run.case_id
    assert view["budget"]["cap_eur"] == ai_config.budget.total_cap_eur
    assert view["budget"]["spent_eur"] == 0
    assert set(view["languages"]) == set(LANGS)
    assert view["needs_you"][0]["message"] == "The de film stopped."
    assert view["publish_requests"] == 0


def test_the_texts_step_detail_is_recomputed_live_not_frozen_at_the_last_autopilot_pass(
        db_session, candidate):
    """Case 15: manual /revise and /improve rounds moved English forward long after the autopilot's
    own attempts were exhausted; the stored step detail stayed frozen at the last pass while the real
    state kept changing underneath it. run_view() must recompute it from the current StoryVersion,
    not trust the stored snapshot, whenever the step is not settled."""
    run = SV.start_run(db_session, candidate.id, LANGS)
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


def test_no_run_row_survives_for_a_refused_start(db_session, candidate):
    before = db_session.query(AutopilotRun).count()
    with pytest.raises(SV.StartError):
        SV.start_run(db_session, candidate.id, ["fa"])
    assert db_session.query(AutopilotRun).count() == before


def test_a_suggestion_that_matches_an_existing_case_is_not_started(db_session, candidate):
    twin = Case(canonical_title=candidate.title, slug="autopilot-service-twin", language="en")
    db_session.add(twin)
    db_session.commit()
    try:
        with pytest.raises(SV.StartError, match="already exists"):
            SV.start_run(db_session, candidate.id, LANGS)
    finally:
        _delete_case_tree(db_session, twin.id)
