"""P1: entity dossiers — one per important element of a case, written once from the case
facts, stored through the entity_dossiers table (migration 0060), merged into the planner's
entity dicts wherever they are used: query generation (era-aware aliases/era stamping), the
pre-filter's must_name list, the verifier's entity_list, and candidate ranking's name-collision
veto. Name-collision fixture: two different people who happen to share the same name."""

import json
import uuid

from app.db.models import Case, EntityDossier, VisualAsset
from app.documentary.visuals import director as D
from app.documentary.visuals import dossier as DOS


def _case(db, incident_date="2022-11-13"):
    case = Case(canonical_title="Dossier case", slug=uuid.uuid4().hex, language="en",
               incident_date=incident_date)
    db.add(case)
    db.commit()
    return case


ENTITY = {"key": "john_smith_victim", "name": "John Smith", "type": "person",
          "aliases": ["Johnny Smith"]}


# ---- clean_dossier: validation, truncation, dedup ------------------------------------------

def test_clean_dossier_accepts_a_matching_entity():
    raw = {"entity": "john_smith_victim", "canonical_name": "John Smith",
          "aliases": ["Johnny Smith", "J. Smith"], "visual_description": "a man in his 40s",
          "not_this": ["John Smith the firefighter (unrelated, same name)"],
          "likely_sources": ["local news", "court records"],
          "expected_captions": ["John Smith, 45"], "negative_terms": ["firefighter"],
          "supports_fact_ids": ["F001", "F002", "F999"]}
    out = DOS.clean_dossier(raw, ENTITY, known_fact_ids={"F001", "F002"}, era="modern")
    assert out["entity_key"] == "john_smith_victim"
    assert out["canonical_name"] == "John Smith"
    assert "Johnny Smith" in out["aliases"]            # merged with the entity's own aliases
    assert out["not_this"] == ["John Smith the firefighter (unrelated, same name)"]
    assert out["supports_fact_ids"] == ["F001", "F002"]  # F999 dropped: not a known fact id
    assert out["era"] == "modern"


def test_clean_dossier_rejects_an_entity_the_model_did_not_name_or_got_wrong():
    assert DOS.clean_dossier({"entity": "someone_else"}, ENTITY, set(), "modern") is None
    assert DOS.clean_dossier("not a dict", ENTITY, set(), "modern") is None
    assert DOS.clean_dossier(None, ENTITY, set(), "modern") is None


def test_clean_dossier_truncates_and_dedupes_long_lists():
    from app.core.ai_config import ai_config

    raw = {"entity": "john_smith_victim", "canonical_name": "John Smith",
          "aliases": [f"Alias {i}" for i in range(30)] + ["alias 1"]}
    out = DOS.clean_dossier(raw, ENTITY, set(), "modern")
    assert len(out["aliases"]) <= ai_config.entity_dossier.max_aliases


# ---- name-collision fixture: two different people sharing a name --------------------------

def test_the_not_this_list_separates_two_people_with_the_same_name(db_session):
    case = _case(db_session)
    victim = {"key": "john_smith_victim", "name": "John Smith", "type": "person"}
    db_session.add(EntityDossier(
        case_id=case.id, entity_key="john_smith_victim", canonical_name="John Smith",
        entity_type="person", era="modern",
        aliases_json=json.dumps(["Johnny Smith"]),
        not_this_json=json.dumps(["John Smith, the volunteer firefighter quoted in local news"]),
        visual_description="the victim, late 40s"))
    db_session.commit()
    entities = DOS.merge_into_entities(db_session, case, [victim])
    [merged] = entities
    assert merged["not_this"] == ["John Smith, the volunteer firefighter quoted in local news"]

    lookalike = VisualAsset(
        case_id=case.id, asset_code="A1", asset_type="photo", provider="brave_images",
        verification_status="unverified", asset_role="evidence",
        title="John Smith, the volunteer firefighter quoted in local news, at the scene")
    other = VisualAsset(
        case_id=case.id, asset_code="A2", asset_type="photo", provider="brave_images",
        verification_status="unverified", asset_role="evidence",
        title="John Smith pictured outside the courthouse")
    db_session.add_all([lookalike, other])
    db_session.commit()
    # the collision is vetoed to 0 even though the name itself matches in full
    assert D.entity_score(lookalike, merged) == 0.0
    assert D.entity_score(other, merged) == 0.7


def test_a_confident_match_is_never_vetoed_by_not_this():
    # an asset already tied to this entity (by search/verification) is trusted regardless of
    # what its caption text happens to contain
    entity = {**ENTITY, "not_this": ["John Smith the firefighter"]}
    asset = VisualAsset(entity_key="john_smith_victim", asset_role="evidence",
                        title="John Smith the firefighter speaks at a John Smith victim event",
                        verification_status="verified")
    assert D.entity_score(asset, entity) == 1.0


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


# ---- upsert: write once, update in place on a re-run --------------------------------------

# ---- the verifier's own input carries the dossier's description and not_this -------------

def test_the_verifier_payload_carries_the_dossiers_description_and_not_this(db_session):
    from app.agents.visual_verification import VisualVerificationAgent

    case = _case(db_session)
    enriched = {**ENTITY, "visual_description": "a man in his 40s, grey jacket",
               "not_this": ["John Smith the firefighter"]}
    asset = VisualAsset(case_id=case.id, asset_code="A1", asset_type="photo",
                        found_for="John Smith", title="t", caption="c")
    agent = VisualVerificationAgent.__new__(VisualVerificationAgent)
    payload = agent._payload(case, asset, [enriched], [])
    [item] = payload["entity_list"]
    assert item["description"] == "a man in his 40s, grey jacket"
    assert item["not_this"] == ["John Smith the firefighter"]


def test_the_verifier_payload_omits_empty_dossier_fields(db_session):
    from app.agents.visual_verification import VisualVerificationAgent

    case = _case(db_session)
    asset = VisualAsset(case_id=case.id, asset_code="A2", asset_type="photo",
                        found_for="John Smith", title="t", caption="c")
    agent = VisualVerificationAgent.__new__(VisualVerificationAgent)
    payload = agent._payload(case, asset, [ENTITY], [])
    [item] = payload["entity_list"]
    assert "description" not in item and "not_this" not in item


# ---- identifiers: real APIs need a User-Agent (Wikidata/Commons robot policy) ------------

def test_identifier_lookups_send_a_user_agent(monkeypatch):
    import asyncio

    import httpx

    from app.documentary.visuals import identifiers as ID

    seen_headers = []

    def handler(request):
        seen_headers.append(request.headers.get("user-agent"))
        if "wikidata" in str(request.url):
            return httpx.Response(200, json={"search": [{"id": "Q1"}]})
        return httpx.Response(200, json={"query": {"search": [{"title": "Category:Name"}]}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return (await ID.wikidata_id(client, "Name"), await ID.commons_category(client, "Name"))

    wid, cat = asyncio.run(go())
    assert wid == "Q1" and cat == "Category:Name"
    assert all(h and "TrueCrimeStudio" in h for h in seen_headers)


def test_upsert_writes_once_and_updates_in_place_on_a_rerun(db_session):
    case = _case(db_session)
    cleaned = DOS.clean_dossier({"entity": "john_smith_victim", "canonical_name": "John Smith",
                                "visual_description": "first pass"}, ENTITY, set(), "modern")
    DOS.upsert(db_session, case.id, cleaned, "m/visual_entity_dossier")
    assert db_session.query(EntityDossier).filter_by(case_id=case.id).count() == 1
    cleaned2 = DOS.clean_dossier({"entity": "john_smith_victim", "canonical_name": "John Smith",
                                 "visual_description": "second pass"}, ENTITY, set(), "modern")
    DOS.upsert(db_session, case.id, cleaned2, "m/visual_entity_dossier")
    rows = db_session.query(EntityDossier).filter_by(case_id=case.id).all()
    assert len(rows) == 1 and rows[0].visual_description == "second pass"
