"""The evidence pack the story plan and the writers read: strongest-first selection,
provenance, act word budgets, capacity and the size of the director's input."""
import asyncio
import json
import uuid

from app.core.ai_config import ai_config
from app.providers.generation.base import GenerationResult
from app.utils import strip_narration_artifacts


def _mk_case(db):
    from app.db.models import Case
    from app.utils import slugify

    title = f"MP Case {uuid.uuid4().hex[:8]}"
    c = Case(canonical_title=title, slug=slugify(title), language="en")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _add_fact(db, case, claim="F", **kw):
    from app.db.models import Fact

    f = Fact(
        case_id=case.id, claim=claim,
        category=kw.pop("category", "context"),
        confidence=kw.pop("confidence", 0.9), **kw,
    )
    db.add(f)
    db.commit()
    return f


# ---------------------------------------------------------------------------
# Act word budgets
# ---------------------------------------------------------------------------


def test_act_budgets_normalized_to_target():
    from app.agents.story import normalize_act_budgets

    plan = {"acts": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    normalize_act_budgets(plan, 6000)
    total = sum(a["target_words"] for a in plan["acts"])
    assert abs(total - 6000) <= len(plan["acts"])
    min_words = 6000 * ai_config.story_plan.min_act_budget_share
    assert all(a["target_words"] >= min_words for a in plan["acts"])


def test_budget_rescaling_preserves_proportions():
    from app.agents.story import normalize_act_budgets

    plan = {"acts": [
        {"id": "a", "target_words": 10},
        {"id": "b", "target_words": 30},
    ]}
    normalize_act_budgets(plan, 4000)
    assert plan["acts"][1]["target_words"] > plan["acts"][0]["target_words"]
    assert abs(
        sum(a["target_words"] for a in plan["acts"]) - 4000
    ) <= 2


# ---------------------------------------------------------------------------
# Surgical grounding repair
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Targeted revision ops
# ---------------------------------------------------------------------------


def _three_sections():
    return [
        {"id": "a1", "text": " ".join(["one"] * 100)},
        {"id": "a2", "text": " ".join(["two"] * 100)},
        {"id": "a3", "text": " ".join(["three"] * 100)},
    ]


# ---------------------------------------------------------------------------
# Evidence plan / coverage
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Capacity gate + italic markdown
# ---------------------------------------------------------------------------


def test_capacity_endpoint_reports(client, db_session):
    """Sufficiently deep evidence supports the requested duration: mixed
    full/partial source depth, human + scene + investigation details,
    quotes, a dense timeline and contradictions -> ready.

    Bare fact counts alone must not inflate the estimate (see the
    shallow-evidence counterpart test below)."""
    from app.db.models import Contradiction, Source

    case = _mk_case(db_session)
    for i in range(4):
        db_session.add(
            Source(
                case_id=case.id, title=f"F{i}", url=f"https://e/f{i}",
                source_type="article", content_status="full_text",
                raw_text="text " * 400, source_family=f"fam{i}",
            )
        )
    for i in range(3):
        db_session.add(
            Source(
                case_id=case.id, title=f"P{i}", url=f"https://e/p{i}",
                source_type="article", content_status="partial_text",
                raw_text="text " * 100, source_family=f"pfam{i}",
            )
        )
    # Narrative-value mix across every typed bucket, all dated.
    nvs = ["human_detail", "scene_detail", "investigation", "quote"]
    for i in range(40):
        _add_fact(
            db_session, case, f"fact {i}",
            narrative_value=nvs[i % len(nvs)],
            event_date=f"2000-01-{(i % 28) + 1:02d}",
        )
    for i in range(3):
        db_session.add(
            Contradiction(
                case_id=case.id, topic=f"t{i}",
                description="accounts differ", severity="medium",
            )
        )
    db_session.commit()

    r = client.get(
        f"/api/cases/{case.id}/research/capacity?target_minutes=45"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["estimated_supported_minutes"] >= body["minimum_required_minutes"]
    depth = body["depth"]
    assert depth["full_text_sources"] == 4
    assert depth["partial_text_sources"] == 3
    assert depth["human_details"] > 0
    assert depth["scene_details"] > 0
    assert depth["investigation_details"] > 0
    assert depth["quotes"] > 0
    assert depth["timeline_events"] == 40
    assert depth["contradictions"] == 3
    assert depth["independent_source_families"] == 7


def test_capacity_shallow_evidence_insufficient(client, db_session):
    """Many facts but only shallow evidence (summary-only sources, untyped
    claims, no dates) must NOT be judged ready — volume ≠ depth."""
    from app.db.models import Source

    case = _mk_case(db_session)
    for i in range(10):
        db_session.add(
            Source(
                case_id=case.id, title=f"Shallow{i}", url=f"https://e/s{i}",
                source_type="article", content_status="summary_only",
                publisher=f"pub{i}",
            )
        )
    db_session.commit()
    for i in range(40):
        _add_fact(db_session, case, f"bare fact {i}")

    r = client.get(
        f"/api/cases/{case.id}/research/capacity?target_minutes=45"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "insufficient_for_requested_length"
    assert body["estimated_supported_minutes"] < body["minimum_required_minutes"]
    assert "deep source text" in body["weak_areas"]


def test_italic_bold_markdown_stripped():
    out = strip_narration_artifacts(
        "The *Hesperus* arrived; **three men** were gone. Rate: 3 * 4."
    )
    assert "*Hesperus*" not in out and "Hesperus" in out
    assert "**three men**" not in out and "three men" in out
    assert "3 * 4" in out


# ---------------------------------------------------------------------------
# improve() kind preservation
# ---------------------------------------------------------------------------


def test_director_prompt_stays_small_for_a_case_with_thousands_of_facts(monkeypatch):
    """1,700 extracted facts, each repeated in the disputed list and in the
    narrative buckets, made a 1.18M-token director prompt (limit 1M). The
    director plans with one short line per item; the act writers still get
    full detail by id."""
    from app.agents.story import build_evidence_pack, director_view
    from app.db.models import Fact

    monkeypatch.setattr(ai_config.story_quality, "max_evidence_per_story", 5000)
    facts = [
        Fact(id=i, case_id=1, claim=f"Claim {i} " + "word " * 80, confidence=0.9,
             category="evidence", narrative_value=["human_detail", "quote", "scene_detail"][i % 3],
             evidence_strength="secondary", original_claim="Originaltext " * 40,
             original_language="de", supporting_text="verbatim passage " * 30,
             source_ids_json="[1,2]", chunk_ids_json="[3,4]", event_date="1924-05-21")
        for i in range(1, 1701)
    ]
    pack = build_evidence_pack(facts, [], [])
    view = director_view(pack)

    assert len(json.dumps(view, ensure_ascii=False)) < len(json.dumps(pack, ensure_ascii=False)) / 5
    assert len(view["facts"]) == 1700
    item = view["facts"][0]
    assert item["id"] == "F001" and len(item["claim"]) <= 220
    assert not {"original_claim", "supporting_text", "source_ids", "chunk_ids"} & set(item)
    assert view["human_details"][0] == "F003" and view["approved_quotes"][0] == "F001"
    assert pack["facts"][0]["supporting_text"]            # the full pack is untouched


def test_director_is_told_how_many_evidence_ids_an_act_can_use(db_session):
    """Left to itself the director listed ids for most of 1,700 facts in every
    act and its plan overran max_tokens; an act only uses the strongest
    max_evidence_per_act, and the director is told so."""
    from app.agents.story import StoryDirector
    from app.core.prompts import prompt

    case = _mk_case(db_session)
    seen = {}

    class Spy:
        async def generate_structured(self, role, system, user, images=None):
            seen["user"] = json.loads(user)
            return {"acts": []}, GenerationResult(text="{}", model="m", provider="fake")

    director = StoryDirector()
    director.gen = Spy()
    asyncio.run(director.design(case, {"facts": []}, 45, "fa"))
    assert seen["user"]["max_evidence_ids_per_act"] == ai_config.story_quality.max_evidence_per_act
    assert "max_evidence_ids_per_act" in prompt("agents/story/design")


def test_evidence_pack_holds_a_diverse_selection_of_the_strongest_facts(monkeypatch):
    """Every whole-story check serialized all 1,700 facts (over a million
    tokens, and money for each call). The pack keeps story_quality.
    max_evidence_per_story facts: the strongest, round-robin over fact types,
    numbered in their original order, the same selection every time."""
    from app.agents.story import build_evidence_pack
    from app.db.models import Fact

    monkeypatch.setattr(ai_config.story_quality, "max_evidence_per_story", 40)
    values = ["human_detail", "scene_detail", "quote", "investigation_detail"]
    strengths = ["weak", "secondary", "primary"]
    facts = [Fact(id=i, case_id=1, claim=f"Claim {i}", confidence=0.5 + (i % 5) / 10,
                  category="evidence", narrative_value=values[i % 4],
                  evidence_strength=strengths[(i // 4) % 3], source_ids_json="[1]")
             for i in range(1, 401)]
    pack = build_evidence_pack(facts, [], [])

    assert len(pack["facts"]) == 40
    kinds = [f["narrative_value"] for f in pack["facts"]]
    assert {kinds.count(v) for v in values} == {10}
    assert {f["evidence_strength"] for f in pack["facts"]} == {"primary"}
    claims = [f["claim"] for f in pack["facts"]]
    assert [f["id"] for f in pack["facts"]] == [f"F{i:03d}" for i in range(1, 41)]
    assert claims == sorted(claims, key=lambda c: int(c.split()[1]))     # original order
    assert build_evidence_pack(list(reversed(facts)), [], [])["facts"] == pack["facts"]
    assert len(build_evidence_pack(facts[:10], [], [])["facts"]) == 10


def test_evidence_pack_prefers_corroborated_facts_and_reports_what_it_dropped(monkeypatch):
    """Part 2(c): among facts tied on evidence_strength and confidence, the
    one backed by more independent sources (post-merge corroboration)
    survives the cap; every fact the cap cuts is named in the pack with a
    reason — nothing is a silent drop."""
    from app.agents.story import build_evidence_pack
    from app.db.models import Fact

    monkeypatch.setattr(ai_config.story_quality, "max_evidence_per_story", 1)
    weak = Fact(id=1, case_id=1, claim="Only one source", confidence=0.5,
               category="evidence", narrative_value="context",
               evidence_strength="secondary", source_ids_json="[1]")
    strong = Fact(id=2, case_id=1, claim="Three independent sources agree", confidence=0.5,
                 category="evidence", narrative_value="context",
                 evidence_strength="secondary", source_ids_json="[1, 2, 3]")
    pack = build_evidence_pack([weak, strong], [], [])

    assert [f["claim"] for f in pack["facts"]] == ["Three independent sources agree"]
    assert pack["evidence_diet"]["cap"] == 1
    assert pack["evidence_diet"]["kept"] == 1
    assert [d["fact_id"] for d in pack["evidence_diet"]["dropped"]] == [1]
    assert "evidence diet cap" in pack["evidence_diet"]["dropped"][0]["reason"]


def test_evidence_pack_prefers_a_high_reliability_source_over_a_low_one(monkeypatch):
    """Part 3(e) item 2: "a fact from a high-quality video counts at least
    as much as one from a news page" — Source.reliability_score (now real
    for video sources, app.services.claims.video_source_reliability_score)
    is a ranking tier here for the first time, independent of corroboration
    or evidence_strength, which tie in this fixture on purpose."""
    from app.agents.story import build_evidence_pack
    from app.db.models import Fact, Source

    monkeypatch.setattr(ai_config.story_quality, "max_evidence_per_story", 1)
    low_source = Source(id=1, case_id=1, title="Low", url="https://e/1",
                        source_type="article", reliability_score=0.3)
    high_source = Source(id=2, case_id=1, title="High-quality video", url="https://e/2",
                         source_type="youtube_video", reliability_score=0.9)
    from_low = Fact(id=1, case_id=1, claim="From the low-reliability source", confidence=0.5,
                    category="evidence", narrative_value="context",
                    evidence_strength="secondary", source_ids_json="[1]")
    from_high = Fact(id=2, case_id=1, claim="From the high-reliability video", confidence=0.5,
                     category="evidence", narrative_value="context",
                     evidence_strength="secondary", source_ids_json="[2]")
    pack = build_evidence_pack([from_low, from_high], [], [low_source, high_source])

    assert [f["claim"] for f in pack["facts"]] == ["From the high-reliability video"]


def test_evidence_pack_flags_single_source_facts():
    """Part 3(e) item 2: "keep the single-source flag for facts only one
    [source] claims, and show it.\""""
    from app.agents.story import build_evidence_pack
    from app.db.models import Fact

    single = Fact(id=1, case_id=1, claim="Only one source claims this", confidence=0.9,
                  category="evidence", narrative_value="context",
                  evidence_strength="secondary", source_ids_json="[1]")
    corroborated = Fact(id=2, case_id=1, claim="Two sources agree", confidence=0.9,
                        category="evidence", narrative_value="context",
                        evidence_strength="secondary", source_ids_json="[1, 2]")
    pack = build_evidence_pack([single, corroborated], [], [])
    by_claim = {f["claim"]: f["single_source"] for f in pack["facts"]}
    assert by_claim["Only one source claims this"] is True
    assert by_claim["Two sources agree"] is False


def test_evidence_pack_never_drops_a_fact_a_contradiction_depends_on(monkeypatch):
    """Part 2(c): "keep every item... a contradiction depends on" — a fact
    sharing a source and terms with a stored Contradiction survives the cap even
    when every other signal (evidence_strength, corroboration, confidence)
    would have ranked it last."""
    from app.agents.story import build_evidence_pack
    from app.db.models import Contradiction, Fact

    monkeypatch.setattr(ai_config.story_quality, "max_evidence_per_story", 1)
    weak_but_cited = Fact(id=1, case_id=1, claim="Andrew Borden wounds are disputed", confidence=0.1,
                          category="evidence", narrative_value="context",
                          evidence_strength="weak", source_ids_json="[9]")
    strong_unrelated = Fact(id=2, case_id=1, claim="Unrelated strong fact", confidence=0.9,
                            category="evidence", narrative_value="context",
                            evidence_strength="primary", source_ids_json="[1, 2, 3]")
    contradiction = Contradiction(id=1, case_id=1, topic="Andrew Borden wounds", description="Accounts of the wounds differ",
                                  source_ids_json="[9]", severity="high")
    pack = build_evidence_pack([weak_but_cited, strong_unrelated], [contradiction], [])

    claims = {f["claim"] for f in pack["facts"]}
    assert claims == {"Andrew Borden wounds are disputed"}
    assert [d["fact_id"] for d in pack["evidence_diet"]["dropped"]] == [2]
