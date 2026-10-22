"""P10: review triage for the owner. Duplicates are grouped, obvious junk is auto-rejected with
a stored reason, the second pass (S3.b) resolves what it can first — the owner sees only what
is genuinely unclear. An approve/reject decision goes through the existing
PATCH /api/visuals/{id} (human_override=True), already its own audit trail."""

import json
import uuid

from app.core.ai_config import ai_config
from app.db.models import Case, EntityDossier, VisualAsset
from app.documentary.visuals import review_queue as RQ


def _case(db):
    case = Case(canonical_title="Triage", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


def _asset(db, case, code, status="needs_review", conf=0.5, provider="brave_images",
          subject="place", phash=None, second_pass=False, **kw):
    v = {"subject_type": subject}
    if second_pass:
        v["second_pass"] = {"status": "needs_review"}
    a = VisualAsset(case_id=case.id, asset_code=code, asset_type="photo", provider=provider,
                    verification_status=status, verification_confidence=conf, phash=phash,
                    entity_type=("person" if subject == "person" else subject), subject_type=subject,
                    verification_json=json.dumps(v), **kw)
    db.add(a)
    db.commit()
    return a


# ---- auto-reject obvious junk -------------------------------------------------------------

def test_a_picture_right_at_the_reject_floor_is_obvious_junk(db_session):
    case = _case(db_session)
    floor = ai_config.visual_verification.reject_below_confidence
    a = _asset(db_session, case, "A1", conf=floor + 0.01, provider="brave_images")
    assert RQ.is_obvious_junk(a) is True


def test_a_case_source_picture_is_never_auto_rejected_even_near_the_floor(db_session):
    case = _case(db_session)
    floor = ai_config.visual_verification.reject_below_confidence
    a = _asset(db_session, case, "A2", conf=floor + 0.01, provider="source_page")
    assert RQ.is_obvious_junk(a) is False


def test_a_confident_needs_review_picture_is_not_obvious_junk(db_session):
    case = _case(db_session)
    a = _asset(db_session, case, "A3", conf=0.65, provider="brave_images")
    assert RQ.is_obvious_junk(a) is False


def test_auto_reject_obvious_junk_rejects_and_records_a_reason(db_session):
    case = _case(db_session)
    floor = ai_config.visual_verification.reject_below_confidence
    junk = _asset(db_session, case, "J1", conf=floor + 0.01, provider="brave_images")
    kept = _asset(db_session, case, "K1", conf=0.65, provider="brave_images")
    n = RQ.auto_reject_obvious_junk(db_session, case.id)
    assert n == 1
    db_session.refresh(junk)
    db_session.refresh(kept)
    assert junk.verification_status == "rejected"
    assert json.loads(junk.verification_json)["reason"] == "triage_auto_reject"
    assert kept.verification_status == "needs_review"


# ---- grouping near-duplicates ---------------------------------------------------------------

def test_group_duplicates_clusters_by_close_phash(db_session):
    case = _case(db_session)
    a = _asset(db_session, case, "D1", phash=0b0000000000000000)
    b = _asset(db_session, case, "D2", phash=0b0000000000000011)  # hamming distance 2
    c = _asset(db_session, case, "D3", phash=0b1111111111111111)  # far away
    groups = RQ.group_duplicates([a, b, c])
    codes = sorted(sorted(x.asset_code for x in g) for g in groups)
    assert ["D1", "D2"] in codes and ["D3"] in codes


def test_group_duplicates_gives_a_picture_without_a_phash_its_own_group(db_session):
    case = _case(db_session)
    a = _asset(db_session, case, "N1", phash=None)
    [group] = RQ.group_duplicates([a])
    assert [x.asset_code for x in group] == ["N1"]


# ---- the full queue: excludes second-pass-eligible, counts before/after ---------------------

def test_review_queue_excludes_pictures_the_second_pass_has_not_tried_yet(db_session):
    case = _case(db_session)
    _asset(db_session, case, "P1", subject="place")          # eligible for the second pass: excluded
    _asset(db_session, case, "P2", subject="person")          # never eligible: stays in the queue
    _asset(db_session, case, "P3", subject="place", second_pass=True)  # already tried: stays
    out = RQ.review_queue(db_session, case.id)
    assert out["before"] == 3
    codes = {code for g in out["queue"] for code in g["asset_codes"]}
    assert codes == {"P2", "P3"}
    assert out["items"] == 2


def test_review_queue_groups_items_so_the_count_is_by_group_not_by_picture(db_session):
    case = _case(db_session)
    _asset(db_session, case, "G1", subject="person", phash=0)
    _asset(db_session, case, "G2", subject="person", phash=0)  # same phash: one group
    out = RQ.review_queue(db_session, case.id)
    assert out["items"] == 2 and out["groups"] == 1


# ---- beats_needing_entity: cross-references the plan's requirements with the blueprint's text --

def test_beats_needing_entity_pulls_the_beats_narration_summary():
    requirements = {"beats": [
        {"beat_id": "B01", "requirements": [{"entity": "kabar_sheath"}]},
        {"beat_id": "B02", "requirements": [{"entity": "white_sedan"}]},
        {"beat_id": "B03", "requirements": [{"entity": "kabar_sheath"}]},
    ]}
    blueprint = {"beats": [
        {"id": "B01", "summary": "Police found no murder weapon."},
        {"id": "B02", "summary": "A white sedan was seen nearby."},
        {"id": "B03", "summary": "The sheath was swabbed for DNA."},
    ]}
    out = RQ.beats_needing_entity(requirements, blueprint, "kabar_sheath")
    assert [b["beat_id"] for b in out] == ["B01", "B03"]
    assert out[0]["summary"] == "Police found no murder weapon."


def test_beats_needing_entity_is_empty_for_an_entity_no_beat_needs():
    requirements = {"beats": [{"beat_id": "B01", "requirements": [{"entity": "white_sedan"}]}]}
    assert RQ.beats_needing_entity(requirements, {"beats": []}, "kabar_sheath") == []


# ---- mark_wrong_person: rejects the picture and writes a negative example into the dossier -----

def test_mark_wrong_person_rejects_the_picture_and_updates_the_dossier(db_session):
    case = _case(db_session)
    dossier = EntityDossier(case_id=case.id, entity_key="bryan_kohberger", canonical_name="Bryan Kohberger",
                            entity_type="person", not_this_json=json.dumps(["Michael Kohberger"]))
    db_session.add(dossier)
    a = _asset(db_session, case, "W1", subject="person", title="A man at a rally",
              entity_key="bryan_kohberger")
    db_session.commit()
    out = RQ.mark_wrong_person(db_session, a.id, "bryan_kohberger")
    db_session.refresh(a)
    db_session.refresh(dossier)
    assert a.verification_status == "rejected" and a.human_override is True
    assert json.loads(a.verification_json)["reason"] == "wrong_person_owner_review"
    not_this = json.loads(dossier.not_this_json)
    assert "Michael Kohberger" in not_this  # kept
    assert any("A man at a rally" in n for n in not_this)  # the new negative example added
    assert out["entity_key"] == "bryan_kohberger"


def test_mark_wrong_person_is_safe_when_the_entity_has_no_dossier_yet(db_session):
    case = _case(db_session)
    a = _asset(db_session, case, "W2", subject="person", title="Someone", entity_key="no_dossier_yet")
    db_session.commit()
    out = RQ.mark_wrong_person(db_session, a.id, "no_dossier_yet")
    db_session.refresh(a)
    assert a.verification_status == "rejected"
    assert out["entity_key"] == "no_dossier_yet"


# ---- review_queue_for_ui: the enriched, beat-aware, sorted listing the UI reads ----------------

def test_review_queue_for_ui_sorts_by_how_many_beats_a_picture_would_fill(db_session):
    from app.db.models import VisualPlan
    from tests.factories import make_blueprint

    case = _case(db_session)
    bp = make_blueprint(db_session, case)
    bp.blueprint_json = json.dumps({"beats": [
        {"id": "B01", "summary": "Police found no murder weapon."},
        {"id": "B02", "summary": "A white sedan was seen nearby."},
        {"id": "B03", "summary": "The sheath was swabbed for DNA."},
    ]})
    db_session.commit()
    requirements = {
        "entities": [{"key": "kabar_sheath", "name": "Ka-Bar sheath"},
                    {"key": "white_sedan", "name": "White sedan"}],
        "beats": [
            {"beat_id": "B01", "requirements": [{"entity": "kabar_sheath"}]},
            {"beat_id": "B02", "requirements": [{"entity": "white_sedan"}]},
            {"beat_id": "B03", "requirements": [{"entity": "kabar_sheath"}]},
        ],
    }
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1, status="planned",
                      requirements_json=json.dumps(requirements))
    db_session.add(plan)
    db_session.commit()
    # subject types outside the second-pass list (place/building/landscape/object/document/map),
    # so both stay in the true human queue rather than being excluded as second-pass-pending;
    # far-apart phashes keep them two groups, so the order test does not depend on which row
    # the database returns first
    _asset(db_session, case, "U1", subject="person", entity_key="kabar_sheath", phash=0b0000000000000000)
    _asset(db_session, case, "U2", subject="vehicle", entity_key="white_sedan", phash=0b1111111111111111)
    out = RQ.review_queue_for_ui(db_session, case.id)
    codes = [g["asset"]["asset_id"] for g in out["queue"]]
    assert codes[0] == "U1"  # needed by 2 beats, comes first
    assert out["queue"][0]["beats_count"] == 2
    assert [b["beat_id"] for b in out["queue"][0]["beats"]] == ["B01", "B03"]
    assert out["queue"][0]["entity_name"] == "Ka-Bar sheath"
