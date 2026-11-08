"""The production choices made on the case page: languages and outfits are saved first, then the Autopilot button starts exactly those
languages, and only once the case has an approved name. A language the owner skipped is never produced."""

from __future__ import annotations

import json

import pytest
from conftest import _delete_case_tree

from app.autopilot import production as PR
from app.autopilot import service as SV
from app.db.models import AutopilotRun, Case, CaseProduction, EpisodeIdentity
from app.documentary import outfits as OUT
from app.documentary.outfits import OutfitRegistry


@pytest.fixture(autouse=True)
def no_launch(monkeypatch):
    monkeypatch.setattr(SV, "launch", lambda run_id: None)


@pytest.fixture
def case(db_session):
    for stale in db_session.query(Case).filter_by(slug="case-production-test").all():
        _delete_case_tree(db_session, stale.id)
    row = Case(canonical_title="Production case", slug="case-production-test", language="en")
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.expire_all()
    _delete_case_tree(db_session, row.id)


def approve_name(db, case, language="en"):
    db.add(EpisodeIdentity(case_id=case.id, channel_id=language, language=language, editorial_title="A name", title_approved=True))
    db.commit()


def choose_outfit(db, case, language):
    ref = OutfitRegistry().all(language)[0].outfit_id
    outfit = OUT.create_outfit(db, case.id, language, "Outfit", outfit_ref=ref)
    OUT.select_outfit(db, outfit)
    return outfit


def test_a_fresh_case_shows_the_default_languages_unsaved_and_why_it_cannot_start(db_session, case):
    view = PR.view(db_session, case)
    assert view["saved"] is False and view["name"]["approved"] is False
    assert view["languages"] == ["en", "de", "fa", "ar"] and view["required_language"] == "en"
    assert view["allowed_languages"] == ["en", "de", "fa", "ar", "es", "it", "fr", "pt", "tr"]
    assert any("Approve the case's English name" in b for b in view["blockers"])


def test_saving_keeps_the_chosen_languages_only_and_english_is_required(db_session, case):
    with pytest.raises(SV.StartError, match="English is required"):
        PR.save(db_session, case, ["de", "fa"])
    with pytest.raises(SV.StartError, match="Not a language"):
        PR.save(db_session, case, ["en", "xx"])
    view = PR.save(db_session, case, ["fa", "en", "tr"])
    assert view["saved"] is True and view["languages"] == ["en", "fa", "tr"]
    assert json.loads(db_session.get(CaseProduction, case.id).languages_json) == ["en", "fa", "tr"]


def test_the_autopilot_waits_for_an_approved_name_the_saved_languages_and_an_outfit_for_each(db_session, case):
    with pytest.raises(SV.StartError, match="English name"):
        PR.start(db_session, case)
    approve_name(db_session, case)
    with pytest.raises(SV.StartError, match="Save the languages"):
        PR.start(db_session, case)
    PR.save(db_session, case, ["en", "de"])
    with pytest.raises(SV.StartError, match="host outfit for: en, de"):
        PR.start(db_session, case)
    choose_outfit(db_session, case, "en")
    with pytest.raises(SV.StartError, match="host outfit for: de"):
        PR.start(db_session, case)
    choose_outfit(db_session, case, "de")
    assert PR.blockers(db_session, case) == []


def test_the_run_covers_exactly_the_saved_languages_and_a_second_press_is_refused(db_session, case):
    approve_name(db_session, case)
    PR.save(db_session, case, ["en", "fa"])
    for language in ("en", "fa"):
        choose_outfit(db_session, case, language)
    run = PR.start(db_session, case)
    assert json.loads(run.languages_json) == ["en", "fa"] and run.status == "running" and run.candidate_id is None
    assert db_session.query(AutopilotRun).filter_by(case_id=case.id).count() == 1
    with pytest.raises(SV.StartError, match="already going"):
        PR.start(db_session, case)
    with pytest.raises(SV.StartError, match="fixed until it ends"):
        PR.save(db_session, case, ["en", "de", "fa"])
    assert PR.view(db_session, case)["run"]["id"] == run.id


def test_outfits_are_saved_with_the_languages_and_only_for_the_saved_ones(db_session, case):
    PR.save(db_session, case, ["en"], outfits={"tr": "registry:tr/navySuit"})        # tr is not saved: its outfit is ignored
    assert OUT.selected_outfit(db_session, case.id, "tr") is None
    assert PR.view(db_session, case)["languages"] == ["en"]


def test_the_production_endpoints_show_save_and_refuse(client, case):
    assert client.get(f"/api/cases/{case.id}/production").json()["saved"] is False
    r = client.put(f"/api/cases/{case.id}/production", json={"languages": ["de"]})
    assert r.status_code == 409 and "English" in r.json()["detail"]
    r = client.put(f"/api/cases/{case.id}/production", json={"languages": ["en", "es"]})
    assert r.status_code == 200 and r.json()["languages"] == ["en", "es"]
    r = client.post(f"/api/cases/{case.id}/production/autopilot")
    assert r.status_code == 409 and "English name" in r.json()["detail"]
    assert client.get("/api/cases/999999/production").status_code == 404


def test_a_language_whose_library_has_no_usable_outfit_shows_no_choices_instead_of_breaking_the_page(db_session, case, monkeypatch):
    import app.documentary.outfits as module

    original = module.OutfitRegistry.all

    def refuse(self, language=None):
        if language == "fr":
            raise ValueError("no outfit directories found")
        return original(self, language)

    monkeypatch.setattr(module.OutfitRegistry, "all", refuse)
    rows = {row["language"]: row for row in PR.view(db_session, case)["outfits"]}
    assert rows["fr"]["options"] == [] and rows["en"]["options"]

