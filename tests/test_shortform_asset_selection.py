import pytest

from app.db.models import Case, VisualAsset
from app.documentary.visuals.director import rank_candidates
from app.shortform.assets import UnverifiedVisualAssetsError, require_verified_case_assets
from app.shortform.director import ShortFormDirectorAgent
from scripts.live_pilot_case6 import _candidate_asset_pool, _export, _select_pilot_candidates


def _asset(code="VIS_REJECTED", *, status="rejected"):
    return VisualAsset(
        asset_code=code,
        asset_type="photo",
        asset_role="evidence",
        verification_status=status,
        rights_status="editorial_review_required",
        local_path="/tmp/asset.jpg",
        entity_key="karmelo_anthony",
        entities_json="[]",
        reveals_json="[]",
        quality_score=0.8,
    )


def test_rejected_visual_is_excluded_from_all_shortform_selection_paths(db_session):
    case = Case(canonical_title="Selection test", slug="selection-test", language="en")
    db_session.add(case)
    db_session.flush()
    rejected = _asset()
    rejected.case_id = case.id
    db_session.add(rejected)
    db_session.commit()

    req = {
        "entity": "karmelo_anthony",
        "purpose": "victim_introduction",
        "priority": "high",
        "acceptable_roles": ["evidence"],
    }
    entity = {"key": "karmelo_anthony", "name": "Karmelo Anthony"}
    assert rank_candidates(req, entity, [rejected], set(), "preview") == []
    assert _candidate_asset_pool([rejected]) == []
    require_verified_case_assets(db_session, rejected.case_id)

    unverified = _asset("VIS_UNVERIFIED", status="unverified")
    unverified.case_id = case.id
    db_session.add(unverified)
    db_session.commit()
    with pytest.raises(UnverifiedVisualAssetsError):
        require_verified_case_assets(db_session, rejected.case_id)

    generated = ShortFormDirectorAgent(candidate_count=1).generate_from_blueprint(
        {"beats": [{"id": "B01", "text": "Karmelo Anthony was identified.",
                    "asset_ids": [rejected.asset_code]}]},
        assets=[rejected],
    )
    assert generated[0]["asset_ids"] == []


def test_unverified_visual_is_also_excluded_from_shortform_pool():
    assert _candidate_asset_pool([_asset(status="unverified")]) == []


def test_pilot_records_zero_eligible_candidates_without_exporting(tmp_path):
    candidates = [{
        "id": "SFC_BLOCKED",
        "_pilot_gate": {
            "all_platforms_passed": False,
            "platforms": {"youtube_short": {}},
        },
    }]
    selected, reasons = _select_pilot_candidates(candidates)
    assert selected == []
    assert reasons == ["only 0 candidates passed all platform gates"]
    assert _export(selected, [], tmp_path) == []
