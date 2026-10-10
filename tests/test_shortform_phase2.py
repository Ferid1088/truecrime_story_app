import json

from sqlalchemy import text

from app.db.models import Case, EditorialBlueprint, VisualAsset
from app.shortform.director import ShortFormDirectorAgent, candidate_table


def _blueprint():
    return {
        "beats": [
            {
                "id": "B001",
                "summary": "Four people left the hotel, and the route still matters.",
                "evidence_ids": ["F001"],
                "asset_ids": ["MAP001"],
                "allowed_reveals": ["R_OPENING", "R_ROUTE"],
                "forbidden_reveals": ["R_ENDING"],
                "visual_priority": "map",
            },
            {
                "id": "B002",
                "summary": "A witness statement introduced a contradiction in the timeline.",
                "evidence_ids": ["F002"],
                "asset_ids": ["DOC001"],
                "allowed_reveals": ["R_OPENING", "R_ROUTE", "R_WITNESS"],
                "forbidden_reveals": ["R_CULPRIT"],
                "visual_priority": "document",
            },
            {
                "id": "B003",
                "summary": "The family detail makes the mystery human without revealing the ending.",
                "evidence_ids": ["F003"],
                "asset_ids": ["PHOTO001"],
                "allowed_reveals": ["R_OPENING", "R_FAMILY"],
                "forbidden_reveals": ["R_HIDDEN_RELATIONSHIP"],
            },
        ]
    }


def test_director_generates_14_candidates_from_real_beats_assets():
    candidates = ShortFormDirectorAgent().generate_from_blueprint(_blueprint())
    assert len(candidates) == 14
    assert all(c["source_beat_ids"] for c in candidates)
    assert all(c["evidence_ids"] for c in candidates)
    assert all(c["asset_ids"] for c in candidates)
    assert {c["source_beat_ids"][0] for c in candidates} == {"B001", "B002", "B003"}
    assert all(c["forbidden_reveals"] for c in candidates)


def test_candidate_table_shows_phase2_manual_inspection_fields():
    table = candidate_table(ShortFormDirectorAgent().generate_from_blueprint(_blueprint()))
    assert set(table[0]) == {"type", "hook", "beats", "assets", "forbidden_reveals"}
    assert table[0]["beats"] == ["B001"]
    assert table[0]["assets"] == ["MAP001"]


def test_director_can_persist_concepts_from_blueprint_row(db_session):
    case = Case(canonical_title="Pilot", slug="pilot-shortform", language="en")
    db_session.add(case)
    db_session.commit()
    row = EditorialBlueprint(
        case_id=case.id,
        story_version_id=1,
        status="valid",
        blueprint_json=json.dumps(_blueprint()),
    )
    db_session.add(row)
    db_session.add(VisualAsset(
        case_id=case.id,
        asset_code="MAP001",
        asset_type="map",
        asset_role="evidence",
        rights_status="owned",
        reveals_json=json.dumps(["R_ROUTE"]),
    ))
    db_session.commit()

    candidates = ShortFormDirectorAgent().generate_for_blueprint_row(
        db_session, row, persist=True
    )
    assert len(candidates) == 14
    stored = db_session.execute(
        text("select count(*) from short_form_concepts where episode_identity_id = :blueprint_id"),
        {"blueprint_id": row.id},
    ).scalar()
    assert stored == 14


def test_short_form_candidates_api_returns_readable_table(client, db_session):
    case = Case(canonical_title="API Pilot", slug="api-pilot-shortform", language="en")
    db_session.add(case)
    db_session.commit()
    row = EditorialBlueprint(
        case_id=case.id,
        story_version_id=1,
        status="valid",
        blueprint_json=json.dumps(_blueprint()),
    )
    db_session.add(row)
    db_session.commit()

    response = client.get(f"/api/short-form/blueprints/{row.id}/candidates")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 14
    assert payload["table"][0]["type"]
    assert payload["table"][0]["hook"]
    assert payload["table"][0]["beats"]
    assert payload["table"][0]["assets"]
