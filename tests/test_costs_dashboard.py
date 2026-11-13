"""The cost dashboard: every ledger row gets a step, language, vendor and accuracy by the same rules for old and new rows; a call written twice counts once; the matrix,
the drill-down to the single call, the shared work and the bills of the vendors add up. Everything runs on a scratch case."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from app.costs import backfill, dedupe
from app.costs import queries as Q
from app.costs import registry as R
from app.db.models import Case, CostInvoice, ProviderCall
from app.services.budget import BudgetService
from app.utils import utc_now


@pytest.fixture
def case(db_session):
    row = Case(canonical_title="Cost proof", slug=f"cost-{uuid4().hex[:8]}", language="en")
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(ProviderCall).filter(ProviderCall.case_id == row.id).delete()
    db_session.query(CostInvoice).filter(CostInvoice.note == "test").delete()
    db_session.delete(row)
    db_session.commit()


def add(db, case, *, stage, eur, host="budget-ledger", model="gemini-3.8-flash", tin=100, tout=50, at=None, language=None, **extra):
    found = R.label(host=host, stage=stage, model=model, cost_eur=eur, estimated=extra.get("estimated"), cost_source=extra.get("cost_source"), language=language)
    row = ProviderCall(created_at=at or utc_now(), host=host, method="CALL", path="/x", status=200, case_id=case.id, stage=stage, model=model, cost_eur=eur, tokens_in=tin, tokens_out=tout,
                       language=found.language, step=found.step, vendor=found.vendor, subject=found.subject, accuracy=found.accuracy, attribution="live", **extra)
    db.add(row)
    db.commit()
    return row


def test_the_rules_find_step_language_vendor_and_how_sure_the_price_is():
    assert R.step_of("reading: write fa_b01_g01_01") == "text_polish" and R.language_of("reading: write fa_b01_g01_01") == "fa"
    assert R.step_of("tts:fa") == "voice" and R.language_of("tts:fa") == "fa" and R.vendor_of("budget-ledger", "eleven_v4", "tts:fa") == "elevenlabs"
    assert R.step_of("heygen:avatar") == "avatar" and R.vendor_of("budget-ledger", "heygen_avatar_iii", "heygen:avatar") == "heygen"
    assert R.step_of("image:cover") == "covers" and R.step_of("mvp_direct") == "visuals" and R.step_of("something new") == "other"
    assert R.language_of("irab: arbitrate") is None and R.language_of("Spoken Writer (tr)") == "tr" and R.language_of("host angle en s4") == "en"
    assert R.vendor_of("api.search.brave.com", "brave-search", None) == "brave" and R.vendor_of("budget-ledger", "claude-sonnet-5-5", "story plan") == "apimaster"
    assert R.accuracy_of(None, False, None) == "none" and R.accuracy_of(1.0, True, None) == "estimated" and R.accuracy_of(1.0, False, "assumed-unverified") == "estimated"
    assert R.accuracy_of(1.0, False, "provider_reported") == "reported" and R.accuracy_of(1.0, False, "computed_from_tokens") == "computed"
    assert "research" in R.shared_steps() and "voice" not in R.shared_steps()


def test_a_call_written_by_the_network_layer_and_the_ledger_counts_once(db_session, case):
    now = utc_now()
    ledger = add(db_session, case, stage="reading: write fa_b01", eur=0.5, at=now)
    wire = add(db_session, case, stage=None, host="apimaster.ai", eur=0.5, at=now + timedelta(seconds=2))
    add(db_session, case, stage=None, host="apimaster.ai", eur=0.7, tin=999, tout=1, at=now)             # other tokens: another call
    add(db_session, case, stage="tts:fa", eur=0.2, host="budget-ledger", tin=None, tout=None, at=now)        # no tokens: never a repeat
    assert dedupe.mark_duplicates(db_session, case_id=case.id) == 1
    db_session.refresh(wire)
    assert wire.duplicate_of == ledger.id
    assert dedupe.mark_duplicates(db_session, case_id=case.id) == 0                                       # nothing twice
    assert Q.totals(db_session, case.id)["real_eur"] == pytest.approx(1.4) and Q.totals(db_session, case.id)["gross_eur"] == pytest.approx(1.9)
    assert Q.totals(db_session, case.id)["repeats_eur"] == pytest.approx(0.5)


def test_test_data_and_unpriced_calls_are_not_real_money(db_session, case):
    add(db_session, case, stage="reading", eur=2.0)
    add(db_session, case, stage="reading", eur=9.0, is_test_data=True)
    add(db_session, case, stage="reading", eur=None, host="api.elevenlabs.io", model=None)
    assert Q.totals(db_session, case.id)["real_eur"] == pytest.approx(2.0)


def test_the_matrix_adds_up_and_the_shared_work_can_be_spread_evenly(db_session, case):
    add(db_session, case, stage="Spoken Writer (fa)", eur=3.0)
    add(db_session, case, stage="Spoken Writer (tr)", eur=1.0)
    add(db_session, case, stage="research", eur=4.0)                                    # shared by nature
    add(db_session, case, stage="reading", eur=0.5)                                     # a language step without a language
    plain = Q.matrix(db_session, case.id, "none")
    assert plain["total_eur"] == pytest.approx(8.5)
    assert plain["column_totals"]["fa"]["eur"] == pytest.approx(3.0) and plain["column_totals"][R.SHARED]["eur"] == pytest.approx(4.0)
    assert plain["column_totals"][R.UNKNOWN_LANGUAGE]["eur"] == pytest.approx(0.5)
    even = Q.matrix(db_session, case.id, "even")
    assert even["languages"] == ["fa", "tr"] and even["column_totals"]["fa"]["allocated_eur"] == pytest.approx(2.0) and even["column_totals"]["tr"]["allocated_eur"] == pytest.approx(2.0)
    assert even["total_eur"] == pytest.approx(8.5)                                     # a share is not extra money


def test_the_drill_goes_from_the_step_to_the_vendor_to_the_single_call(db_session, case):
    first = add(db_session, case, stage="tts:fa", eur=1.5, model="eleven_v4", host="api.elevenlabs.io", tin=None, tout=None)
    add(db_session, case, stage="tts:de", eur=0.5, model="eleven_v4", host="api.elevenlabs.io", tin=None, tout=None)
    add(db_session, case, stage="reading: write fa_b01_g01_01", eur=0.25)
    by_step = {r["key"]: r["eur"] for r in Q.grouped(db_session, case.id, "step")}
    assert by_step == {"voice": 2.0, "text_polish": 0.25}
    inside = Q.grouped(db_session, case.id, "language", {"step": "voice"})
    assert {r["key"]: r["eur"] for r in inside} == {"fa": 1.5, "de": 0.5}
    vendors = Q.grouped(db_session, case.id, "vendor", {"step": "voice", "language": "fa"})
    assert [(r["key"], r["eur"]) for r in vendors] == [("elevenlabs", 1.5)]
    found = Q.calls(db_session, case.id, {"step": "voice", "language": "fa", "vendor": "elevenlabs"})
    assert found["total"] == 1 and found["calls"][0]["id"] == first.id and found["calls"][0]["cost_eur"] == 1.5 and found["calls"][0]["attribution"] == "live"


def test_a_new_call_is_labelled_when_the_ledger_writes_it_and_an_old_one_by_the_backfill(db_session, case):
    BudgetService(db_session).record(case_id=case.id, stage="heygen:avatar", model="heygen_avatar_iii", units=7.0, cost_eur=0.24, estimated=True, cost_source="estimate",
                                     language="de", subject="short clip C2 hook")
    new = db_session.query(ProviderCall).filter(ProviderCall.case_id == case.id).one()
    assert (new.step, new.vendor, new.language, new.subject, new.accuracy, new.attribution) == ("avatar", "heygen", "de", "short clip C2 hook", "estimated", "live")
    old = ProviderCall(host="budget-ledger", method="CALL", path="/x", case_id=case.id, stage="tts:fr", model="eleven_v4", cost_eur=0.1)
    db_session.add(old)
    db_session.commit()
    assert backfill.label_rows(db_session) >= 1
    db_session.refresh(old)
    assert (old.step, old.language, old.vendor, old.accuracy, old.attribution) == ("voice", "fr", "elevenlabs", "computed", "rule")


def test_the_bills_are_compared_with_the_ledger(db_session, case):
    month = utc_now().strftime("%Y-%m")
    add(db_session, case, stage="tts:fa", eur=3.0, model="eleven_v4", host="api.elevenlabs.io", tin=None, tout=None)
    db_session.add(CostInvoice(vendor="elevenlabs", period=month, amount_eur=1.0, note="test"))
    db_session.commit()
    row = next(r for r in Q.reconciliation(db_session) if (r["vendor"], r["period"]) == ("elevenlabs", month))
    assert row["invoice_eur"] == 1.0 and row["ledger_eur"] >= 3.0 and row["difference_eur"] == pytest.approx(row["ledger_eur"] - 1.0, abs=1e-3)


def test_the_api_serves_the_overview_the_case_the_calls_and_the_bills(client, db_session, case):
    add(db_session, case, stage="tts:fa", eur=1.5, model="eleven_v4", host="api.elevenlabs.io", tin=None, tout=None)
    assert any(c["case_id"] == case.id for c in client.get("/api/costs/overview").json()["cases"])
    summary = client.get(f"/api/costs/cases/{case.id}?allocation=even").json()
    assert summary["totals"]["real_eur"] == pytest.approx(1.5) and summary["matrix"]["rows"][0]["step"] == "voice"
    grouped = client.get(f"/api/costs/cases/{case.id}/group", params={"by": "vendor", "step": "voice"}).json()
    assert grouped["rows"][0]["key"] == "elevenlabs" and grouped["totals"]["eur"] == pytest.approx(1.5)
    listed = client.get(f"/api/costs/cases/{case.id}/calls", params={"vendor": "elevenlabs"}).json()
    assert listed["total"] == 1
    live = client.get(f"/api/costs/cases/{case.id}/live").json()
    assert live["calls"][0]["id"] == listed["calls"][0]["id"] and client.get(f"/api/costs/cases/{case.id}/live", params={"after_id": live["last_id"]}).json()["calls"] == []
    assert "per_language" in client.get(f"/api/costs/cases/{case.id}/analysis").json()
    month = utc_now().strftime("%Y-%m")
    saved = client.put("/api/costs/invoices", json={"vendor": "heygen", "period": month, "amount_eur": 12.5, "note": "test"})
    assert saved.status_code == 200 and any(r["invoice_eur"] == 12.5 for r in saved.json()["reconciliation"])
    invoice_id = next(r["invoice_id"] for r in saved.json()["reconciliation"] if r["invoice_eur"] == 12.5)
    assert client.put("/api/costs/invoices", json={"vendor": "nobody", "period": month, "amount_eur": 1}).status_code == 422
    assert client.put("/api/costs/invoices", json={"vendor": "heygen", "period": "2026-13", "amount_eur": 1}).status_code == 422
    assert client.delete(f"/api/costs/invoices/{invoice_id}").status_code == 200
    assert client.get("/api/costs/cases/999999999").status_code == 404


def test_the_series_have_zero_filled_buckets_and_fold_the_steps_without_a_colour(db_session, case):
    add(db_session, case, stage="tts:fa", eur=2.0, model="eleven_v4", host="api.elevenlabs.io", tin=None, tout=None)
    add(db_session, case, stage="research", eur=1.0)
    add(db_session, case, stage="critique", eur=0.5)
    by_vendor = Q.series(db_session, case.id, "vendor", "hour", 24)
    assert len(by_vendor["labels"]) == 24 and {s["key"] for s in by_vendor["series"]} == {"elevenlabs", "apimaster"}
    assert all(len(s["values"]) == 24 for s in by_vendor["series"]) and sum(sum(s["values"]) for s in by_vendor["series"]) == pytest.approx(3.5)
    by_step = Q.series(db_session, case.id, "step", "day", 30)
    assert len(by_step["labels"]) == 30 and {s["key"]: round(sum(s["values"]), 2) for s in by_step["series"]} == {"voice": 2.0, "other_steps": 1.5}


def test_the_phases_come_in_pipeline_order_split_by_vendor_and_language(db_session, case):
    add(db_session, case, stage="tts:fa", eur=2.0, model="eleven_v4", host="api.elevenlabs.io", tin=None, tout=None)
    add(db_session, case, stage="tts:de", eur=1.0, model="eleven_v4", host="api.elevenlabs.io", tin=None, tout=None)
    add(db_session, case, stage="research", eur=4.0)
    add(db_session, case, stage="Spoken Writer (fa)", eur=0.5)
    found = Q.phases(db_session, case.id)
    assert [s["key"] for s in found["steps"]] == ["research", "language_text", "voice"]
    voice = found["steps"][2]
    assert voice["vendors"] == {"elevenlabs": 3.0} and voice["languages"] == {"fa": 2.0, "de": 1.0} and voice["total_eur"] == 3.0
    assert found["steps"][0]["shared"] is True and found["steps"][0]["languages"] == {R.SHARED: 4.0}
    assert "research" not in {s["key"] for s in Q.phases(db_session, 999999999)["steps"]}
