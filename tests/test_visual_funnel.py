import json
import uuid

from PIL import Image

from app.core.ai_config import ai_config
from app.db.models import (
    Case,
    DocumentaryJob,
    EditorialBlueprint,
    ProductionScript,
    VideoSource,
    VisualAsset,
    VisualPlan,
)
from app.documentary.visuals import contact_sheet, funnel


def _case(db):
    case = Case(canonical_title="Funnel", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


def _asset(db, case, code, status="verified", reason=None, provider="brave_images", **kw):
    verdict = {"reason": reason} if reason else None
    db.add(VisualAsset(case_id=case.id, asset_code=code, asset_type=kw.pop("asset_type", "photo"),
                       provider=provider, rights_status=kw.pop("rights_status", "public_domain"), verification_status=status,
                       verification_json=json.dumps(verdict) if verdict else None,
                       found_during=kw.pop("found_during", "research"),
                       local_path=kw.pop("local_path", "x.jpg"), **kw))
    db.commit()


def test_a_rejection_is_reported_under_its_label_or_other(db_session):
    case = _case(db_session)
    _asset(db_session, case, "A1", "rejected", "watermark")
    _asset(db_session, case, "A2", "rejected", "something_new")
    _asset(db_session, case, "A3", "rejected")
    rows = {a.asset_code: funnel.rejection_reason(a) for a in db_session.query(VisualAsset)
            .filter_by(case_id=case.id)}
    assert rows == {"A1": "watermark", "A2": "other", "A3": "other"}


def test_the_provider_funnel_counts_every_verdict_and_unchecked_pictures(db_session):
    case = _case(db_session)
    _asset(db_session, case, "A1", "verified")
    _asset(db_session, case, "A2", "needs_review")
    _asset(db_session, case, "A3", "rejected", "tone")
    _asset(db_session, case, "A4", "unverified")
    _asset(db_session, case, "A5", "unverified", local_path=None)
    _asset(db_session, case, "A6", "verified", provider="wikimedia", found_during="production_search")
    rows = {(r["stage"], r["provider"]): r for r in funnel.provider_funnel(db_session, case.id)}
    brave = rows[("research", "brave_images")]
    assert (brave["stored"], brave["downloaded"], brave["verified"], brave["needs_review"],
            brave["rejected"], brave["unverified"]) == (5, 4, 1, 1, 1, 2)
    assert brave["rejected_by"] == {"tone": 1}
    assert rows[("production_search", "wikimedia")]["verified"] == 1


def test_research_stages_of_a_job_are_listed_without_the_skipped_and_pending_ones(db_session):
    case = _case(db_session)
    stages = [{"name": "visual_research", "status": "done", "detail": {"queries": 5}},
              {"name": "visual_check", "status": "skipped", "detail": {}},
              {"name": "visual_gaps", "status": "pending"},
              {"name": "render:en", "status": "done"}]
    db_session.add(DocumentaryJob(case_id=case.id, mode="full", languages_json='["en"]',
                                  stages_json=json.dumps(stages)))
    db_session.commit()
    assert [(s["stage"], s["detail"]) for s in funnel.intake_stages(db_session, case.id)] == [
        ("visual_research", {"queries": 5})]


def test_a_film_counts_real_pictures_illustrations_and_video(db_session):
    case = _case(db_session)
    _asset(db_session, case, "R1")
    _asset(db_session, case, "R2")
    _asset(db_session, case, "G1", provider="generated")
    shots = [{"asset_id": "R1", "kind": "image", "role": "evidence", "start": 0, "end": 10},
             {"asset_id": "R1", "kind": "image", "role": "evidence", "start": 20, "end": 28},
             {"asset_id": "R2", "kind": "image", "role": "context", "start": 10, "end": 20},
             {"asset_id": "G1", "kind": "image", "role": "illustration", "start": 28, "end": 38},
             {"asset_id": None, "kind": "video", "start": 38, "end": 48},
             {"asset_id": None, "kind": "title", "start": 48, "end": 52}]
    script = ProductionScript(case_id=case.id, story_version_id=1, language="en", duration_seconds=52.0,
                              script_json=json.dumps({"shots": shots}))
    db_session.add(script)
    db_session.commit()
    stats = funnel.film_stats(db_session, script)
    assert stats["real_image_shots"] == 3 and stats["real_distinct"] == 2
    assert stats["real_avg_seconds"] == 9.3
    assert (stats["video_shots"], stats["video_seconds"]) == (1, 10.0)
    assert stats["by_kind"]["illustration"] == {"shots": 1, "seconds": 10.0}


def test_videos_are_reported_by_use_and_reason(db_session):
    case = _case(db_session)
    for n, (use, reason) in enumerate([("research_only", "other channel"), ("research_only", "other channel"),
                                       ("film_candidate", "Internet Archive: public domain")]):
        db_session.add(VideoSource(case_id=case.id, video_id=f"v{n}", url=f"https://example.invalid/{n}",
                                   title="t", use=use, use_reason=reason))
    db_session.commit()
    _asset(db_session, case, "C1", "unverified", asset_type="video")
    _asset(db_session, case, "C2", "verified", asset_type="video", spec_json='{"parent": "S1"}')
    report = funnel.video_funnel(db_session, case.id)
    assert report["found"] == 3 and report["downloaded"] == 2 and report["pieces"] == 1
    assert {(x["use"], x["videos"]) for x in report["by_use"]} == {("research_only", 2), ("film_candidate", 1)}
    assert report["by_class"] == {"none": 3} and report["usable_cuts"] == 1


def _plan(db, case, requirements, blueprint):
    bp = EditorialBlueprint(case_id=case.id, story_version_id=1, status="valid",
                            blueprint_json=json.dumps(blueprint))
    db.add(bp)
    db.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, requirements_json=json.dumps(requirements),
                      plan_json=json.dumps({"candidates": {"B01": []}}))
    db.add(plan)
    db.commit()
    return plan


def test_an_empty_beat_is_explained_by_the_first_rule_that_kept_each_picture_away(db_session):
    case = _case(db_session)
    entity = {"key": "sheath", "name": "Ka-Bar sheath", "type": "object"}
    requirements = {"entities": [entity], "beats": [{"beat_id": "B01", "requirements": [
        {"entity": "sheath", "purpose": "evidence", "priority": "high",
         "acceptable_roles": ["evidence"]}]}]}
    _asset(db_session, case, "OK", entities_json='["sheath"]', asset_role="evidence")
    _asset(db_session, case, "REJ", "rejected", entities_json='["sheath"]', asset_role="evidence")
    _asset(db_session, case, "NOFILE", local_path=None, entities_json='["sheath"]', asset_role="evidence")
    _asset(db_session, case, "OTHER", entities_json='["house"]', asset_role="evidence", title="a house")
    _asset(db_session, case, "FW", entities_json='["sheath"]', asset_role="evidence",
           reveals_json='["f1"]')
    plan = _plan(db_session, case, requirements, {"beats": [
        {"id": "B01", "words": 40}, {"id": "B02", "words": 40, "purpose": "reveal", "reveals": ["f1"]}]})
    why = funnel.empty_beat_reasons(db_session, plan, "B01")["sheath"]
    assert (why["ranked"], why["rejected"], why["no_file"], why["not_about_the_entity"],
            why["reveal_firewall"]) == (1, 1, 1, 1, 1)


def _thumbnail(tmp_path, name):
    path = tmp_path / name
    Image.new("RGB", (400, 300), "gray").save(path)
    return str(path)


def test_rejected_pictures_are_sampled_evenly_over_their_reasons(db_session):
    case = _case(db_session)
    for n in range(10):
        _asset(db_session, case, f"W{n}", "rejected", "watermark")
    for n in range(3):
        _asset(db_session, case, f"T{n}", "rejected", "tone")
    rejected = db_session.query(VisualAsset).filter_by(case_id=case.id).all()
    picked = contact_sheet.sample_rejected(rejected, 6)
    assert len(picked) == 6
    assert sum(a.asset_code.startswith("T") for a in picked) == 3
    assert contact_sheet.sample_rejected(rejected, 100) and len(contact_sheet.sample_rejected(rejected, 100)) == 13


def test_a_contact_sheet_is_one_png_with_a_cell_per_picture(db_session, tmp_path):
    case = _case(db_session)
    for n in range(7):
        _asset(db_session, case, f"S{n}", "rejected", "tone", thumbnail_path=_thumbnail(tmp_path, f"s{n}.png"),
               verification_confidence=0.9)
    assets = db_session.query(VisualAsset).filter_by(case_id=case.id).all()
    out = contact_sheet.render_sheet(assets, tmp_path / "sheets" / "rejected_1.png")
    cfg = ai_config.visual_report_settings().contact_sheet
    with Image.open(out) as sheet:
        assert sheet.width > cfg.columns * cfg.thumbnail_px
        assert sheet.height > 2 * cfg.thumbnail_px * 3 // 4


def _requirements(entity_key="sheath"):
    return {"entities": [{"key": entity_key, "name": "Ka-Bar sheath", "type": "object"}],
            "beats": [{"beat_id": "B01", "requirements": [
                {"entity": entity_key, "purpose": "evidence", "priority": "high",
                 "acceptable_roles": ["evidence", "context"]}]}]}


def test_beat_offers_replay_the_candidates_and_count_what_the_film_used(db_session):
    from app.db.models import MediaUsage

    case = _case(db_session)
    for n in range(3):
        _asset(db_session, case, f"P{n}", entities_json='["sheath"]', asset_role="evidence")
    plan = _plan(db_session, case, _requirements(), {"beats": [{"id": "B01", "words": 40}]})
    first = db_session.query(VisualAsset).filter_by(case_id=case.id, asset_code="P0").one()
    db_session.add(MediaUsage(case_id=case.id, asset_id=first.id, film_key=f"bp{plan.blueprint_id}",
                              beat_id="B01"))
    db_session.commit()
    [beat] = funnel.beat_offers(db_session, plan)
    assert (beat["beat"], beat["offered_now"], beat["stored_with_plan"], beat["used"]) == ("B01", 3, 0, 1)


def test_the_candidate_cap_is_reported_as_hit_when_more_pictures_ranked(db_session, monkeypatch):
    case = _case(db_session)
    for n in range(3):
        _asset(db_session, case, f"C{n}", entities_json='["sheath"]', asset_role="evidence")
    plan = _plan(db_session, case, _requirements(), {"beats": [{"id": "B01", "words": 40}]})
    monkeypatch.setattr(ai_config.visual_direction, "candidates_per_requirement", 2)
    caps = {c["cap"]: c for c in funnel.caps_hit(db_session, case.id, plan)}
    assert caps["director candidates per requirement"]["hit"] is True
    monkeypatch.setattr(ai_config.visual_direction, "candidates_per_requirement", 3)
    caps = {c["cap"]: c for c in funnel.caps_hit(db_session, case.id, plan)}
    assert caps["director candidates per requirement"]["hit"] is False


def test_plan_rules_count_adjustments_and_warnings(db_session):
    plan = VisualPlan(case_id=1, blueprint_id=1, validation_json=json.dumps({"plan": {
        "adjustments": [{"beat": "B1", "reason": "density"}, {"beat": "B2", "reason": "density"},
                        {"beat": "B3", "fallback": "BLACK_SCREEN"}],
        "warnings": [{"code": "asset_not_candidate"}]}}))
    rules = funnel.plan_rules(plan)
    assert rules["adjustments"] == {"density": 2, "fallback": 1}
    assert rules["warnings"] == {"asset_not_candidate": 1}


def test_fill_ways_and_coverage_repairs_of_a_film_are_counted():
    shots = [{"fill_reason": "fill way reframe: a closer view"}, {"fill_reason": "fill way reframe: a closer view"},
             {"why": "coverage repair: an illustration instead of a card"}, {"why": "director chose it"}]
    script = ProductionScript(case_id=1, story_version_id=1, language="en",
                              script_json=json.dumps({"shots": shots}))
    assert funnel.fill_ways_used(script) == {"fill way reframe: a clos": 2, "coverage repair: an illu": 1}


def test_stage_details_list_every_run_of_a_stage(db_session):
    case = _case(db_session)
    for detail in ({"added": 1}, {"added": 2}):
        db_session.add(DocumentaryJob(case_id=case.id, mode="full", languages_json='["en"]',
                                      stages_json=json.dumps([{"name": "visual_gaps", "status": "done",
                                                               "detail": detail}])))
    db_session.commit()
    assert [d["added"] for d in funnel.stage_details(db_session, case.id, "visual_gaps")] == [1, 2]
