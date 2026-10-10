"""Phase 15 branch coverage for core long-form and short-form safety modules.

The persisted case-6 tests remain the real-data checks. These focused tests are
clearly synthetic branch tests for malformed inputs, fallback behavior, and
platform-independent edge paths that real case data does not exercise.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.longform import compression as C
from app.longform import epistemic as E
from app.longform import service as L
from app.shortform import director as D
from app.shortform import export as X
from app.shortform import selection as S
from app.shortform import metrics as M
from app.shortform import operations as O
from app.shortform import api as A
from app.longform import rollout as R
from app.db.models import Case, EditorialBlueprint, ShortFormMetric, StoryVersion, VisualAsset


def test_epistemic_atomic_proposition_branches_are_explicit():
    cases = [
        ('Anthony said "I did it."', ("REPORTING_ACT", "QUOTED_PROPOSITION")),
        ('Anthony: "I did it."', ("REPORTING_ACT", "QUOTED_PROPOSITION")),
        ("Witnesses testified that Anthony was there.", ("REPORTING_ACT", "ATTRIBUTED_PROPOSITION")),
        ("The state asserted Anthony was there.", ("NARRATOR_ASSERTION", "ATTRIBUTED_PROPOSITION")),
        ("The prosecutor denied the timeline, arguing that the route was wrong, and maintaining that the clock failed.",
         ("REPORTING_ACT", "ATTRIBUTED_PROPOSITION", "ATTRIBUTED_PROPOSITION")),
        ("Officer Cortez was reporting that he is detaining the alleged suspect.",
         ("REPORTING_ACT", "ATTRIBUTED_PROPOSITION")),
        ("According to the defense, Anthony was there, but he left.",
         ("REPORTING_ACT", "ATTRIBUTED_PROPOSITION", "ATTRIBUTED_PROPOSITION")),
        ("Anthony told investigators that he was there.", ("REPORTING_ACT", "ATTRIBUTED_PROPOSITION")),
        ("Anthony claimed, he was there.", ("REPORTING_ACT", "ATTRIBUTED_PROPOSITION")),
        ("The record confirms the location.", ("NARRATOR_ASSERTION",)),
    ]
    for sentence, roles in cases:
        assert tuple(part["role"] for part in E._atomic_parts(sentence)) == roles

    assert E._modality("The accounts were conflicting") == "DISPUTED"
    assert E._modality("The defense alleged the route") == "ALLEGED"
    assert E._modality("Investigators believed the route") == "BELIEVED_BY_INVESTIGATORS"
    assert E._modality("There was no evidence of a second call") == "ABSENCE_OF_EVIDENCE"
    assert E._modality("The call was recorded") == "ESTABLISHED"
    assert E._speaker("the defense team") == "defense"
    assert E._speaker("a witness testified") == "witnesses"
    assert E._speaker("the prosecutor objected") == "prosecution"
    assert E._speaker("Cortez arrived") == "Officer Eduardo Cortez"
    assert E._speaker("Anthony answered") == "Anthony"
    assert E._speaker("the narrator answered") is None
    assert E._quoted_text('No quote here.') is None
    assert E._tokens("The route was recorded") == {"the", "route", "was", "recorded"}


def test_epistemic_extraction_assigns_beats_and_coverage(monkeypatch):
    class EmptyQuery:
        def filter_by(self, **_kwargs):
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return []

    class EmptyDB:
        def query(self, *_args):
            return EmptyQuery()

    monkeypatch.setattr(E, "build_evidence_pack", lambda *_args: {
        "facts": [{"id": "F1", "claim": "call recorded", "description": "body camera"}],
        "timeline": [], "contradictions": []
    })
    story = SimpleNamespace(
        id=15,
        case_id=6,
        story_text="The call was recorded.\n\nWas the route clear? The account was disputed.",
        narrative_structure=json.dumps({"sections": [{"id": "A1", "text": "x\n\ny"}]}),
    )
    blueprint = SimpleNamespace(blueprint_json=json.dumps({
        "beats": [{"id": "B1", "act_id": "A1", "paragraphs": [1, 2]}],
    }))
    result = E.extract_master_claims(EmptyDB(), story, blueprint)
    assert result["coverage"]["denominator_declarative_sentences"] == 2
    assert result["coverage"]["declarative_spans_covered"] == 2
    assert all(claim["beat_id"] == "B1" for claim in result["claims"])
    assert E._loads("not json") == {}
    assert E._loads("[]") == {}
    assert E._sentence_spans("One sentence.\n\nTwo sentences.")
    uncovered = SimpleNamespace(
        id=16, case_id=6, story_text="Anthony argued that the call happened.",
        narrative_structure=json.dumps({"sections": [{"id": "A1", "text": "x"}]}),
    )
    assert E.extract_master_claims(EmptyDB(), uncovered, blueprint)["claims"]
    with pytest.raises(ValueError, match="not covered"):
        E.extract_master_claims(
            EmptyDB(),
            SimpleNamespace(id=17, case_id=6, story_text="A fact.",
                            narrative_structure=json.dumps({"sections": [{"id": "A1", "text": "x"}]})),
            SimpleNamespace(blueprint_json=json.dumps({"beats": []})),
        )


def test_director_handles_nested_beats_assets_and_concept_branches():
    nested = {"acts": [{"beats": [
        {"beat_id": "B1", "text": "A call changed the route", "evidence_ids": [], "asset_ids": [],
         "allowed_reveals": ["R1"]},
        {"id": "B2", "purpose": "The document contradicts the map", "visual": "document"},
        {"id": "B3", "short_text": "A very long " + "detail " * 40},
        {"id": "B4", "summary": "", "evidence_ids": ["F4"]},
        "skip me",
    ]}]}
    beats = D.extract_beats(nested)
    assert [beat.id for beat in beats] == ["B1", "B2", "B3"]
    assert D.extract_beats({}) == []
    assert D.choose_concept_type(beats[0], 0) == "ORIGINAL_AUDIO"
    assert D.choose_concept_type(D.BeatMaterial("B", "the map location road", visual_priority="map"), 0) == "LOCATION_OR_MAP"
    assert D.choose_concept_type(D.BeatMaterial("B", "a contradiction", visual_priority=""), 0) == "CONTRADICTION"
    assert D.make_hook(D.BeatMaterial("B", "a question", visual_priority=""), "QUESTION_DRIVEN").endswith("?")
    assert D.make_hook(D.BeatMaterial("B", "a document", visual_priority=""), "DOCUMENT_MOMENT").startswith("One document")
    assert D.make_hook(D.BeatMaterial("B", "a contradiction", visual_priority=""), "CONTRADICTION").startswith("The detail")
    assert len(D.make_hook(D.BeatMaterial("B", "x " * 80), "MYSTERY_HOOK")) <= 96
    assert D._asset_map([{"id": "A1", "asset_role": "evidence"}])["A1"]["asset_role"] == "evidence"
    assets = {"A1": {"asset_role": "evidence", "asset_type": "document", "reveals": ["R1"]},
              "A2": {"asset_role": "illustration", "asset_type": "photo", "reveals": ["R2"]}}
    assert D._best_asset_ids(assets, D.BeatMaterial("B", "x", allowed_reveals=["R1"]), limit=1) == ["A1"]
    assert D._best_asset_ids({}, beats[0], limit=1) == []
    with pytest.raises(ValueError):
        D.ShortFormDirectorAgent(candidate_count=1).generate_from_blueprint({})


def test_selection_edge_scoring_and_asset_normalization():
    assert S.similarity({"concept_type": "A", "source_beat_ids": ["B"], "asset_ids": ["A"], "hook": "same words"},
                        {"concept_type": "A", "source_beat_ids": ["B"], "asset_ids": ["A"], "hook": "same words"}) > 0.7
    assert S.hook_structure("Is this true?") == "question"
    assert S.hook_structure("One document: a time") == "colon_fact"
    assert S.hook_structure("The timeline") == "declarative_detail"
    assert S.hook_structure("Look here") == "statement"
    assert S.shortfall_reasons(2, [{"id": "A"}], []) == ["Requested 2, qualified 1"]
    assert S._asset_use("VIS_1").rights_status == "owned"
    asset = S._asset_use({"id": "VIS_2", "rights_status": "unknown", "platform_claim_risk": "high",
                          "reveals": ["F1"], "human_signoff": True, "requires_ai_disclosure": True})
    assert asset.asset_id == "VIS_2" and asset.rights_status == "unknown" and asset.human_signoff


def test_export_covers_fallback_fonts_and_all_localized_cues(tmp_path, monkeypatch):
    original = X.ImageFont.truetype
    monkeypatch.setattr(X.ImageFont, "truetype", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(X.ImageFont, "load_default", lambda: object())
    assert X._font("en", 12) is not None
    monkeypatch.setattr(X.ImageFont, "truetype", original)
    monkeypatch.setattr(X, "_write_cover", lambda path, *_args: path.write_bytes(b"cover"))
    monkeypatch.setattr(X.subprocess, "run", lambda *_args, **_kwargs: None)
    for language in ("fa", "ar"):
        manifest = X.build_export_package(tmp_path / language, language=language)
        assert manifest["subtitle_layout"]["direction"] == "rtl"


def test_compression_classifier_fallback_and_provider_decisions():
    fallback = C.SemanticAttributionClassifier(allow_fallback=True)
    review = asyncio.run(fallback.check("ALLEGED", "Anthony asserted he did it", "Anthony did it"))
    assert review.decision == "HUMAN_REVIEW"

    class Provider:
        def is_configured(self):
            return True

        async def generate_structured(self, *_args, **_kwargs):
            return ({"direct_assertion": True, "attribution_present": False, "confidence": 0.99,
                     "rationale": "bare assertion"}, SimpleNamespace(provider="test", model="fixed"))

    classifier = C.SemanticAttributionClassifier(provider=Provider())
    rejected = asyncio.run(classifier.check("ALLEGED", "Anthony asserted he did it", "Anthony did it"))
    assert rejected.decision == "REJECT"
    accepted = asyncio.run(classifier.check("ESTABLISHED", "The record confirms it", "The record confirms it"))
    assert accepted.decision == "ACCEPT"

    class UncertainProvider(Provider):
        async def generate_structured(self, *_args, **_kwargs):
            return ({"direct_assertion": False, "attribution_present": False, "confidence": 0.5},
                    SimpleNamespace(provider="test", model="fixed"))

    assert asyncio.run(C.SemanticAttributionClassifier(provider=UncertainProvider()).check(
        "ALLEGED", "The wording is attributed", "The wording is unclear")).decision == "HUMAN_REVIEW"

    class BrokenProvider(Provider):
        async def generate_structured(self, *_args, **_kwargs):
            return ({"direct_assertion": "yes", "attribution_present": False, "confidence": 2},
                    SimpleNamespace(provider="test", model="fixed"))

    with pytest.raises(ValueError):
        asyncio.run(C.SemanticAttributionClassifier(provider=BrokenProvider()).classify("x", "y"))

    class UnconfiguredProvider(Provider):
        def is_configured(self):
            return False

    with pytest.raises(C.GenerationError, match="configured generation provider"):
        asyncio.run(C.SemanticAttributionClassifier(provider=UnconfiguredProvider()).classify("x", "y"))
    with pytest.raises(ValueError, match="Unknown source modality"):
        C.check_compression_safety("UNKNOWN", "a claim")
    assert C.infer_modality("Investigators believed the report") == "BELIEVED_BY_INVESTIGATORS"
    assert C.infer_modality("The witness allegedly saw it") == "ALLEGED"
    measured = C.evaluate_compression_cases([
        {"source_modality": "ALLEGED", "rewritten_text": "He did it", "expected_violation": True},
        {"source_modality": "ESTABLISHED", "rewritten_text": "He did it", "expected_violation": False},
    ])
    assert measured["false_negative"] == 0 and measured["false_positive"] == 0


def test_rollout_reports_each_real_readiness_state():
    class Query:
        def __init__(self, rows=(), count=None):
            self.rows, self._count = list(rows), count

        def filter_by(self, **kwargs):
            rows = [row for row in self.rows if all(getattr(row, key, None) == value for key, value in kwargs.items())]
            return Query(rows, self._count if self._count is not None and not self.rows else len(rows))

        def all(self):
            return self.rows

        def count(self):
            return self._count if self._count is not None else len(self.rows)

        def order_by(self, *_args):
            return self

    def db_for(*, blueprints=(), stories=0, assets=0, segments=0, graphs=0, contracts=()):
        for blueprint in blueprints:
            blueprint.case_id = 1
        for contract in contracts:
            contract.case_id = 1
        mapping = {
            R.EditorialBlueprint: Query(blueprints), R.StoryVersion: Query(count=stories),
            R.VisualAsset: Query(count=assets), R.OriginalMediaSegment: Query(count=segments),
            R.RevealGraph: Query(count=graphs), R.EpistemicContractSet: Query(contracts),
        }
        return SimpleNamespace(query=lambda model: mapping[model])

    case = SimpleNamespace(id=1, case_uid="C1", canonical_title="Synthetic readiness")
    scenarios = [
        (db_for(), "NOT_CANDIDATE"),
        (db_for(blueprints=[SimpleNamespace(status="draft", id=2)], stories=1, assets=1), "NOT_CANDIDATE"),
        (db_for(blueprints=[SimpleNamespace(status="valid", id=3)], stories=1, assets=1), "BLOCKED_GRAPH"),
        (db_for(blueprints=[SimpleNamespace(status="valid", id=4)], stories=1, assets=1, graphs=1), "BLOCKED_CONTRACTS"),
        (db_for(blueprints=[SimpleNamespace(status="valid", id=5)], stories=1, assets=1, graphs=1,
                contracts=[SimpleNamespace(status="in_review")]), "REVIEW_REQUIRED"),
        (db_for(blueprints=[SimpleNamespace(status="valid", id=6)], stories=1, assets=1, graphs=1,
                contracts=[SimpleNamespace(status="approved")]), "READY"),
    ]
    for db, expected in scenarios:
        result = R.assess_case_readiness(db, case)
        assert result.status == expected, (expected, result)
        assert result.to_dict()["case_id"] == 1


def test_shortform_api_and_metrics_error_boundaries(client, db_session):
    assert client.get("/api/short-form/metrics?case_id=999").status_code == 200
    assert client.get("/api/short-form/blueprints/999999/candidates").status_code == 404
    broken = EditorialBlueprint(case_id=6, story_version_id=15, status="valid", blueprint_json="not-json")
    db_session.add(broken)
    db_session.commit()
    assert client.get(f"/api/short-form/blueprints/{broken.id}/candidates").status_code == 409
    malformed = ShortFormMetric(platform="test", views=1, detail_json="[")
    db_session.add(malformed)
    db_session.commit()
    assert M.concept_type_comparison(db_session, 999)["metric_rows"] == 0


def test_shortform_operations_edge_validation():
    with pytest.raises(ValueError):
        O.transition_publish_status("approved", "unknown")
    assert O.native_script({"id": "c", "hook": "hook"}, "en")["native_authored"] is False
    assert O.native_script({"id": "c", "hook": "hook", "native_hooks": {"fa": "قلاب"}}, "fa")["direction"] == "rtl"
    assert O.build_publish_plan(__import__("datetime").date.today(), {}, days=10) == []
    with pytest.raises(ValueError):
        O.build_publish_plan(__import__("datetime").date.today(), {"x": 1}, days=9)
    with pytest.raises(ValueError):
        O.build_publish_plan(__import__("datetime").date.today(), {"x": -1})


def test_longform_service_validation_and_crud_error_paths(db_session):
    case = Case(canonical_title="Coverage service case", slug="coverage-service-case", language="en")
    db_session.add(case)
    db_session.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="master", language="en",
                         narrative_angle="test", story_text="One.", engagement_score=0.0)
    db_session.add(story)
    db_session.flush()
    blueprint = EditorialBlueprint(
        case_id=case.id, story_version_id=story.id, version=1, status="valid",
        blueprint_json=json.dumps({"beats": [
            {"id": "B1", "reveals": ["F1"], "relies_on": [], "viewer_knows": ["F1"]},
            {"id": "B2", "reveals": ["F2"], "relies_on": ["F1"], "viewer_knows": ["F1", "F2"],
             "do_not_reveal": ["F3"]},
        ]}),
    )
    db_session.add(blueprint)
    db_session.commit()
    nodes = [
        {"node_key": "F1", "label": "one", "category": "fact", "first_revealed_beat_id": "B1", "first_revealed_order": 0},
        {"node_key": "F2", "label": "two", "category": "fact", "first_revealed_beat_id": "B2", "first_revealed_order": 1},
        {"node_key": "F3", "label": "three", "category": "fact", "first_revealed_beat_id": "B2", "first_revealed_order": 1},
    ]
    edges = [{"prerequisite": "F1", "dependent": "F2"}]
    exposures = [{"beat_id": "B1", "node_key": "F1", "exposure_type": "exposed"},
                 {"beat_id": "B2", "node_key": "F3", "exposure_type": "withheld"}]
    payload = {"version": 1, "nodes": nodes, "edges": edges, "exposures": exposures}
    graph = L.create_reveal_graph(db_session, case.id, blueprint.id, payload)
    assert L.reveal_graph_dict(graph)["nodes"]
    assert L._loads("bad", {"fallback": True}) == {"fallback": True}
    with pytest.raises(L.LongformValidationError, match="exactly one"):
        L.spoiler_horizon(db_session, graph.id)
    with pytest.raises(L.LongformValidationError, match="exactly one"):
        L.spoiler_horizon(db_session, graph.id, beat_id="B1", timestamp_seconds=1)
    with pytest.raises(L.LongformValidationError, match="Unknown blueprint beat"):
        L.spoiler_horizon(db_session, graph.id, beat_id="B99")
    with pytest.raises(L.LongformValidationError, match="Unknown reveal references"):
        L.check_spoiler_references(db_session, graph.id, ["NOPE"], beat_id="B1")
    for node in graph.nodes:
        node.first_revealed_at_seconds = 1.0 if node.node_key == "F1" else 4.0
    db_session.commit()
    assert L.spoiler_horizon(db_session, graph.id, timestamp_seconds=2)["allowed"] == ["F1"]

    asset = VisualAsset(case_id=case.id, asset_code="COVERAGE-ASSET", asset_type="photo")
    db_session.add(asset)
    db_session.commit()
    with pytest.raises(L.LongformValidationError, match="Reveal graph not found"):
        L.add_reveal_asset_link(db_session, 999999, "F1", visual_asset_id=asset.id)
    with pytest.raises(L.LongformValidationError, match="Reveal node not found"):
        L.add_reveal_asset_link(db_session, graph.id, "NOPE", visual_asset_id=asset.id)
    with pytest.raises(L.LongformValidationError, match="needs a visual"):
        L.add_reveal_asset_link(db_session, graph.id, "F1")
    with pytest.raises(L.LongformValidationError, match="does not belong"):
        L.add_reveal_asset_link(db_session, graph.id, "F1", visual_asset_id=999999)
    link = L.add_reveal_asset_link(db_session, graph.id, "F1", visual_asset_id=asset.id)
    assert L.add_reveal_asset_link(db_session, graph.id, "F1", visual_asset_id=asset.id).id == link.id
    with pytest.raises(L.LongformValidationError, match="no reveal links"):
        L.check_spoiler_visual_assets(db_session, graph.id, [999], beat_id="B1")

    with pytest.raises(L.LongformValidationError, match="non-empty"):
        L.validate_reveal_graph_payload(db_session, case.id, blueprint.id, [{**nodes[0], "node_key": ""}], [], [])
    with pytest.raises(L.LongformValidationError, match="unknown reveal nodes"):
        L.validate_reveal_graph_payload(db_session, case.id, blueprint.id, nodes[:2], [], [])
    with pytest.raises(L.LongformValidationError, match="self-edge"):
        L._validate_acyclic({"F1"}, [("F1", "F1")])
    with pytest.raises(L.LongformValidationError, match="at least one"):
        L._validate_reachability([], [])
    with pytest.raises(L.LongformValidationError, match="unknown beat"):
        L.validate_reveal_graph_payload(db_session, case.id, blueprint.id,
                                        [{**nodes[0], "first_revealed_beat_id": "B99"}, *nodes[1:]], [], [])
    with pytest.raises(L.LongformValidationError, match="unknown beat"):
        L.validate_reveal_graph_payload(db_session, case.id, blueprint.id, nodes, [],
                                        [{"beat_id": "B99", "node_key": "F1", "exposure_type": "exposed"}])
    with pytest.raises(L.LongformValidationError, match="exposure type"):
        L.validate_reveal_graph_payload(db_session, case.id, blueprint.id, nodes, [],
                                        [{"beat_id": "B1", "node_key": "F1", "exposure_type": "bad"}])
    with pytest.raises(L.LongformValidationError, match="already exists"):
        L.create_reveal_graph(db_session, case.id, blueprint.id, payload)
    with pytest.raises(L.LongformValidationError, match="Invalid reveal graph status"):
        L.update_reveal_graph_status(db_session, graph.id, "bad")
    with pytest.raises(L.LongformValidationError, match="not found"):
        L.update_reveal_graph_status(db_session, 999999, "draft")
    with pytest.raises(L.LongformValidationError, match="not found"):
        L.delete_reveal_graph(db_session, 999999)

    contract = L.create_contract_set(db_session, case.id, {
        "blueprint_id": blueprint.id, "story_version_id": story.id,
        "claims": [{"claim_key": "C1", "claim_text": "One", "beat_id": "B1", "modality": "ESTABLISHED"}],
    })
    with pytest.raises(L.LongformValidationError, match="Invalid contract"):
        L.update_contract_set_status(db_session, contract.id, "bad")
    with pytest.raises(L.LongformValidationError, match="current status"):
        L.approve_contract_set(db_session, contract.id)
    with pytest.raises(L.LongformValidationError, match="not found"):
        L.update_contract_set_status(db_session, 999999, "draft")
    with pytest.raises(L.LongformValidationError, match="unique"):
        L.create_contract_set(db_session, case.id, {"blueprint_id": blueprint.id, "story_version_id": story.id,
            "claims": [{"claim_key": "C1", "claim_text": "x", "beat_id": "B1", "modality": "ESTABLISHED"},
                       {"claim_key": "C1", "claim_text": "y", "beat_id": "B1", "modality": "ESTABLISHED"}]})
    with pytest.raises(L.LongformValidationError, match="unknown beat"):
        L.create_claim(db_session, contract.id, {"claim_key": "C2", "claim_text": "x", "beat_id": "B99", "modality": "ESTABLISHED"})
    with pytest.raises(L.LongformValidationError, match="not found"):
        L.create_claim(db_session, 999999, {"claim_key": "C2", "claim_text": "x", "beat_id": "B1", "modality": "ESTABLISHED"})
    claim = contract.claims[0]
    assert L.claim_dict(claim)["claim_key"] == "C1"
    with pytest.raises(L.LongformValidationError, match="not found"):
        L.update_claim(db_session, 999999, {})
    with pytest.raises(L.LongformValidationError, match="not found"):
        L.delete_claim(db_session, 999999)

    with pytest.raises(L.LongformValidationError, match="not viewer-known"):
        bad_blueprint = EditorialBlueprint(
            case_id=case.id, story_version_id=story.id, status="valid",
            blueprint_json=json.dumps({"beats": [
                {"id": "X1", "reveals": ["F9"], "relies_on": [], "viewer_knows": []},
                {"id": "X2", "reveals": ["F10"], "relies_on": ["F9"], "viewer_knows": []},
            ]}),
        )
        db_session.add(bad_blueprint)
        db_session.commit()
        monkey = lambda *_args: {"facts": [{"id": "F9", "claim": "x"}, {"id": "F10", "claim": "y"}],
                                  "timeline": [], "contradictions": []}
        original_pack = L.build_evidence_pack
        L.build_evidence_pack = monkey
        try:
            L.build_reveal_graph_payload(db_session, case.id, bad_blueprint.id)
        finally:
            L.build_evidence_pack = original_pack

    with pytest.raises(L.LongformValidationError, match="revealed more than once"):
        duplicate = EditorialBlueprint(
            case_id=case.id, story_version_id=story.id, status="valid",
            blueprint_json=json.dumps({"beats": [{"id": "D1", "reveals": ["F9"]},
                                                   {"id": "D2", "reveals": ["F9"]}]}),
        )
        db_session.add(duplicate)
        db_session.commit()
        original_pack = L.build_evidence_pack
        L.build_evidence_pack = lambda *_args: {"facts": [{"id": "F9", "claim": "x"}],
                                                 "timeline": [], "contradictions": []}
        try:
            L.build_reveal_graph_payload(db_session, case.id, duplicate.id)
        finally:
            L.build_evidence_pack = original_pack

    with pytest.raises(L.LongformValidationError, match="unknown node"):
        L._validate_acyclic({"F1"}, [("F1", "NOPE")])
    with pytest.raises(L.LongformValidationError, match="disconnected"):
        L._validate_reachability([{"node_key": "F1"}, {"node_key": "F2"}], [("F1", "F2"), ("F2", "F1")])
    with pytest.raises(L.LongformValidationError, match="revealed too late"):
        L._validate_reveal_order([{**nodes[0], "first_revealed_order": 1},
                                  {**nodes[1], "first_revealed_order": 0}],
                                 [("F1", "F2")], {"B1": 1, "B2": 0})
    assert L._blueprint_references(blueprint) >= {"F1", "F2", "F3"}
    with pytest.raises(L.LongformValidationError, match="unknown node"):
        L.validate_reveal_graph_payload(db_session, case.id, blueprint.id, nodes,
                                        edges, [{"beat_id": "B1", "node_key": "NOPE", "exposure_type": "exposed"}])
    with pytest.raises(L.LongformValidationError, match="not found"):
        L.spoiler_horizon(db_session, 999999, beat_id="B1")
    for node in graph.nodes:
        node.first_revealed_at_seconds = None
    db_session.commit()
    with pytest.raises(L.LongformValidationError, match="Timestamp horizon unavailable"):
        L.spoiler_horizon(db_session, graph.id, timestamp_seconds=1)
    with pytest.raises(L.LongformValidationError, match="does not belong"):
        L.add_reveal_asset_link(db_session, graph.id, "F1", original_media_segment_id=999999)
    assert L.reveal_graph_dict(graph)["asset_links"]
    with pytest.raises(L.LongformValidationError, match="needs a visual"):
        L.create_reveal_graph(db_session, case.id, blueprint.id, {**payload, "version": 2,
            "asset_links": [{"node_key": "F1"}]})
    with pytest.raises(L.LongformValidationError, match="does not belong"):
        L.create_reveal_graph(db_session, case.id, blueprint.id, {**payload, "version": 3,
            "asset_links": [{"node_key": "F1", "visual_asset_id": 999999}]})

    with pytest.raises(L.LongformValidationError, match="Story version"):
        L.create_contract_set(db_session, case.id, {"blueprint_id": blueprint.id,
            "story_version_id": 999999, "claims": []})
    original_context = L._blueprint_context
    L._blueprint_context = lambda *_args: (blueprint, {"B1", "B2"}, {"B1": 0, "B2": 1})
    class CaseMissingDB:
        def get(self, model, key):
            if model is StoryVersion:
                return SimpleNamespace(id=story.id, case_id=999999)
            if model is Case:
                return None
            return db_session.get(model, key)
    try:
        with pytest.raises(L.LongformValidationError, match="Case not found"):
            L.create_contract_set(CaseMissingDB(), 999999, {"blueprint_id": blueprint.id,
                "story_version_id": story.id, "claims": []})
    finally:
        L._blueprint_context = original_context
    empty = L.create_contract_set(db_session, case.id, {"blueprint_id": blueprint.id,
        "story_version_id": story.id, "version": 2, "status": "in_review", "claims": []})
    with pytest.raises(L.LongformValidationError, match="empty"):
        L.approve_contract_set(db_session, empty.id)
