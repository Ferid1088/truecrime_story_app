import json

from app.core.ai_config import ai_config
from app.db.base import Base, engine
from app.db.models import ShortFormConcept
from app.shortform.gates import (
    AssetUse,
    ClaimUse,
    Modality,
    PolicySignal,
    disclosure_checker,
    duration_checker,
    epistemic_checker,
    policy_risk_checker,
    reveal_firewall,
    rights_checker,
)


def test_short_form_config_defaults_match_build_spec():
    cfg = ai_config.short_form
    assert cfg.candidate_multiplier == 1.75
    assert cfg.publish_target == 8
    assert cfg.candidate_count() == 14
    assert cfg.distribution["youtube_short"].model_dump() == {
        "enabled": True,
        "count": 5,
        "min_seconds": 20,
        "target_seconds": 35,
        "max_seconds": 60,
    }
    assert cfg.distribution["instagram_reel"].count == 6
    assert cfg.distribution["facebook_reel"].min_seconds == 25
    assert cfg.distribution["tiktok_video"].target_seconds == 28
    assert cfg.hard_max_seconds == 60


def test_short_form_tables_are_declared():
    Base.metadata.create_all(bind=engine)
    assert ShortFormConcept.__tablename__ in Base.metadata.tables


def test_forbidden_reveal_rejected_for_text_audio_and_visual_frame():
    result = reveal_firewall(
        allowed_reveals=["R_OPENING", "R_LOCATION"],
        text_reveals=["R_OPENING", "R_CULPRIT"],
        audio_reveals=["R_HIDDEN_RELATIONSHIP"],
        visual_reveals=["R_LATE_DOCUMENT"],
    )
    assert not result.passed
    assert result.metadata["spoiler_check_result"] == "fail"
    assert result.offending_ids == [
        "R_CULPRIT",
        "R_HIDDEN_RELATIONSHIP",
        "R_LATE_DOCUMENT",
    ]


def test_epistemic_checker_rejects_stronger_than_source_claim():
    result = epistemic_checker([
        ClaimUse(
            source_claim_id="C001",
            source_modality=Modality.ALLEGED,
            short_modality=Modality.ESTABLISHED,
            text="He did it.",
        )
    ])
    assert not result.passed
    assert result.offending_ids == ["C001"]


def test_epistemic_checker_allows_preserved_hedge():
    result = epistemic_checker([
        ClaimUse(
            source_claim_id="C002",
            source_modality=Modality.BELIEVED_BY_INVESTIGATORS,
            short_modality=Modality.BELIEVED_BY_INVESTIGATORS,
        )
    ])
    assert result.passed


def test_unknown_rights_flagged_and_platform_signoff_required():
    unknown = rights_checker(
        [AssetUse(asset_id="A001", rights_status="unknown")],
        platform="youtube_short",
    )
    assert not unknown.passed
    assert "A001" in unknown.offending_ids

    strict = rights_checker(
        [AssetUse(asset_id="A002", rights_status="fair_use_review")],
        platform="tiktok_video",
    )
    assert not strict.passed
    assert "A002" in strict.offending_ids


def test_missing_disclosure_blocks_export():
    result = disclosure_checker(
        assets=[AssetUse(asset_id="A003", requires_ai_disclosure=True)],
        synthetic_voice=True,
        disclosure_flags={"youtube_short": False},
        platform="youtube_short",
    )
    assert not result.passed
    assert "AI disclosure" in result.reasons[0]


def test_duration_checker_enforces_platform_and_hard_max():
    too_long = duration_checker(
        61,
        min_seconds=20,
        max_seconds=60,
        hard_max_seconds=60,
    )
    assert not too_long.passed
    assert any("hard max" in reason for reason in too_long.reasons)

    ok = duration_checker(35, min_seconds=20, max_seconds=60, hard_max_seconds=60)
    assert ok.passed


def test_policy_high_risk_requires_human_review():
    result = policy_risk_checker(
        [PolicySignal(signal="minor", severity="high", reason="minor is identifiable")],
        platform="instagram_reel",
    )
    assert not result.passed
    assert result.metadata["policy_risk"][0]["signal"] == "minor"


def test_short_form_concept_persists_gate_results(db_session):
    concept = ShortFormConcept(
        case_id=1,
        concept_type="MYSTERY_HOOK",
        source_beat_ids_json=json.dumps(["B001"]),
        evidence_ids_json=json.dumps(["F001"]),
        asset_ids_json=json.dumps(["A001"]),
        allowed_reveals_json=json.dumps(["R_OPENING"]),
        forbidden_reveals_json=json.dumps(["R_CULPRIT"]),
        claims_json=json.dumps([
            {"source_claim_id": "C001", "modality": "ALLEGED"}
        ]),
        gate_results_json=json.dumps({"RevealFirewall": {"passed": False}}),
    )
    db_session.add(concept)
    db_session.commit()
    assert concept.id
    saved = db_session.get(ShortFormConcept, concept.id)
    assert json.loads(saved.gate_results_json)["RevealFirewall"]["passed"] is False

