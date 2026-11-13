"""The live configuration (owner decision 2026-11-13): the host appears three times, as the opening, one mid appearance of the length of a transition, and the outro."""

from app.core.ai_config import ai_config
from app.documentary.host_planning import validate_host_plan
from tests.test_host import codes, plan_raw, story


def _three(role="transition"):
    raw = plan_raw()
    raw["segments"] = [raw["segments"][0], {"id": "S2", "position": "mid", "role": role, "after_act": "act2", "reason": "pause"}, raw["segments"][3]]
    return raw


def test_the_live_configuration_asks_for_three_appearances_and_one_transition_in_the_middle():
    host = ai_config.host
    assert (host.min_appearances, host.max_appearances, host.short_film_min_appearances) == (3, 3, 3)
    assert host.mid_roles == ["transition"]


def test_three_appearances_with_a_transition_in_the_middle_are_valid_and_s3_is_the_outro():
    plan, report = validate_host_plan(_three(), story())
    assert report["status"] == "valid", report["errors"]
    assert [(s["id"], s["role"]) for s in plan["segments"]] == [("S1", "opening"), ("S2", "transition"), ("S3", "outro")]
    middle = plan["segments"][1]["target_seconds"]
    spec = ai_config.host.roles["transition"]
    assert spec.min_seconds <= middle <= spec.max_seconds


def test_a_mid_appearance_of_another_role_is_refused_and_so_are_two_mid_appearances():
    assert "mid_role_not_allowed" in codes(validate_host_plan(_three("reflection"), story())[1]["errors"])
    four = plan_raw()
    four["segments"][2]["role"] = "transition"
    assert "appearance_count_out_of_range" in codes(validate_host_plan(four, story())[1]["errors"])


def test_a_case_with_a_host_plan_keeps_its_four_appearances_and_a_new_case_gets_three(db_session):
    import json
    from uuid import uuid4

    from app.db.models import Case, HostPlan
    from app.documentary.host_planning import cfg_for_case
    from app.documentary.outfits import section_ids

    old = Case(canonical_title="Old four", slug=f"o-{uuid4().hex[:8]}", language="en")
    new = Case(canonical_title="New case", slug=f"n-{uuid4().hex[:8]}", language="en")
    db_session.add_all([old, new])
    db_session.commit()
    db_session.add(HostPlan(case_id=old.id, version=1, status="valid", plan_json=json.dumps({"segments": plan_raw()["segments"]})))
    db_session.commit()
    kept = cfg_for_case(db_session, old.id)
    assert (kept.min_appearances, kept.max_appearances, kept.mid_roles) == (4, 4, [])
    assert validate_host_plan(plan_raw(), story(), kept)[1]["status"] == "valid"
    assert section_ids(db_session, old.id) == ["S1", "S2", "S3", "S4"]
    assert cfg_for_case(db_session, new.id) is ai_config.host and section_ids(db_session, new.id) == ["S1", "S2", "S3"]
