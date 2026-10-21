import uuid

from app.db.models import Case, VisualSearchLog
from app.documentary.visuals.search_log import SearchLedger


def _case(db):
    case = Case(canonical_title="Ledger", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


def test_a_query_is_new_until_a_provider_has_run_it(db_session):
    case = _case(db_session)
    ledger = SearchLedger(db_session, case.id)
    assert not ledger.seen("wikimedia", "Lizzie Borden")
    ledger.record("wikimedia", "Lizzie Borden", kind="exact", found_during="research",
                  entity_key="lb", candidates=7)
    assert ledger.seen("wikimedia", "Lizzie Borden")
    assert not ledger.seen("brave_images", "Lizzie Borden")


def test_the_same_query_in_another_spelling_is_the_same_query(db_session):
    case = _case(db_session)
    ledger = SearchLedger(db_session, case.id)
    ledger.record("brave_images", "Zürich  Hauptbahnhof", kind="exact", found_during="research",
                  entity_key=None, candidates=1)
    assert ledger.seen("brave_images", "zurich hauptbahnhof")


def test_a_new_ledger_knows_the_searches_of_an_earlier_one(db_session):
    case = _case(db_session)
    SearchLedger(db_session, case.id).record("wikimedia", "King Road", kind="context",
                                             found_during="production_search", entity_key="kr",
                                             candidates=0)
    assert SearchLedger(db_session, case.id).seen("wikimedia", "King Road")
    row = db_session.query(VisualSearchLog).filter_by(case_id=case.id).one()
    assert (row.kind, row.found_during, row.candidates) == ("context", "production_search", 0)


def test_the_ledger_counts_queries_per_provider(db_session):
    case = _case(db_session)
    ledger = SearchLedger(db_session, case.id)
    for q in ("a", "b"):
        ledger.record("wikimedia", q, kind="exact", found_during="research", entity_key=None, candidates=2)
    ledger.record("brave_images", "a", kind="exact", found_during="research", entity_key=None, candidates=3)
    assert ledger.counts() == {"wikimedia": 2, "brave_images": 1}
