"""Section D2 — dispatch builders for the tools beyond fact_card/floor_plan/timeline. Each test
proves a builder produces data that the REAL renderer (Graphic.can_render + draw_state, or an
engine's equivalent) actually accepts and draws, not just a plausible-looking dict."""

from __future__ import annotations

import json

from app.documentary.detective import dispatch as D
from app.documentary import memory as MEM
from app.documentary.graphics.registry import GRAPHICS
from app.documentary.graphics.engines.evidence import ChainOfCustody, MapOfUnknowns
from app.documentary.graphics.engines.trial import CourtroomReasoning, EvidenceCheck, SentencingExplanation
from app.documentary.graphics.engines.story import InterrogationAnalysis, InvestigationClock
from app.db.models import Fact

from tests.test_detective_section_d import _graph, _scene_with_action


def _bind(action, db_session, **keys):
    action.content_bindings_json = json.dumps([{k: v} for k, v in keys.items()])
    db_session.flush()


def test_detective_board_builder_renders_T02(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "p1", "Jane Doe", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "p1", "role", "Jane was home alone.", "established_in_text")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "detective_board")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}])
    db_session.flush()
    data = D._detective_board_data(db_session, action)
    assert data == {"cards": [{"id": "c1", "title": "Jane was home alone.", "fact_id": "c1"}]}


def test_recap_builder_renders_T03(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "p1", "Jane Doe", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "p1", "role", "Jane was home.", "established_in_text")
    MEM.add_claim(db_session, graph.id, "c2", "p1", "role", "Ages disputed.", "disputed")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "recap")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}, {"claim_key": "c2"}])
    db_session.flush()
    data = D._recap_data(db_session, action)
    assert GRAPHICS["recap"].can_render(data) is None
    GRAPHICS["recap"].draw_state(data, "en", 1)


def test_unknowns_builder_renders_T04(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "p1", "Jane Doe", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "p1", "role", "Jane was home.", "established_in_text")
    MEM.add_question(db_session, graph.id, "q1", ["p1"], "Who else was there?", "not_yet_explained")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "unknowns")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}, {"question_key": "q1"}])
    db_session.flush()
    data = D._unknowns_data(db_session, action)
    assert MapOfUnknowns().can_render(data) is None
    MapOfUnknowns().draw(json.dumps(data), "en", 2)


def test_conflict_builder_renders_T05(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "src-a", "The Guardian", "organization", "B01", 0)
    MEM.ensure_entity(db_session, graph.id, "src-b", "NBC News", "organization", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "src-a", "age", "The Guardian said nine and eleven.",
                 "attributed", attribution="The Guardian")
    MEM.add_claim(db_session, graph.id, "c2", "src-b", "age", "NBC said nine and twelve.",
                 "attributed", attribution="NBC News")
    MEM.add_relation(db_session, graph.id, "r1", "src-a", "src-b", "conflict", "Daughter's age disputed")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "conflict")
    action.content_bindings_json = json.dumps(
        [{"relation_key": "r1"}, {"claim_key": "c1"}, {"claim_key": "c2"}])
    db_session.flush()
    data = D._conflict_data(db_session, action)
    assert GRAPHICS["conflict"].can_render(data) is None
    GRAPHICS["conflict"].draw_state(data, "en", 1)


def test_conflict_board_builder_renders_T06(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "a", "A", "organization", "B01", 0)
    MEM.ensure_entity(db_session, graph.id, "b", "B", "organization", "B01", 0)
    MEM.ensure_entity(db_session, graph.id, "c", "C", "organization", "B01", 0)
    MEM.add_relation(db_session, graph.id, "r1", "a", "b", "conflict", "First disagreement")
    MEM.add_relation(db_session, graph.id, "r2", "b", "c", "conflict", "Second disagreement")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "conflict_board")
    action.content_bindings_json = json.dumps([{"relation_key": "r1"}, {"relation_key": "r2"}])
    db_session.flush()
    data = D._conflict_board_data(db_session, action)
    assert GRAPHICS["conflict_board"].can_render(data) is None
    GRAPHICS["conflict_board"].draw_state(data, "en", 1)


def test_key_facts_builder_renders_T10(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "p1", "Jane Doe", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "p1", "age", "32 is Jane's age.", "established_in_text")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "key_facts")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}])
    db_session.flush()
    data = D._key_facts_data(db_session, action)
    assert GRAPHICS["key_facts"].can_render(data) is None
    GRAPHICS["key_facts"].draw_state(data, "en", 1)


def _quote_claim(db_session, url):
    """An attributed claim linked to a fact whose one stored source has the given address."""
    from app.db.models import Source

    graph, _, case = _graph(db_session, beats=[{"id": "B01"}])
    source = Source(case_id=case.id, url=url, title="t", publisher="p", source_type="news", language="en")
    db_session.add(source)
    db_session.flush()
    fact = Fact(case_id=case.id, claim="x", source_ids_json=json.dumps([source.id]))
    db_session.add(fact)
    db_session.flush()
    MEM.ensure_entity(db_session, graph.id, "p1", "Caleb Flynn", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "p1", "statement", "Somebody broke into my home.",
                 "attributed", attribution="Caleb Flynn", evidence_fact_ids=[fact.id])
    _, action = _scene_with_action(db_session, graph, "B01", 0, "quote")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}])
    db_session.flush()
    return action


def test_quote_builder_renders_T12(db_session):
    action = _quote_claim(db_session, "https://www.bbc.co.uk/news/world-us-canada-1")
    data = D._quote_data(db_session, action)
    assert data["source"] == "BBC News"
    assert GRAPHICS["quote"].can_render(data) is None
    GRAPHICS["quote"].draw_state(data, "en", 1)


def test_quote_builder_refuses_a_source_that_is_not_a_listed_newspaper_or_tv_broadcaster(db_session):
    action = _quote_claim(db_session, "https://someblog.example.com/post")
    assert D._quote_data(db_session, action) is None


def test_evidence_check_builder_renders_T15(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "item", "Spent casings", "object", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "item", "found", "Two casings were found at the scene.",
                 "established_in_text")
    MEM.add_claim(db_session, graph.id, "c2", "item", "owner", "Who fired them is unknown.", "explicit_unknown")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "evidence_check")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}, {"claim_key": "c2"}])
    db_session.flush()
    data = D._evidence_check_data(db_session, action)
    assert EvidenceCheck().can_render(data) is None
    EvidenceCheck().draw(json.dumps(data), "en", 1)


def test_custody_builder_renders_T16(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "item", "Spent casings", "object", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "item", "found", "Found near the bed.", "established_in_text")
    MEM.add_claim(db_session, graph.id, "c2", "item", "logged", "Logged into evidence.", "established_in_text")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "custody")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}, {"claim_key": "c2"}])
    db_session.flush()
    data = D._custody_data(db_session, action)
    assert ChainOfCustody().can_render(data) is None
    ChainOfCustody().draw(json.dumps(data), "en", 1)


def test_investigation_clock_builder_renders_T08(db_session):
    graph, _, case = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "crime", "The break-in", "event", "B01", 0)
    f1 = Fact(case_id=case.id, claim="Shots fired.", event_date="2024-03-15")
    f2 = Fact(case_id=case.id, claim="Officers arrive.", event_date="2024-03-16")
    db_session.add_all([f1, f2])
    db_session.flush()
    MEM.add_claim(db_session, graph.id, "c1", "crime", "crime", "Ashley Flynn was shot.", "established_in_text")
    MEM.add_claim(db_session, graph.id, "c2", "crime", "investigation", "Officers responded.", "established_in_text")
    from app.db.models import DetectiveClaim
    db_session.query(DetectiveClaim).filter_by(claim_key="c1").update({"evidence_fact_ids_json": json.dumps([f1.id])})
    db_session.query(DetectiveClaim).filter_by(claim_key="c2").update({"evidence_fact_ids_json": json.dumps([f2.id])})
    db_session.flush()
    _, action = _scene_with_action(db_session, graph, "B01", 0, "investigation_clock")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}, {"claim_key": "c2"}])
    db_session.flush()
    data = D._investigation_clock_data(db_session, action)
    assert InvestigationClock().can_render(data) is None
    InvestigationClock().draw(json.dumps(data), "en", 1)


def test_forensic_builder_renders_T17(db_session):
    from app.documentary.graphics.engines.evidence import ForensicExamination

    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "casings", "Spent casings", "object", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "casings", "exam", "ballistics", "established_in_text")
    MEM.add_claim(db_session, graph.id, "c2", "casings", "sample", "Two 9mm casings from the scene.",
                 "established_in_text")
    MEM.add_claim(db_session, graph.id, "c3", "casings", "method", "Comparison microscopy against test fires.",
                 "established_in_text")
    MEM.add_claim(db_session, graph.id, "c4", "casings", "result", "Matched to one firearm.", "established_in_text")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "forensic")
    action.content_bindings_json = json.dumps([{"claim_key": k} for k in ("c1", "c2", "c3", "c4")])
    db_session.flush()
    data = D._forensic_data(db_session, action)
    assert ForensicExamination().can_render(data) is None
    ForensicExamination().draw(json.dumps(data), "en", 1)


def test_interrogation_builder_renders_T18(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "caleb", "Caleb Flynn", "person", "B01", 0)
    MEM.add_question(db_session, graph.id, "q1", ["caleb"], "Where were you that night?", "not_yet_explained")
    MEM.add_claim(db_session, graph.id, "c1", "caleb", "answer", "I was asleep upstairs.", "attributed",
                 attribution="Caleb Flynn")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "interrogation")
    action.content_bindings_json = json.dumps([{"question_key": "q1"}, {"claim_key": "c1"}])
    db_session.flush()
    data = D._interrogation_data(db_session, action)
    assert InterrogationAnalysis().can_render(data) is None
    InterrogationAnalysis().draw(json.dumps(data), "en", 1)


def test_courtroom_builder_renders_T19(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "case", "The trial", "event", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "case", "issue", "Whether the defendant fired the weapon.",
                 "established_in_text")
    MEM.add_claim(db_session, graph.id, "c2", "case", "prosecution", "Gunshot residue was found on his hands.",
                 "attributed", attribution="Prosecution")
    MEM.add_claim(db_session, graph.id, "c3", "case", "defense", "The residue could be transfer contamination.",
                 "attributed", attribution="Defense")
    MEM.add_claim(db_session, graph.id, "c4", "case", "finding", "The jury found him guilty.", "inference")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "courtroom")
    action.content_bindings_json = json.dumps([{"claim_key": k} for k in ("c1", "c2", "c3", "c4")])
    db_session.flush()
    data = D._courtroom_data(db_session, action)
    assert CourtroomReasoning().can_render(data) is None
    CourtroomReasoning().draw(json.dumps(data), "en", 1)


def test_sentencing_builder_renders_T20(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "defendant", "Caleb Flynn", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "defendant", "charge", "Life without parole.",
                 "established_in_text")
    MEM.add_claim(db_session, graph.id, "c2", "defendant", "charge", "25 years to life.", "established_in_text")
    MEM.add_claim(db_session, graph.id, "c3", "defendant", "final",
                 "The court imposed the maximum sentence on both counts.", "inference")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "sentencing")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}, {"claim_key": "c2"}, {"claim_key": "c3"}])
    db_session.flush()
    data = D._sentencing_data(db_session, action)
    assert SentencingExplanation().can_render(data) is None
    SentencingExplanation().draw(json.dumps(data), "en", 1)


def _sourced_claim(db_session, graph, case, claim_text, source, basis="attributed"):
    """A detective claim linked, through a stored Fact, to a real Source row."""
    from app.db.models import DetectiveClaim, Source

    db_session.add(source)
    db_session.flush()
    fact = Fact(case_id=case.id, claim=claim_text, source_ids_json=json.dumps([source.id]))
    db_session.add(fact)
    db_session.flush()
    MEM.ensure_entity(db_session, graph.id, "ent", "Entity", "event", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "ent", "statement", claim_text, basis, attribution="Court")
    db_session.query(DetectiveClaim).filter_by(claim_key="c1").update(
        {"evidence_fact_ids_json": json.dumps([fact.id])})
    db_session.flush()


def test_document_builder_renders_T11(db_session):
    from app.db.models import Source
    from app.documentary.graphics.document import DocumentCard

    graph, _, case = _graph(db_session, beats=[{"id": "B01"}])
    quote = "The defendant fired two shots into the victim while she lay in bed."
    _sourced_claim(db_session, graph, case, quote, Source(
        case_id=case.id, title="Sentencing Opinion", url="https://courts.example.gov/op.pdf",
        source_type="official", publisher="Delaware County Court", language="en",
        raw_text=f"Findings of fact. {quote} The court so finds.", published_at="2026-10-05"))
    _, action = _scene_with_action(db_session, graph, "B01", 0, "document")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}])
    db_session.flush()
    data = D._document_data(db_session, action)
    assert DocumentCard().can_render(data) is None
    DocumentCard().draw_state(data, "en", 1)


def test_document_builder_refuses_a_quote_not_in_the_official_source(db_session):
    from app.db.models import Source

    graph, _, case = _graph(db_session, beats=[{"id": "B01"}])
    _sourced_claim(db_session, graph, case, "A sentence the court never wrote.", Source(
        case_id=case.id, title="Opinion", url="https://courts.example.gov/op.pdf", source_type="official",
        publisher="Delaware County Court", language="en", raw_text="Entirely different text."))
    _, action = _scene_with_action(db_session, graph, "B01", 0, "document")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}])
    db_session.flush()
    assert D._document_data(db_session, action) is None


def test_headline_builder_renders_T13(db_session):
    from app.db.models import Source
    from app.documentary.graphics.headline import HeadlineCard

    graph, _, case = _graph(db_session, beats=[{"id": "B01"}])
    _sourced_claim(db_session, graph, case, "Husband charged in wife's shooting death.", Source(
        case_id=case.id, title="Husband charged in wife's shooting death in Ohio",
        url="https://www.theguardian.com/us-news/2026/feb/16/ohio-shooting", source_type="news",
        publisher="The Guardian", language="en", published_at="2026-02-16"))
    _, action = _scene_with_action(db_session, graph, "B01", 0, "headline")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}])
    db_session.flush()
    data = D._headline_data(db_session, action)
    assert HeadlineCard().can_render(data) is None
    HeadlineCard().draw_state(data, "en", 1)


def test_headline_builder_refuses_an_unlisted_outlet(db_session):
    from app.db.models import Source

    graph, _, case = _graph(db_session, beats=[{"id": "B01"}])
    _sourced_claim(db_session, graph, case, "Husband charged.", Source(
        case_id=case.id, title="Husband charged in wife's shooting death in Ohio",
        url="https://random-blog.example.com/post", source_type="news", publisher="A blog",
        language="en", published_at="2026-02-16"))
    _, action = _scene_with_action(db_session, graph, "B01", 0, "headline")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}])
    db_session.flush()
    assert D._headline_data(db_session, action) is None


def _map_fixture(db_session, category="place"):
    graph, _, case = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "court", "Cunningham Court", category, "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "court", "location", "The house stood on Cunningham Court.",
                 "established_in_text")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "map")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}])
    db_session.flush()
    return graph, case, action


def test_map_place_comes_only_from_a_stored_place_entity(db_session):
    _, _, action = _map_fixture(db_session, category="place")
    assert D._map_place_of(db_session, action) == "Cunningham Court"
    _, _, person = _map_fixture(db_session, category="person")
    assert D._map_place_of(db_session, person) is None      # never a map of a person or invented geography


def test_map_renders_through_the_real_direction_and_map_panel_path_T01(db_session):
    import numpy as np

    from app.core.ai_config import ai_config
    from app.documentary.graphics import mappanel as PANEL
    from app.documentary.mvp import direct as DR
    from app.documentary.mvp.pipeline import case_region

    graph, case, action = _map_fixture(db_session)
    case.location = "Tipp City, Ohio, United States"
    place = D._map_place_of(db_session, action)
    # the real direction step: a bare stretch whose words name the place becomes a SHOW_MAP shot
    window = 30.0
    shots = [{"gap": True, "share": 1.0, "said": "The call came from Cunningham Court in Tipp City."}]
    DR.add_map_cards(shots, {"map_place": place}, window, [], set(), case_region(case))
    maps = [s for s in shots if s.get("command") == "SHOW_MAP"]
    assert maps and maps[0]["map_place"] == "Cunningham Court, Ohio, United States"
    # the real panel renderer for that place (synthetic map frame, as tests/test_map_panel.py does)
    width = PANEL.map_width(1920)
    panel = {"names": ["United States", "Ohio", "Tipp City", "Cunningham Court"], "target": [39.96, -84.18],
             "camera": [{"t": t, "zoom": z, "lon": -84.18, "lat": 39.96, "pitch": 0.0, "bearing": 0.0}
                        for t, z in ((0.0, 2.0), (3.0, 6.0), (6.0, 10.0), (9.0, 15.0))]}
    frame = PANEL.MapPanel("en", panel, maps[0]["map_place"], ["© OpenStreetMap contributors"]).compose(
        np.full((1080, width, 3), 90, np.uint8), 9.5, [0.0, 3.0, 6.0, 9.0], 1920)
    assert frame.shape == (1080, 1920, 3) and not (frame[:600, :width] == 90).all()


def _beat_with_a_plentiful_photo(db_session, tool_id):
    """One beat, an approved photo already filling all of it, and one detective action for it."""
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "gun", "The 9mm", "object", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "gun", "origin", "The gun came from the house.", "established_in_text")
    _, action = _scene_with_action(db_session, graph, "B01", 0, tool_id)
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}])
    db_session.flush()
    photo = {"kind": "photo", "beat_id": "B01", "start": 0.0, "end": 20.0, "asset_id": "A1"}
    return graph, [photo], [{"beat_id": "B01", "start": 0.0, "end": 20.0}]



def test_timeline_builder_makes_a_dated_line_from_claims_with_real_event_dates_and_nothing_else(db_session):
    from app.db.models import DetectiveClaim
    from app.documentary.graphics.timeline import Timeline

    graph, _, case = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "crime", "The break-in", "event", "B01", 0)
    f1 = Fact(case_id=case.id, claim="Shots fired.", event_date="2024-03-16")
    f2 = Fact(case_id=case.id, claim="Officers arrive.", event_date="2024-03-15")
    db_session.add_all([f1, f2])
    db_session.flush()
    for key, fact in (("c1", f1), ("c2", f2)):
        MEM.add_claim(db_session, graph.id, key, "crime", "event", f"Claim {key} happened.", "established_in_text")
        db_session.query(DetectiveClaim).filter_by(claim_key=key).update({"evidence_fact_ids_json": json.dumps([fact.id])})
    MEM.add_claim(db_session, graph.id, "c3", "crime", "event", "No date for this one.", "established_in_text")
    db_session.flush()
    _, action = _scene_with_action(db_session, graph, "B01", 0, "timeline")
    action.content_bindings_json = json.dumps([{"claim_key": k} for k in ("c1", "c2", "c3")])
    db_session.flush()

    data = D._timeline_data(db_session, action)

    assert [e["fact_id"] for e in data["events"]] == ["c2", "c1"], "in date order; the undated claim is left out"
    assert Timeline().can_render(data) is None
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}])
    db_session.flush()
    assert D._timeline_data(db_session, action) is None, "one dated event is no line"
    assert D.BUILDERS["timeline"] is D._timeline_data


def test_a_map_action_with_a_stored_place_becomes_a_placed_map_not_a_lost_note(db_session):
    graph, _, action = _map_fixture(db_session)
    record = next(r for r in D.resolve_dispatchable_actions(db_session, graph.id) if r["tool_id"] == "map")
    assert record["data"] == {"place": "Cunningham Court"} and record["beat_id"] == "B01" and "skip" not in record


def test_a_board_action_is_not_dispatched_as_a_graphic_because_the_board_plan_places_it(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "p1", "Jane Doe", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "p1", "role", "Jane was home alone.", "established_in_text")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "detective_board")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}])
    db_session.flush()
    resolved = D.resolve_dispatchable_actions(db_session, graph.id)
    assert [r["skip"] for r in resolved if r["tool_id"] == "detective_board"] == ["placed_by_the_board_plan"]
    assert D.place_detective_graphics([], [], "en", resolved)[0]["skip"] == "placed_by_the_board_plan"


def test_a_map_action_is_not_dispatched_as_a_graphic_because_the_planner_places_it():
    resolved = [{"action_key": "m1", "tool_id": "map", "beat_id": "B01", "data": {"place": "Miami County"}}]
    spans = [{"beat_id": "B01", "start": 0.0, "end": 30.0}]
    assert D.place_detective_graphics([], spans, "en", resolved)[0]["skip"] == "placed_by_the_planner"
