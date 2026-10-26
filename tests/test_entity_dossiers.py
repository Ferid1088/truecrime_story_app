"""P1: entity dossiers — the stored reference per important element of a case (entity_dossiers table,
migration 0060), merged into the planner's entity dicts: era-aware aliases and era stamping for the
queries, and the description / "NOT this" list of the verifier's entity list."""

import json
import uuid

from app.db.models import Case, EntityDossier
from app.documentary.visuals import dossier as DOS


def _case(db, incident_date="2022-11-13"):
    case = Case(canonical_title="Dossier case", slug=uuid.uuid4().hex, language="en",
               incident_date=incident_date)
    db.add(case)
    db.commit()
    return case


ENTITY = {"key": "john_smith_victim", "name": "John Smith", "type": "person",
          "aliases": ["Johnny Smith"]}


# ---- merge_into_entities: era stamping and dossier enrichment -----------------------------

def test_an_entity_without_a_dossier_still_gets_the_case_era_stamped(db_session):
    case = _case(db_session, incident_date="1892-08-04")
    entities = DOS.merge_into_entities(db_session, case, [ENTITY])
    assert entities[0]["era"] == "historical"
    assert entities[0]["not_this"] == []


def test_merge_adds_identifiers_and_likely_sources_from_the_stored_dossier(db_session):
    case = _case(db_session)
    db_session.add(EntityDossier(
        case_id=case.id, entity_key="john_smith_victim", canonical_name="John Smith",
        entity_type="person", era="modern", wikidata_id="Q12345",
        commons_category="Category:John Smith (case)",
        likely_sources_json=json.dumps(["local news", "court records"])))
    db_session.commit()
    [merged] = DOS.merge_into_entities(db_session, case, [ENTITY])
    assert merged["wikidata_id"] == "Q12345"
    assert merged["commons_category"] == "Category:John Smith (case)"
    assert merged["likely_sources"] == ["local news", "court records"]


# ---- the verifier's own input carries the dossier's description and not_this -------------

def test_the_verifier_entity_list_carries_the_dossiers_description_and_not_this():
    from app.documentary.mvp.verify import entity_view

    enriched = {**ENTITY, "visual_description": "a man in his 40s, grey jacket",
                "not_this": ["John Smith the firefighter"]}
    item = entity_view(enriched)
    assert item["description"] == "a man in his 40s, grey jacket"
    assert item["not_this"] == ["John Smith the firefighter"]


def test_the_verifier_entity_list_omits_empty_dossier_fields():
    from app.documentary.mvp.verify import entity_view

    item = entity_view(ENTITY)
    assert "description" not in item and "not_this" not in item
