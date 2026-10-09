"""CaseNamingAgent / critic / gates / approval with a scripted LLM (no network)."""

import asyncio
import json

import pytest

from app.db.models import (Case, CaseTitleCandidate, EditorialBlueprint, Fact, Source,
                           StoryVersion)
from app.identity import titles as T
from app.lifecycle.status import set_resolution
from app.naming import agent as A
from app.naming import gates as G
from app.naming.context import build_context

EN = ["The Lantern Of Celle", "Seven Doors Harbor", "The Borrowed Boat", "A Lamp For Rita",
      "The Salt Road Clock", "Nine Steps Upstream", "The Quiet Ferryman", "Window Seat Eleven",
      "The Copper Kettle Night", "Harbor Light Eleven"]
DE = ["Die Laterne von Celle", "Das geliehene Boot", "Der stille Fährmann", "Hafenlicht Nummer elf",
      "Die Salzstraße", "Neun Stufen flussauf", "Das Kupferkessel-Rätsel", "Lampe für Rita"]
FA = ["فانوس سلله", "قایق قرضی", "پایاب خاموش", "چراغ بندر یازده", "جاده‌ی نمک",
      "نه پله تا رود", "کتری مسی", "پل بی‌نشان"]
AR = ["فانوس سيله", "القارب المستعار", "العبّارة الصامتة", "مصباح الميناء", "طريق الملح",
      "تسع درجات", "غلاية النحاس", "جسر بلا اسم"]


class ScriptedGen:
    def __init__(self, proposals, bad=None):
        self.proposals = {k: list(v) for k, v in proposals.items()}
        self.bad = bad or {}
        self.calls = []

    def is_configured(self):
        return True

    async def generate_structured(self, role, system, user, images=None):
        self.calls.append(role)
        d = json.loads(user)
        if role == "case_naming_agent":
            lang = {"English": "en", "German": "de", "Persian (Farsi)": "fa",
                    "Arabic": "ar"}[d["language"]]
            pool = self.proposals[lang]
            take, self.proposals[lang] = pool[:d["count"]], pool[d["count"]:]
            return {"editorial_concept": "a lamp that was left burning",
                    "candidates": [{"title": t, "angle": "lamp"} for t in take]}, None
        out = []
        for t in d["titles"]:
            b = self.bad.get(t, {})
            if role == "case_title_critic":
                out.append({"title": t, "specificity": 0.8, "memorability": 0.7, "curiosity": 0.7,
                            "documentary_tone": 0.8, "sensationalism_risk": 0.1,
                            "spoiler_risk": 0.1, "epistemic_risk": 0.1, "reason": "ok", **b})
            else:
                out.append({"title": t, "native_quality": 0.9, "literal_translation": False,
                            "reason": "ok", **b})
        return {"scores": out}, None


@pytest.fixture(autouse=True)
def _clean_titles(db_session):
    """Every test starts without earlier tests' candidates and identities."""
    from app.db.models import EpisodeIdentity

    db_session.query(CaseTitleCandidate).delete()
    db_session.query(EpisodeIdentity).delete()
    db_session.commit()


def _case(db, name="Celle lantern", status="UNSOLVED"):
    case = Case(canonical_title=name, slug=name.lower().replace(" ", "-") + "-nm",
                location="Celle", people_json=json.dumps(["Rita Haas"]))
    db.add(case)
    db.flush()
    set_resolution(db, case, status, changed_by="user", reason="t", confidence=0.9, commit=False)
    db.commit()
    return case


def _run(coro):
    return asyncio.run(coro)


def test_exactly_seven_per_language_with_replacement(db_session):
    case = _case(db_session)
    gen = ScriptedGen({"en": EN, "de": DE, "fa": FA, "ar": AR},
                      bad={EN[0]: {"spoiler_risk": 0.9}})
    out = _run(A.generate_case_titles(db_session, case, gen))
    for lang in ("en", "de", "fa", "ar"):
        assert out["languages"][lang]["eligible"] == 7 and out["languages"][lang]["short_by"] == 0
        rows = db_session.query(CaseTitleCandidate).filter_by(case_id=case.id, language=lang)
        assert rows.filter(CaseTitleCandidate.status != "rejected").count() == 7
    en_rows = db_session.query(CaseTitleCandidate).filter_by(case_id=case.id, language="en").all()
    rejected = [r for r in en_rows if r.status == "rejected"]
    assert any(r.title == EN[0] and "spoiler" in r.rejection_reason for r in rejected)
    assert len({r.title_family_id for r in db_session.query(CaseTitleCandidate).all()
                if r.case_id == case.id}) == 1
    best = [json.loads(r.critic_json) for r in en_rows if r.status != "rejected"]
    assert sum(1 for b in best if b["recommended"]) == 1
    assert "native_title_critic" in gen.calls and gen.calls.count("native_title_critic") == 3


def test_shortfall_is_reported_not_padded(db_session):
    case = _case(db_session, "Celle shortfall")
    gen = ScriptedGen({"en": EN[:3]})
    out = _run(A.generate_case_titles(db_session, case, gen, languages=["en"]))
    assert out["languages"]["en"]["eligible"] == 3 and out["languages"]["en"]["short_by"] == 4


def test_rejected_title_is_not_regenerated(db_session):
    case = _case(db_session, "Celle history")
    gen = ScriptedGen({"en": EN}, bad={EN[0]: {"specificity": 0.1}})
    _run(A.generate_case_titles(db_session, case, gen, languages=["en"]))
    gen2 = ScriptedGen({"en": [EN[0].lower(), "The Brass Ferry Bell"]})
    row = _run(A.evaluate_manual(db_session, case, "en", EN[0].lower(), gen2))
    assert row.status == "rejected" and "previously rejected" in row.rejection_reason


@pytest.mark.parametrize("title,language,reason", [
    ("The Dark Secret", "en", "generic"),
    ("A Very Long Title That Keeps On Going Forever", "en", "too long"),
    ("Single", "en", "too short"),
    ("THE MISSING GIRL", "en", "ALL CAPS"),
    ("Where Did Rita Go?", "en", "question"),
    ("Episode 12 The Lamp", "en", "episode number"),
    ("Das dunkle Geheimnis", "de", "generic"),
    ("راز تاریک", "fa", "generic"),
    ("The Silent Orchard", "fa", "Arabic script"),
    ("Rad Khamoosh", "ar", "Arabic script"),
])
def test_deterministic_gates(title, language, reason):
    assert reason in (G.run_gates(title, language, {"claim_limits": {}}) or "")


def test_specific_title_passes_and_generic_is_penalized():
    ctx = {"claim_limits": {}, "canonical_case_name": "Celle lantern"}
    assert G.run_gates("The Lantern Of Celle", "en", ctx) is None
    assert G.run_gates("The Final Night", "en", ctx)
    assert G.brevity_score("The Lantern Of Celle") > G.brevity_score(
        "The Lantern Of The Old Celle Harbor Road")


def test_spoiler_and_epistemic_gates():
    ctx = {"reveal_terms": ["brother", "basement"],
           "claim_limits": {"may_state_guilt": False}}
    assert "withheld" in G.run_gates("The Basement Door", "en", ctx)
    assert G.run_gates("The Lantern Door", "en", ctx) is None
    assert "not established" in G.run_gates("The Father Who Killed Them", "en", ctx)
    assert "not established" in G.run_gates("Der Mörder von Celle", "de", ctx)
    assert "not established" in G.run_gates("قاتل سله", "fa", ctx)
    assert "not established" in G.run_gates("القاتل الصامت", "ar", ctx)
    assert G.run_gates("The Father Who Disappeared With Them", "en", ctx) is None
    solved = {**ctx, "claim_limits": {"may_state_guilt": True}}
    assert G.run_gates("The Killer Next Door", "en", solved) is None


def test_non_native_copy_is_rejected():
    assert "not native" in G.run_gates("Harbor Light", "de", {"claim_limits": {}},
                                       {"en": ["Harbor Light"]})


def test_native_critic_rejects_literal_translation(db_session):
    case = _case(db_session, "Celle native")
    lit = DE[0]
    gen = ScriptedGen({"en": EN, "de": DE},
                      bad={lit: {"literal_translation": True}})
    _run(A.generate_case_titles(db_session, case, gen, languages=["en", "de"]))
    row = db_session.query(CaseTitleCandidate).filter_by(case_id=case.id, title=lit).one()
    assert row.status == "rejected" and "literal translation" in row.rejection_reason


def test_context_hides_nothing_public_and_holds_back_late_reveals(db_session):
    case = _case(db_session, "Celle context")
    f1 = Fact(case_id=case.id, claim="Rita Haas vanished from Celle in 1994", confidence=0.9)
    f2 = Fact(case_id=case.id, claim="Her brother Jonas hid a ledger in the cellar", confidence=0.9)
    db_session.add_all([f1, f2])
    sv = StoryVersion(case_id=case.id, version=1, narrative_angle="{}", story_text="x",
                      engagement_score=0.5)
    db_session.add(sv)
    db_session.flush()
    bp = {"beats": [{"id": "B01", "reveals": ["F001"]}, {"id": "B02", "reveals": []},
                    {"id": "B03", "reveals": ["F002"]}]}
    db_session.add(EditorialBlueprint(case_id=case.id, story_version_id=sv.id, status="valid",
                                      blueprint_json=json.dumps(bp),
                                      central_question="What happened to Rita?"))
    db_session.commit()
    ctx = build_context(db_session, case)
    assert "ledger" in ctx["reveal_terms"] and "cellar" in ctx["reveal_terms"]
    assert "celle" not in ctx["reveal_terms"] and "vanished" not in ctx["reveal_terms"]
    assert ctx["claim_limits"]["may_state_guilt"] is False
    assert ctx["resolution_status"] == "unsolved"
    assert G.run_gates("The Ledger Door", "en", ctx)


def test_approval_writes_identity_and_manual_titles_are_rechecked(db_session):
    case = _case(db_session, "Celle approve")
    gen = ScriptedGen({"en": EN, "de": DE})
    _run(A.generate_case_titles(db_session, case, gen, languages=["en", "de"]))
    de = db_session.query(CaseTitleCandidate).filter_by(
        case_id=case.id, language="de").filter(CaseTitleCandidate.status == "candidate").first()
    ident = A.approve_candidate(db_session, case, de.id)
    assert ident.editorial_title == de.title and ident.title_family_id == de.title_family_id
    assert ident.youtube_title.endswith("(Ungelöst) | Fallspur") and "Folge" not in ident.youtube_title
    # a manual title goes through gates + collision + critics before approval
    bad = _run(A.evaluate_manual(db_session, case, "en", "The Killer Next Door", gen))
    assert bad.status == "rejected" and "not established" in bad.rejection_reason
    with pytest.raises(A.ApprovalRefused):
        A.approve_candidate(db_session, case, bad.id)
    T.mark_published(db_session, ident)
    other = db_session.query(CaseTitleCandidate).filter(
        CaseTitleCandidate.case_id == case.id, CaseTitleCandidate.language == "de",
        CaseTitleCandidate.status == "candidate").first()
    with pytest.raises(T.TitleLocked):
        A.approve_candidate(db_session, case, other.id)
    assert A.approve_candidate(db_session, case, other.id, revise=True).title_version == 2


def test_api_flow(client, db_session, monkeypatch):
    import app.naming.api as N

    case = _case(db_session, "Celle api")
    gen = ScriptedGen({"en": EN, "de": DE, "fa": FA, "ar": AR})
    monkeypatch.setattr(N, "_gen", lambda: gen)
    monkeypatch.setattr(N, "_embedder", lambda: None)
    r = client.post(f"/api/cases/{case.id}/naming/generate", json={}).json()
    assert all(r["languages"][l]["eligible"] == 7 for l in ("en", "de", "fa", "ar"))
    state = client.get(f"/api/cases/{case.id}/naming").json()
    assert state["externally_verified"] is False and state["title_family_id"]
    fa = state["languages"]["fa"]["candidates"]
    assert len(fa) == 7 and fa[0]["recommended"] and fa[0]["collision_status"] == "clear"
    ok = client.post(f"/api/cases/{case.id}/naming/{fa[0]['id']}/approve").json()
    assert ok["youtube_title"].endswith("(حل‌نشده) | رد خاموش")
