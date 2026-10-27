"""O2: the Outfits panel — per-language outfit choice with approved-look thumbnails,
"same outfit for all languages" (A), "same picture for all languages" (B), "one fixed
picture" per language (C), and the stored-order picture rotation for appearances."""
import pytest

from app.core.ai_config import ai_config
from app.documentary import look_selection as LS
from app.documentary import outfits as OF


def _case(db):
    from test_blueprint_performance import _story

    return _story(db)[0]


def _outfit(db, case, language="en", ref=None, name="Navy suit", select=True):
    outfit = OF.create_outfit(db, case.id, language, name,
                              outfit_ref=ref or f"{language}/blazerJeans")
    return OF.select_outfit(db, outfit) if select else outfit


def _look(db, outfit, camera="CAM01_FRONT", framing="MEDIUM", stance="SEATED",
          status="approved", pose="P01_NEUTRAL_SEATED", heygen="hg"):
    look = OF.request_look(db, outfit, camera, framing, stance, pose)
    look.status = status
    look.heygen_look_id = heygen
    db.commit()
    return look


# ---------------------------------------------------------------------------
# A: same outfit for all languages
# ---------------------------------------------------------------------------

def test_same_outfit_copies_only_where_the_name_exists_and_lists_skipped(db_session):
    case = _case(db_session)
    en = _outfit(db_session, case, "en", name="Navy suit")
    de = _outfit(db_session, case, "de", name="Navy suit", select=False)
    _outfit(db_session, case, "fa", name="Grey", select=False)   # different name
    out = OF.apply_same_outfit(db_session, case.id, en.id)
    db_session.refresh(de)
    assert out["applied"] == ["de"] and "fa" in out["skipped"] and "ar" in out["skipped"]
    assert de.status == "selected"
    # no second selected outfit anywhere
    assert OF.selected_outfit(db_session, case.id, "en").id == en.id
    assert OF.selected_outfit(db_session, case.id, "de").id == de.id


def test_same_outfit_reports_when_no_other_language_has_the_name(db_session):
    case = _case(db_session)
    en = _outfit(db_session, case, "en", name="Only here")
    _outfit(db_session, case, "de", name="Grey", select=False)
    out = OF.apply_same_outfit(db_session, case.id, en.id)
    assert out["applied"] == [] and set(out["skipped"]) == {"de", "fa", "ar", "es", "tr"}
    assert out["reason"] == "no other language has an outfit with this name"


def test_same_outfit_never_touches_a_retired_or_other_cases_outfit(db_session):
    case = _case(db_session)
    en = _outfit(db_session, case, "en", name="Navy suit")
    retired = _outfit(db_session, case, "de", name="Navy suit", select=False)
    OF.retire_outfit(db_session, retired)
    other = _outfit(db_session, _case(db_session), "de", name="Navy suit", select=False)
    out = OF.apply_same_outfit(db_session, case.id, en.id)
    assert "de" in out["skipped"]           # retired does not count
    assert other.status == "draft"          # another case's outfit untouched


# ---------------------------------------------------------------------------
# C: one fixed picture
# ---------------------------------------------------------------------------

def test_a_fixed_picture_is_used_for_every_appearance(db_session):
    outfit = _outfit(db_session, _case(db_session))
    _look(db_session, outfit, "CAM01_FRONT", "WIDE", "SEATED", heygen="a")
    fixed = _look(db_session, outfit, "CAM04_LEFT_SIDE", "MEDIUM", "SEATED", heygen="b")
    OF.set_fixed_look(db_session, outfit, fixed.id)
    for n in range(4):
        got = LS.choose_appearance(db_session, outfit,
                                   LS.Wanted("SEATED", "CAM01_FRONT", "WIDE"), n)
        assert got["look"].id == fixed.id and got["choice"]["fixed"] is True


def test_a_fixed_picture_must_be_an_approved_look_of_that_outfit(db_session):
    case = _case(db_session)
    outfit = _outfit(db_session, case)
    pending = _look(db_session, outfit, status="generated")
    other = _outfit(db_session, case, ref="en/brownSweater", name="Grey", select=False)
    foreign = _look(db_session, other)
    with pytest.raises(OF.OutfitError):
        OF.set_fixed_look(db_session, outfit, pending.id)
    with pytest.raises(OF.OutfitError):
        OF.set_fixed_look(db_session, outfit, foreign.id)
    OF.set_fixed_look(db_session, outfit, None)          # clearing is always allowed
    assert outfit.fixed_look_id is None


def test_a_fixed_picture_that_lost_approval_falls_back_to_rotation_and_says_so(db_session):
    outfit = _outfit(db_session, _case(db_session))
    fixed = _look(db_session, outfit)
    spare = _look(db_session, outfit, "CAM02_LEFT_45", "MEDIUM", "SEATED", heygen="spare")
    OF.set_fixed_look(db_session, outfit, fixed.id)
    fixed.status = "rejected"
    db_session.commit()
    got = LS.choose_appearance(db_session, outfit, LS.Wanted("SEATED", "CAM02_LEFT_45", "MEDIUM"), 0)
    assert got["look"].id == spare.id and got["choice"].get("fixed_missing") is True


# ---------------------------------------------------------------------------
# B: same picture for all languages
# ---------------------------------------------------------------------------

def test_same_picture_applies_only_where_the_same_named_outfit_has_the_same_look(db_session):
    case = _case(db_session)
    en = _outfit(db_session, case, "en", name="Navy suit")
    de = _outfit(db_session, case, "de", name="Navy suit")
    fa = _outfit(db_session, case, "fa", name="Navy suit")
    _outfit(db_session, case, "ar", name="Grey")                       # no "Navy suit" in ar
    src = _look(db_session, en, "CAM01_FRONT", "WIDE", "SEATED", pose="P02_SLIGHT_FORWARD")
    match_de = _look(db_session, de, "CAM01_FRONT", "WIDE", "SEATED",
                     pose="P02_SLIGHT_FORWARD")
    _look(db_session, fa, "CAM04_LEFT_SIDE", "MEDIUM", "SEATED", pose="P04_ARMREST_RELAXED")
    out = OF.apply_same_picture(db_session, case.id, en.id, src.id)
    db_session.refresh(de)
    db_session.refresh(fa)
    assert de.fixed_look_id == match_de.id and out["applied"] == ["de"]
    assert "fa" in out["skipped"] and "ar" in out["skipped"]   # no matching look / no outfit
    assert fa.fixed_look_id is None


def test_same_picture_rejects_a_look_of_another_outfit(db_session):
    case = _case(db_session)
    en = _outfit(db_session, case, "en", name="Navy suit")
    other = _outfit(db_session, case, ref="en/brownSweater", name="Grey", select=False)
    foreign = _look(db_session, other)
    with pytest.raises(OF.OutfitError):
        OF.apply_same_picture(db_session, case.id, en.id, foreign.id)


# ---------------------------------------------------------------------------
# rotation: stored order, never twice in a row
# ---------------------------------------------------------------------------

def test_appearances_cycle_the_fitting_looks_in_stored_order_never_twice(db_session):
    outfit = _outfit(db_session, _case(db_session))
    a = _look(db_session, outfit, "CAM01_FRONT", "MEDIUM", "SEATED")
    _look(db_session, outfit, "CAM02_LEFT_45", "MEDIUM", "SEATED")
    _look(db_session, outfit, "CAM03_RIGHT_45", "MEDIUM", "SEATED")
    wanted = LS.Wanted("SEATED", "CAM01_FRONT", "MEDIUM")   # exact fit: a only — the
    # stage-director filter wins over rotation (b, c are different cameras, not fitting)
    prev = None
    for n in range(3):
        got = LS.choose_appearance(db_session, outfit, wanted, n, previous=prev)
        assert got["look"].id == a.id
        prev = got["look"]


def test_rotation_uses_the_look_order_and_wraps(db_session):
    outfit = _outfit(db_session, _case(db_session))
    a = _look(db_session, outfit, "CAM01_FRONT", "MEDIUM", "SEATED")
    # a wider approved picture also fits a MEDIUM plan (a crop of it): two fitting looks
    b = _look(db_session, outfit, "CAM01_FRONT", "WIDE", "SEATED")
    got = [LS.choose_appearance(db_session, outfit, LS.Wanted("SEATED", "CAM01_FRONT", "MEDIUM"), n)
           ["look"].id for n in (0, 1, 2, 3)]
    assert got == [a.id, b.id, a.id, b.id]      # stored order, cycling


def test_rotation_skips_the_previous_picture_when_two_fit(db_session):
    outfit = _outfit(db_session, _case(db_session))
    a = _look(db_session, outfit, "CAM01_FRONT", "MEDIUM", "SEATED")
    b = _look(db_session, outfit, "CAM01_FRONT", "WIDE", "SEATED")
    wanted = LS.Wanted("SEATED", "CAM01_FRONT", "MEDIUM")
    first = LS.choose_appearance(db_session, outfit, wanted, 0)["look"]
    assert first.id == a.id
    # appearance order wants a again at index 2 — never the same picture twice in a row
    second = LS.choose_appearance(db_session, outfit, wanted, 2, previous=first)["look"]
    assert second.id == b.id
    # with only one fitting look the same picture legitimately repeats
    lonely = _outfit(db_session, _case(db_session))
    only = _look(db_session, lonely)
    one = LS.choose_appearance(db_session, lonely, LS.Wanted("SEATED", "CAM01_FRONT", "MEDIUM"),
                               1, previous=only)["look"]
    assert one.id == only.id


def test_the_stage_directors_stance_stays_the_first_filter_in_rotation(db_session):
    outfit = _outfit(db_session, _case(db_session))
    _look(db_session, outfit, "CAM01_FRONT", "MEDIUM", "SEATED")
    _look(db_session, outfit, "CAM02_LEFT_45", "MEDIUM", "SEATED")
    standing = _look(db_session, outfit, "CAM01_FRONT", "MEDIUM", "STANDING", heygen="s")
    got = LS.choose_appearance(db_session, outfit, LS.Wanted("STANDING", "CAM01_FRONT", "MEDIUM"), 0)
    assert got["look"].id == standing.id


# ---------------------------------------------------------------------------
# panel data + the final-version gate
# ---------------------------------------------------------------------------

def test_the_panel_lists_each_languages_outfits_choice_and_fixed_picture(db_session):
    case = _case(db_session)
    en = _outfit(db_session, case, "en", name="Navy suit")
    _outfit(db_session, case, "de", name="Navy suit", select=False)
    lk = _look(db_session, en)
    OF.set_fixed_look(db_session, en, lk.id)
    panel = OF.panel(db_session, case.id)
    assert [r["language"] for r in panel["languages"]] == list(ai_config.channels)
    en_row = next(r for r in panel["languages"] if r["language"] == "en")
    assert en_row["chosen_outfit_id"] == en.id and en_row["fixed_look_id"] == lk.id
    assert en_row["outfits"][0]["looks"][0]["id"] == lk.id
    # name availability drives checkbox A on the client (lowercased key, channel order)
    assert panel["name_in_languages"]["navy suit"] == ["en", "de"]
    de_row = next(r for r in panel["languages"] if r["language"] == "de")
    assert de_row["chosen_outfit_id"] is None and de_row["final_rendered"] is False


def test_an_outfit_choice_can_change_until_the_final_render(db_session):
    case = _case(db_session)
    a = _outfit(db_session, case, "en", name="Navy suit")
    b = _outfit(db_session, case, ref="en/brownSweater", name="Grey", select=False)
    OF.select_outfit(db_session, b)
    db_session.refresh(a)
    db_session.refresh(b)
    assert OF.selected_outfit(db_session, case.id, "en").id == b.id
    assert a.status == "draft" and a.fixed_look_id is None
    # a rendered full film in that language flags "a change needs a new render"
    from app.db.models import Video
    db_session.add(Video(case_id=case.id, language="en", mode="full", state="rendered"))
    db_session.commit()
    row = next(r for r in OF.panel(db_session, case.id)["languages"] if r["language"] == "en")
    assert row["final_rendered"] is True


def test_the_final_version_reports_a_missing_outfit_choice(db_session):
    case = _case(db_session)
    assert OF.needs_outfit_choice(db_session, case.id, "en") is True
    _outfit(db_session, case, "en")
    assert OF.needs_outfit_choice(db_session, case.id, "en") is False


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_outfit_panel_api_round_trip(client, db_session):
    case = _case(db_session)
    en = _outfit(db_session, case, "en", name="Navy suit")
    _outfit(db_session, case, "de", name="Navy suit", select=False)
    lk = _look(db_session, en)

    r = client.get(f"/api/cases/{case.id}/outfit-panel")
    assert r.status_code == 200 and r.json()["languages"][0]["language"] == "en"

    r = client.post(f"/api/cases/{case.id}/outfit-panel/select", json={"outfit_id": en.id})
    assert r.status_code == 200
    r = client.post(f"/api/cases/{case.id}/outfit-panel/same-outfit", json={"outfit_id": en.id})
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["applied"] == ["de"]

    r = client.post(f"/api/cases/{case.id}/outfit-panel/fixed-look",
                    json={"outfit_id": en.id, "look_id": lk.id})
    assert r.status_code == 200
    en_row = next(x for x in r.json()["languages"] if x["language"] == "en")
    assert en_row["fixed_look_id"] == lk.id

    r = client.post(f"/api/cases/{case.id}/outfit-panel/fixed-look",
                    json={"outfit_id": en.id, "look_id": None})
    assert r.status_code == 200
    en_row = next(x for x in r.json()["languages"] if x["language"] == "en")
    assert en_row["fixed_look_id"] is None


def test_outfit_panel_api_rejects_foreign_or_unapproved_looks(client, db_session):
    case = _case(db_session)
    en = _outfit(db_session, case, "en", name="Navy suit")
    pending = _look(db_session, en, status="generated")
    r = client.post(f"/api/cases/{case.id}/outfit-panel/fixed-look",
                    json={"outfit_id": en.id, "look_id": pending.id})
    assert r.status_code == 409
