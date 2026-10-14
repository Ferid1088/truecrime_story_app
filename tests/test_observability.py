import asyncio
import json
import logging

import httpx
import pytest

from app.core import observability as obs
from app.core.config import settings
from app.db.base import SessionLocal
from app.db.models import ProviderCall


@pytest.fixture(autouse=True)
def ledger(monkeypatch):
    monkeypatch.setattr(settings, "provider_ledger", True)
    obs.install_httpx_ledger()
    obs.flush()
    db = SessionLocal()
    db.query(ProviderCall).delete()
    db.commit()
    db.close()
    yield


def rows():
    obs.flush()
    db = SessionLocal()
    try:
        return db.query(ProviderCall).order_by(ProviderCall.id).all()
    finally:
        db.close()


def mock(handler):
    return httpx.MockTransport(handler)


def test_llm_call_records_tokens_cost_and_never_the_query_or_key():
    def h(req):
        return httpx.Response(200, json={"usage": {"prompt_tokens": 11, "completion_tokens": 7, "cost": 0.0123}})

    async def go():
        async with httpx.AsyncClient(transport=mock(h)) as c:
            await c.post("https://llm.example.com/v1/chat/completions?key=SECRET123",
                         headers={"Authorization": "Bearer TOPSECRET"}, json={"prompt": "private text"})

    asyncio.run(go())
    (r,) = rows()
    assert (r.host, r.method, r.path, r.status, r.ok) == ("llm.example.com", "POST", "/v1/chat/completions", 200, True)
    assert (r.tokens_in, r.tokens_out, r.cost_usd) == (11, 7, 0.0123)
    dump = json.dumps({k: str(v) for k, v in r.__dict__.items() if not k.startswith("_")})
    for secret in ("SECRET123", "TOPSECRET", "private text"):
        assert secret not in dump


def test_http_error_and_exception_are_recorded_as_failures():
    def h(req):
        if req.url.path == "/boom":
            raise httpx.ConnectError("down")
        return httpx.Response(503)

    async def go():
        async with httpx.AsyncClient(transport=mock(h)) as c:
            await c.get("https://a.example.com/x")
            with pytest.raises(httpx.ConnectError):
                await c.get("https://a.example.com/boom")

    asyncio.run(go())
    a, b = rows()
    assert (a.status, a.ok, a.error) == (503, False, None)
    assert (b.status, b.ok, b.error) == (None, False, "ConnectError")


def test_sync_client_is_recorded_too():
    with httpx.Client(transport=mock(lambda r: httpx.Response(200, text="ok"))) as c:
        c.get("https://s.example.com/p")
    assert [r.host for r in rows()] == ["s.example.com"]


def test_every_brave_call_is_recorded_with_configured_estimated_cost():
    with httpx.Client(transport=mock(lambda r: httpx.Response(200, json={"web": {"results": []}}))) as c:
        c.get("https://api.search.brave.com/res/v1/web/search?q=case&count=10")
    (row,) = rows()
    assert row.host == "api.search.brave.com"
    assert row.model == "brave-search"
    assert row.estimated is True
    assert row.cost_usd == pytest.approx(0.005)
    assert row.cost_eur == pytest.approx(0.004469)


def test_the_api_itself_and_disabled_ledger_leave_no_rows(client, monkeypatch):
    client.get("/health")
    assert len(rows()) == 1
    monkeypatch.setattr(settings, "provider_ledger", False)
    with httpx.Client(transport=mock(lambda r: httpx.Response(200))) as c:
        c.get("https://off.example.com/")
    assert len(rows()) == 1


def test_a_broken_database_write_does_not_break_the_call(monkeypatch):
    monkeypatch.setattr(obs, "_write", lambda rows_: (_ for _ in ()).throw(RuntimeError("db gone")))
    with httpx.Client(transport=mock(lambda r: httpx.Response(200, text="fine"))) as c:
        assert c.get("https://x.example.com/").text == "fine"
    obs.flush()


def test_request_id_is_generated_echoed_and_untrusted_values_are_replaced(client):
    r = client.get("/health")
    assert len(r.headers["x-request-id"]) == 16
    assert client.get("/health", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"] == "abc-123"
    bad = client.get("/health", headers={"X-Request-ID": "x y\"<script>"}).headers["x-request-id"]
    assert bad != "x y\"<script>" and len(bad) == 16


def test_ledger_row_carries_the_request_id_of_the_caller():
    token = obs.request_id_var.set("req-77")
    try:
        with httpx.Client(transport=mock(lambda r: httpx.Response(200))) as c:
            c.get("https://r.example.com/")
    finally:
        obs.request_id_var.reset(token)
    assert rows()[0].request_id == "req-77"


def test_json_log_format_has_request_id_and_extra_fields():
    rec = logging.LogRecord("n", logging.INFO, "f", 1, "hello %s", ("w",), None)
    rec.request_id = "rid1"
    rec.fields = {"status": 200}
    out = json.loads(obs.JsonFormatter().format(rec))
    assert out["msg"] == "hello w" and out["request_id"] == "rid1" and out["status"] == 200 and out["level"] == "INFO"


def test_access_log_line_has_path_without_query(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.observability"):
        client.get("/api/ops/summary?hours=1&token=zzz")
    lines = [r for r in caplog.records if r.name == "app.observability"]
    assert lines and "token" not in lines[-1].getMessage() and "/api/ops/summary" in lines[-1].getMessage()


def test_ops_endpoints_summarise(client):
    def h(req):
        return httpx.Response(200, json={"usage": {"prompt_tokens": 5, "completion_tokens": 2, "cost": 0.5}})

    with httpx.Client(transport=mock(h)) as c:
        c.get("https://p.example.com/a")
        c.get("https://p.example.com/b")
    s = client.get("/api/ops/summary").json()
    p = next(x for x in s["providers"] if x["host"] == "p.example.com")
    assert p["calls"] == 2 and p["tokens_in"] == 10 and p["cost_usd"] == 1.0 and s["total_cost_usd"] == 1.0
    assert isinstance(s["jobs"], dict)
    calls = client.get("/api/ops/provider-calls", params={"host": "p.example.com", "limit": 1}).json()
    assert len(calls) == 1 and calls[0]["path"] == "/b"
    assert client.get("/api/ops/provider-calls", params={"only_errors": True, "host": "p.example.com"}).json() == []


def test_purge_removes_only_old_rows(monkeypatch):
    from datetime import timedelta

    from app.utils import utc_now

    monkeypatch.setattr(settings, "provider_ledger_days", 30)
    db = SessionLocal()
    common = dict(host="h", method="GET", path="/", ok=True, duration_ms=1)
    db.add_all([ProviderCall(created_at=utc_now() - timedelta(days=31), **common),
                ProviderCall(created_at=utc_now() - timedelta(days=1), **common)])
    db.commit()
    db.close()
    assert obs.purge_old_calls() == 1
    assert len(rows()) == 1
