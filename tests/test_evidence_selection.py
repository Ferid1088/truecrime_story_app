"""The fact cap of 100: story pack and research stages share one selector."""

import json

from app.agents.research import ResearchAgent
from app.core.ai_config import ai_config
from app.db.models import Case, Contradiction, Fact, Source
from app.services.evidence_selection import EvidenceSelector


def _fact(i, **kw):
    base = dict(id=i, case_id=1, claim=f"Claim number {i}", confidence=0.5, category="evidence",
                narrative_value="context", evidence_strength="secondary", source_ids_json="[1]")
    return Fact(**{**base, **kw})


def test_the_cap_is_100_in_story_pack_and_research_stages():
    assert ai_config.story_quality.max_evidence_per_story == 100
    assert ai_config.story_quality.max_evidence_per_act <= 100
    assert ai_config.evidence_selection.max_facts_per_research_stage == 100
    kept, dropped = EvidenceSelector(100).select([_fact(i) for i in range(1, 401)])
    assert len(kept) == 100 and len(dropped) == 300
    assert all("ranked below the evidence diet cap" in d["reason"] for d in dropped)


def test_video_backed_facts_win_ties_over_equal_web_facts():
    video_source = Source(id=2, case_id=1, title="v", url="u", source_type="youtube_video")
    web = [_fact(i) for i in range(1, 11)]
    via_source = _fact(11, source_ids_json="[2]")
    via_claims = _fact(12, transcript_claim_ids_json="[5, 6]")
    kept, _ = EvidenceSelector(2, [video_source]).select(web + [via_source, via_claims])
    assert {f.id for f in kept} == {11, 12}


def test_video_backed_ranks_before_a_stronger_web_fact():
    primary_web = _fact(1, evidence_strength="primary")
    weak_video = _fact(2, evidence_strength="weak", transcript_claim_ids_json="[1]")
    kept, _ = EvidenceSelector(1).select([primary_web, weak_video])
    assert [f.id for f in kept] == [2]


def test_contradiction_and_disputed_facts_are_never_dropped():
    in_contradiction = _fact(1, claim="Andrew Borden suffered eleven wounds", evidence_strength="weak",
                             source_ids_json="[7]")
    unrelated_same_source = _fact(2, claim="Weather was warm that morning", evidence_strength="weak",
                                  source_ids_json="[7]")
    disputed = _fact(3, evidence_strength="weak", disputed=True)
    strong = [_fact(i, evidence_strength="primary") for i in range(10, 30)]
    contradiction = Contradiction(case_id=1, topic="Number of wounds to Andrew Borden",
                                  description="Andrew Borden had ten or eleven wounds",
                                  source_ids_json="[7]")
    kept, dropped = EvidenceSelector(2, [], [contradiction]).select(
        [in_contradiction, unrelated_same_source, disputed] + strong)
    ids = {f.id for f in kept}
    assert {1, 3} <= ids
    assert 2 not in ids and 2 in {d["fact_id"] for d in dropped}


def test_research_stage_gets_at_most_100_facts_without_deleting_any(db_session):
    case = Case(canonical_title="Cap case", slug="cap-case")
    db_session.add(case)
    db_session.commit()
    facts = [Fact(case_id=case.id, claim=f"Claim number {i}", source_ids_json="[1]") for i in range(150)]
    db_session.add_all(facts)
    db_session.commit()
    kept = ResearchAgent._facts_for_stage(db_session, case, facts, [])
    assert len(kept) == 100
    assert db_session.query(Fact).filter(Fact.case_id == case.id).count() == 150


def test_storing_web_facts_keeps_the_facts_promoted_from_videos(db_session):
    case = Case(canonical_title="Keep video facts", slug="keep-video-facts")
    db_session.add(case)
    db_session.commit()
    db_session.add_all([
        Fact(case_id=case.id, claim="From a video", transcript_claim_ids_json=json.dumps([1])),
        Fact(case_id=case.id, claim="Old web fact")])
    db_session.commit()
    agent = ResearchAgent.__new__(ResearchAgent)
    agent._store_facts(db_session, case, [{"claim": "New web fact", "source_ids": []}], [])
    db_session.commit()
    claims = {f.claim for f in db_session.query(Fact).filter(Fact.case_id == case.id)}
    assert claims == {"From a video", "New web fact"}
