"""S3: looser verification for places, objects, documents and maps — and everything that stays strict."""

import asyncio
import json
import uuid
from pathlib import Path

from app.agents import visual_verification as VV
from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary.visuals import director as D
from app.documentary.visuals import tiers as T
from app.documentary.visuals.verification import decide
from app.providers.generation.base import GenerationResult


def v(**kw):
    return {"matches_claim": "yes", "period_ok": "yes", "confidence": 0.85, "subject_type": "building",
            "tone_ok": True, "watermark": False, "graphic_or_sensitive": False, "depicts": "x", **kw}


# ---- an answer that is no verdict is not a rejection -------------------------------------------

def test_an_answer_without_a_verdict_is_not_a_rejection():
    assert decide({}) == ("unverified", 0.0, "no_verdict")
    schema_drift = {"entity": "borden_house", "match": "likely", "confidence": "high", "era": "modern"}
    assert decide(schema_drift)[0] == "unverified"


# ---- a. context pictures count, never as evidence ---------------------------------------------

def test_a_real_picture_of_the_same_kind_of_place_is_context_supply():
    out = decide(v(matches_claim="context", subject_type="place", confidence=0.65))
    assert out == ("verified", 0.65, "context")


def test_the_old_stand_in_answer_reads_as_context():
    assert decide(v(matches_claim="stand_in", subject_type="object", confidence=0.8)) == (
        "verified", 0.8, "context")


def test_context_of_a_person_is_never_accepted():
    out = decide(v(matches_claim="context", subject_type="person", confidence=0.95))
    assert out == ("rejected", 0.95, "context_person")
    assert decide(v(matches_claim="stand_in", subject_type="person"))[0] == "rejected"


def test_a_context_picture_sits_in_tier_four_as_context_never_in_an_exact_tier():
    class A:
        provider, asset_role, entity_type, relevance_tier = "wikimedia", "context", "building", 3

    tier, why = T.verified_why(A(), {"matches_claim": "context", "role": "context",
                                     "subject_type": "building"})
    assert tier == 4 and "context" in why


def test_a_context_picture_ranks_below_an_exact_one():
    w = ai_config.visual_direction.tier_weights
    assert w["4"] < w["3"] < w["2"]


# ---- c. thresholds lower for places/objects/documents/maps only -------------------------------

def test_a_place_object_document_or_map_is_verified_at_the_owner_set_confidence():
    """Owner decision 2026-10-22: both the general and the by-subject bar are 0.5 (equal, no
    longer "lower" for these subjects) after review of the case 9 needs_review backlog."""
    low = ai_config.visual_verification.verified_min_confidence_by_subject
    assert set(low) == {"place", "building", "landscape", "object", "document", "map"}
    for subject in low:
        assert decide(v(subject_type=subject, confidence=low[subject]))[0] == "verified"
        assert decide(v(subject_type=subject, confidence=low[subject] - 0.01))[0] == "needs_review"
    assert all(x == ai_config.visual_verification.verified_min_confidence for x in low.values())


def test_the_boundary_at_the_owner_set_confidence_holds_at_the_cent():
    assert decide(v(subject_type="place", confidence=0.49))[0] == "needs_review"
    assert decide(v(subject_type="place", confidence=0.5))[0] == "verified"


def test_the_person_threshold_moved_with_the_owner_decision():
    """Owner decision 2026-10-22: verified_min_confidence 0.7 -> 0.5, reviewed 113 case 9
    needs_review pictures (~95% correct). A person has no separate entry in
    verified_min_confidence_by_subject, so it follows the same general bar."""
    cfg = ai_config.visual_verification
    assert cfg.verified_min_confidence == 0.5 and cfg.reject_below_confidence == 0.35
    assert decide(v(subject_type="person", confidence=0.5))[0] == "verified"
    assert decide(v(subject_type="person", confidence=0.49))[0] == "needs_review"
    assert decide(v(subject_type="person", confidence=0.34))[:1] == ("rejected",)


def test_events_and_vehicles_keep_the_general_threshold():
    assert decide(v(subject_type="event", confidence=0.49))[0] == "needs_review"
    assert decide(v(subject_type="vehicle", confidence=0.49))[0] == "needs_review"
    assert decide(v(subject_type="event", confidence=0.5))[0] == "verified"
    assert decide(v(subject_type="vehicle", confidence=0.5))[0] == "verified"


# ---- d. partial and modern views --------------------------------------------------------------

def test_a_detail_of_the_exact_thing_is_accepted_for_things_but_not_for_a_face():
    assert decide(v(matches_claim="partial", subject_type="building"))[0] == "verified"
    assert decide(v(matches_claim="partial", subject_type="person"))[0] == "needs_review"


def test_a_modern_view_of_the_right_place_is_still_accepted():
    assert decide(v(period_ok="no")) == ("verified", 0.85, "modern_view")


# ---- strict: identity, graphic/minors, watermarks, tone ---------------------------------------

def test_a_wrong_face_is_a_rejection():
    assert decide(v(matches_claim="no", subject_type="person"))[0] == "rejected"
    assert decide(v(matches_claim="no", subject_type="person", confidence=0.99))[2] == "does_not_match"


def test_an_unidentified_face_is_not_accepted_however_confident():
    out = decide(v(matches_claim="unclear", subject_type="person", confidence=0.95))
    assert out[0] == "needs_review"


def test_a_graphic_picture_or_a_minor_in_distress_stays_rejected():
    assert decide(v(graphic_or_sensitive=True))[0] == "rejected"
    assert decide(v(graphic_or_sensitive=True, subject_type="person"))[2] == "graphic_or_sensitive"


def test_a_watermark_stays_rejected_even_for_a_perfect_place_picture():
    assert decide(v(watermark=True, confidence=0.99, subject_type="place")) == (
        "rejected", 0.99, "watermark")


def test_the_wrong_tone_stays_rejected():
    assert decide(v(tone_ok=False))[0] == "rejected"


def test_the_verifier_prompt_separates_stock_watermarks_from_printed_captions():
    text = Path("prompts/documentary/visuals/verification/verifier_system.md").read_text()
    assert "postcard" in text and "NOT a watermark" in text and "stock" in text
    assert "stand_in" not in text and "context" in text and "partial" in text


# ---- the verification agent keeps an empty answer unverified ----------------------------------

class FakeGen:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    async def generate_structured(self, role, system, user, images=None):
        self.calls.append((role, json.loads(user)))
        return self.answers[role], GenerationResult(text="{}", model=f"m/{role}", provider="fake")


def _asset(db, case, code, **kw):
    a = VisualAsset(case_id=case.id, asset_code=code, asset_type="photo", provider="wikimedia",
                    local_path="x.jpg", thumbnail_path="t.jpg", rights_status="public_domain",
                    asset_role="context", title="Fall River station", entities_json='["fall_river"]',
                    found_for="Fall River", **kw)
    db.add(a)
    db.commit()
    return a


def _case(db):
    case = Case(canonical_title="Verify", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


ENTITIES = [{"key": "fall_river", "name": "Fall River", "type": "place"}]


def test_the_agent_leaves_an_asset_unverified_when_the_answer_is_no_verdict(db_session, monkeypatch):
    monkeypatch.setattr(VV, "image_for_check", lambda a: "t.jpg")
    monkeypatch.setattr(VV.IM, "data_url", lambda p: "data:image/jpeg;base64,")
    monkeypatch.setattr(ai_config.visual_verification, "cheap_tier_enabled", False)
    case = _case(db_session)
    a = _asset(db_session, case, "A1")
    agent = VV.VisualVerificationAgent.__new__(VV.VisualVerificationAgent)
    agent.gen = FakeGen({"visual_verifier": {"entity": "fall_river", "match": "likely"}})
    asyncio.run(agent.verify(db_session, case, a, ENTITIES, []))
    assert a.verification_status == "unverified"
    assert json.loads(a.verification_json)["reason"] == "no_verdict"


def test_a_context_picture_is_stored_as_a_real_context_picture_not_an_illustration(db_session, monkeypatch):
    monkeypatch.setattr(VV, "image_for_check", lambda a: "t.jpg")
    monkeypatch.setattr(VV.IM, "data_url", lambda p: "data:image/jpeg;base64,")
    monkeypatch.setattr(ai_config.visual_verification, "cheap_tier_enabled", False)
    case = _case(db_session)
    a = _asset(db_session, case, "A2")
    agent = VV.VisualVerificationAgent.__new__(VV.VisualVerificationAgent)
    agent.gen = FakeGen({"visual_verifier": v(matches_claim="context", subject_type="place",
                                              role="context", confidence=0.7, entities=[])})
    asyncio.run(agent.verify(db_session, case, a, ENTITIES, []))
    assert (a.verification_status, a.asset_role, a.relevance_tier) == ("verified", "context", 4)


# ---- b. the second, independent pass ----------------------------------------------------------

def _needs_review(db, case, code, subject, conf=0.5, **kw):
    a = _asset(db, case, code, subject_type=subject, verification_status="needs_review",
               verification_confidence=conf,
               verification_json=json.dumps(v(matches_claim="unclear", subject_type=subject,
                                              confidence=conf, entities=[], reason="uncertain")), **kw)
    return a


def _second(db, case, assets, answer):
    agent = VV.VisualVerificationAgent.__new__(VV.VisualVerificationAgent)
    agent.gen = FakeGen({"visual_second_verifier": answer})
    out = asyncio.run(agent.second_pass(db, case, assets, ENTITIES, []))
    return agent, out


def test_the_second_pass_uses_its_own_model_role_and_the_same_inputs(db_session, monkeypatch):
    monkeypatch.setattr(VV, "image_for_check", lambda a: "t.jpg")
    monkeypatch.setattr(VV.IM, "data_url", lambda p: "data:image/jpeg;base64,")
    assert ai_config.model_for("visual_second_verifier") != ai_config.model_for("visual_verifier")
    case = _case(db_session)
    a = _needs_review(db_session, case, "N1", "building")
    agent, out = _second(db_session, case, [a], v(subject_type="building", confidence=0.8,
                                                 role="context", entities=[]))
    assert [c[0] for c in agent.gen.calls] == ["visual_second_verifier"]
    assert out == {"second_checked": 1, "second_verified": 1}
    assert a.verification_status == "verified"
    stored = json.loads(a.verification_json)
    assert stored["reason"] == "second_pass" and stored["second_pass"]["model"] == "m/visual_second_verifier"


def test_a_place_that_passes_the_second_pass_counts_as_real_supply(db_session, monkeypatch):
    from app.documentary.visuals import supply

    monkeypatch.setattr(VV, "image_for_check", lambda a: "t.jpg")
    monkeypatch.setattr(VV.IM, "data_url", lambda p: "data:image/jpeg;base64,")
    case = _case(db_session)
    a = _needs_review(db_session, case, "N2", "place")
    before = supply.usable_real(db_session, case)
    _second(db_session, case, [a], v(subject_type="place", confidence=0.7, role="context", entities=[]))
    assert supply.usable_real(db_session, case) == before + 1


def test_a_person_is_never_accepted_by_the_second_pass(db_session):
    case = _case(db_session)
    a = _needs_review(db_session, case, "P1", "person", conf=0.6)
    agent, out = _second(db_session, case, [a], v(subject_type="person", confidence=0.99))
    assert agent.gen.calls == [] and out == {"second_checked": 0, "second_verified": 0}
    assert a.verification_status == "needs_review"


def test_a_picture_the_second_pass_sees_as_a_person_stays_with_the_owner(db_session, monkeypatch):
    monkeypatch.setattr(VV, "image_for_check", lambda a: "t.jpg")
    monkeypatch.setattr(VV.IM, "data_url", lambda p: "data:image/jpeg;base64,")
    case = _case(db_session)
    a = _needs_review(db_session, case, "P2", "object")
    _second(db_session, case, [a], v(subject_type="person", confidence=0.99))
    assert a.verification_status == "needs_review"


def test_a_disagreeing_second_pass_leaves_the_picture_for_the_owner(db_session, monkeypatch):
    monkeypatch.setattr(VV, "image_for_check", lambda a: "t.jpg")
    monkeypatch.setattr(VV.IM, "data_url", lambda p: "data:image/jpeg;base64,")
    case = _case(db_session)
    a = _needs_review(db_session, case, "D1", "building")
    _, out = _second(db_session, case, [a], v(matches_claim="no", subject_type="building"))
    assert a.verification_status == "needs_review" and out["second_verified"] == 0
    assert "second_pass" in json.loads(a.verification_json)


def test_graphic_or_watermarked_pictures_do_not_pass_the_second_pass(db_session, monkeypatch):
    monkeypatch.setattr(VV, "image_for_check", lambda a: "t.jpg")
    monkeypatch.setattr(VV.IM, "data_url", lambda p: "data:image/jpeg;base64,")
    case = _case(db_session)
    for code, flag in (("G1", {"graphic_or_sensitive": True}), ("W1", {"watermark": True})):
        a = _needs_review(db_session, case, code, "building")
        _second(db_session, case, [a], v(subject_type="building", confidence=0.95, **flag))
        assert a.verification_status == "needs_review", code


def test_a_picture_is_never_sent_to_the_second_pass_twice(db_session, monkeypatch):
    monkeypatch.setattr(VV, "image_for_check", lambda a: "t.jpg")
    monkeypatch.setattr(VV.IM, "data_url", lambda p: "data:image/jpeg;base64,")
    case = _case(db_session)
    a = _needs_review(db_session, case, "T1", "building")
    _second(db_session, case, [a], v(matches_claim="no"))
    agent, out = _second(db_session, case, [a], v(subject_type="building"))
    assert agent.gen.calls == [] and out["second_checked"] == 0


# ---- the spoiler firewall is unchanged for the new kinds of picture ---------------------------

def test_a_verified_context_picture_that_reveals_a_later_fact_is_still_held_back(db_session):
    case = _case(db_session)
    a = _asset(db_session, case, "F1", verification_status="verified", reveals_json='["F9"]')
    assert not D.firewall_ok(a, {"F9"}) and D.firewall_ok(a, set())


def test_the_case_wide_second_pass_takes_every_unchecked_needs_review_place(db_session, monkeypatch):
    from app.documentary import jobs as J

    case = _case(db_session)
    a = _needs_review(db_session, case, "C1", "building")
    person = _needs_review(db_session, case, "C2", "person", conf=0.6)
    seen = []

    async def fake_second(self, db, case_, assets, entities, facts):
        seen.append([x.asset_code for x in assets])
        return {"second_checked": len(assets), "second_verified": 1}

    monkeypatch.setattr(VV.VisualVerificationAgent, "__init__", lambda self: None)
    monkeypatch.setattr(VV.VisualVerificationAgent, "second_pass", fake_second)
    out = asyncio.run(J.second_pass_case(db_session, case, ENTITIES))
    assert seen == [["C1"]] and out == {"second_checked": 1, "second_verified": 1}
    assert a.asset_code == "C1" and person.verification_status == "needs_review"


def _cand(code, role, status, tier):
    a = VisualAsset(case_id=1, asset_code=code, asset_type="photo", provider="wikimedia",
                    verification_status=status, asset_role=role, relevance_tier=tier)
    return a


def test_verified_context_and_related_pictures_are_offered_as_fill_before_anything_else():
    weight = ai_config.visual_direction.tier_weights
    floor = ai_config.visual_direction.fill_min_score
    assert weight["4"] >= floor and weight["3"] >= floor      # tier 3 and 4 clear the bar
    offered = D.fill_candidates({"B1": [
        (0.5, _cand("T3", "context", "verified", 3)),
        (float(weight["4"]) * 0.8, _cand("T4", "context", "verified", 4)),
        (0.9, _cand("ILL", "illustration", "verified", 5)),
        (0.9, _cand("UNV", "context", "unverified", 3)),
        (floor - 0.01, _cand("LOW", "context", "verified", 4))]})
    assert offered == {"B1": ["T3", "T4"]}


def test_a_failed_second_pass_call_leaves_the_picture_for_the_owner_and_the_run_goes_on(db_session, monkeypatch):
    from app.providers.generation.base import GenerationError

    monkeypatch.setattr(VV, "image_for_check", lambda a: "t.jpg")
    monkeypatch.setattr(VV.IM, "data_url", lambda p: "data:image/jpeg;base64,")
    case = _case(db_session)
    bad = _needs_review(db_session, case, "E1", "building")
    good = _needs_review(db_session, case, "E2", "building")

    class Flaky(FakeGen):
        async def generate_structured(self, role, system, user, images=None):
            if json.loads(user)["title"] == "boom":
                raise GenerationError("unavailable", "APIMaster error (HTTP 503)")
            return await super().generate_structured(role, system, user, images)

    bad.title = "boom"
    db_session.commit()
    agent = VV.VisualVerificationAgent.__new__(VV.VisualVerificationAgent)
    agent.gen = Flaky({"visual_second_verifier": v(subject_type="building", confidence=0.8, role="context",
                                                  entities=[])})
    out = asyncio.run(agent.second_pass(db_session, case, [bad, good], ENTITIES, []))
    assert out == {"second_checked": 1, "second_verified": 1, "second_errors": 1}
    assert bad.verification_status == "needs_review" and "second_pass" not in json.loads(bad.verification_json)
    assert good.verification_status == "verified"


def test_a_context_picture_of_an_object_needs_more_confidence_than_one_of_a_place():
    """Precision check of 2026-10-21: sheaths of another kind passed at 0.60 as 'the same kind'."""
    assert decide(v(matches_claim="context", subject_type="place", confidence=0.6))[0] == "verified"
    assert decide(v(matches_claim="context", subject_type="object", confidence=0.6))[0] == "needs_review"
    assert decide(v(matches_claim="context", subject_type="object", confidence=0.7))[0] == "verified"
    assert decide(v(matches_claim="yes", subject_type="object", confidence=0.6))[0] == "verified"   # the exact object
