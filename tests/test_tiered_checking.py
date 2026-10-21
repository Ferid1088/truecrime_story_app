"""P7: tiered checking. Free rules (size/blocked-domain/pre-filter) happen before a candidate
is even stored as unverified (research.py); here, the cheap tier (role visual_verifier_cheap)
looks first and its verdict is trusted only when decisive (verified/rejected, high confidence)
and never for a person — the strong verifier (visual_verifier) decides the unclear middle and
every person-identity question."""

import asyncio
import json
import uuid

from app.agents import visual_verification as VV
from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary.visuals.verification import cheap_tier_may_decide, tier_report
from app.providers.generation.base import GenerationResult


def v(**kw):
    return {"matches_claim": "yes", "period_ok": "yes", "confidence": 0.9, "subject_type": "building",
            "tone_ok": True, "watermark": False, "graphic_or_sensitive": False, "depicts": "x", **kw}


# ---- cheap_tier_may_decide: the pure rule ------------------------------------------------------

def test_a_decisive_non_person_verdict_may_be_trusted():
    assert cheap_tier_may_decide(v(), "verified", 0.9) is True


def test_a_person_is_never_decided_by_the_cheap_tier():
    assert cheap_tier_may_decide(v(subject_type="person"), "verified", 0.99) is False


def test_needs_review_is_never_decisive():
    assert cheap_tier_may_decide(v(), "needs_review", 0.9) is False


def test_below_the_decisive_confidence_margin_escalates(monkeypatch):
    monkeypatch.setattr(ai_config.visual_verification, "cheap_tier_decisive_confidence", 0.85)
    assert cheap_tier_may_decide(v(confidence=0.8), "verified", 0.8) is False
    assert cheap_tier_may_decide(v(confidence=0.9), "verified", 0.9) is True


def test_a_rejected_verdict_can_also_be_decisive():
    assert cheap_tier_may_decide(v(matches_claim="no", period_ok="no"), "rejected", 0.9) is True


# ---- tier_report ------------------------------------------------------------------------------

def _asset(status, tier, subject="building", provider="wikimedia"):
    return VisualAsset(verification_status=status, provider=provider, asset_type="photo",
                      verification_json=json.dumps({"check_tier": tier, "subject_type": subject}))


def test_tier_report_counts_the_share_each_tier_decided():
    assets = [_asset("verified", "cheap")] * 6 + [_asset("rejected", "strong")] * 4
    out = tier_report(assets)
    assert out == {"checked": 10, "by_tier": {"cheap": 6, "strong": 4, "unknown": 0},
                   "cheap_share": 0.6, "cheap_person_pictures": 0}


def test_tier_report_flags_a_person_decided_by_the_cheap_tier_as_a_defect():
    assets = [_asset("verified", "cheap", subject="person")]
    assert tier_report(assets)["cheap_person_pictures"] == 1


def test_unverified_and_generated_pictures_are_not_counted():
    assets = [VisualAsset(verification_status="unverified", provider="wikimedia"),
              VisualAsset(verification_status="verified", provider="generated")]
    assert tier_report(assets)["checked"] == 0


# ---- wired into the agent: the cheap tier decides, or the strong one does ----------------------

class FakeGen:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    async def generate_structured(self, role, system, user, images=None):
        self.calls.append(role)
        return self.answers[role], GenerationResult(text="{}", model=f"m/{role}", provider="fake")


def _case(db):
    case = Case(canonical_title="Tiers", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


def _photo(db, case, code, **kw):
    a = VisualAsset(case_id=case.id, asset_code=code, asset_type="photo", provider="wikimedia",
                    local_path="x.jpg", thumbnail_path="t.jpg", rights_status="public_domain",
                    found_for="Fall River", **kw)
    db.add(a)
    db.commit()
    return a


ENTITIES = [{"key": "fall_river", "name": "Fall River", "type": "place"}]


def test_a_decisive_cheap_verdict_is_used_without_calling_the_strong_verifier(db_session, monkeypatch):
    monkeypatch.setattr(VV, "image_for_check", lambda a: "t.jpg")
    monkeypatch.setattr(VV.IM, "data_url", lambda p: "data:image/jpeg;base64,")
    case = _case(db_session)
    a = _photo(db_session, case, "A1")
    agent = VV.VisualVerificationAgent.__new__(VV.VisualVerificationAgent)
    agent.gen = FakeGen({"visual_verifier_cheap": v(entities=[])})
    asyncio.run(agent.verify(db_session, case, a, ENTITIES, []))
    assert agent.gen.calls == ["visual_verifier_cheap"]  # the strong tier was never called
    assert a.verification_status == "verified"
    assert json.loads(a.verification_json)["check_tier"] == "cheap"


def test_an_undecisive_cheap_verdict_escalates_to_the_strong_verifier(db_session, monkeypatch):
    monkeypatch.setattr(VV, "image_for_check", lambda a: "t.jpg")
    monkeypatch.setattr(VV.IM, "data_url", lambda p: "data:image/jpeg;base64,")
    case = _case(db_session)
    a = _photo(db_session, case, "A2")
    agent = VV.VisualVerificationAgent.__new__(VV.VisualVerificationAgent)
    agent.gen = FakeGen({
        "visual_verifier_cheap": v(matches_claim="unclear", confidence=0.4, entities=[]),
        "visual_verifier": v(confidence=0.9, entities=[]),
    })
    asyncio.run(agent.verify(db_session, case, a, ENTITIES, []))
    assert agent.gen.calls == ["visual_verifier_cheap", "visual_verifier"]
    assert json.loads(a.verification_json)["check_tier"] == "strong"


def test_a_person_always_escalates_to_the_strong_verifier_even_at_full_cheap_confidence(
        db_session, monkeypatch):
    monkeypatch.setattr(VV, "image_for_check", lambda a: "t.jpg")
    monkeypatch.setattr(VV.IM, "data_url", lambda p: "data:image/jpeg;base64,")
    case = _case(db_session)
    a = _photo(db_session, case, "A3")
    agent = VV.VisualVerificationAgent.__new__(VV.VisualVerificationAgent)
    agent.gen = FakeGen({
        "visual_verifier_cheap": v(subject_type="person", confidence=0.99, entities=[]),
        "visual_verifier": v(subject_type="person", confidence=0.95, entities=[]),
    })
    asyncio.run(agent.verify(db_session, case, a, ENTITIES, []))
    assert agent.gen.calls == ["visual_verifier_cheap", "visual_verifier"]
    assert json.loads(a.verification_json)["check_tier"] == "strong"


def test_the_cheap_tier_can_be_switched_off(db_session, monkeypatch):
    monkeypatch.setattr(VV, "image_for_check", lambda a: "t.jpg")
    monkeypatch.setattr(VV.IM, "data_url", lambda p: "data:image/jpeg;base64,")
    monkeypatch.setattr(ai_config.visual_verification, "cheap_tier_enabled", False)
    case = _case(db_session)
    a = _photo(db_session, case, "A4")
    agent = VV.VisualVerificationAgent.__new__(VV.VisualVerificationAgent)
    agent.gen = FakeGen({"visual_verifier": v(entities=[])})
    asyncio.run(agent.verify(db_session, case, a, ENTITIES, []))
    assert agent.gen.calls == ["visual_verifier"]
    assert json.loads(a.verification_json)["check_tier"] == "strong"
