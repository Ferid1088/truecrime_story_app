"""Logs, request ids and the ledger of calls to outside services.

* Logging: text or one-JSON-object-per-line, every record carries the request id of the request
  (or job) that caused it.
* Request id: taken from a sane `X-Request-ID` header or generated; echoed in the response.
* Ledger: every outgoing httpx call is timed and written to `provider_calls`. No keys, no bodies,
  no query strings. Writing never slows or breaks the call: it goes through a queue to one thread.
"""
from __future__ import annotations

import contextvars
import json
import logging
import queue
import re
import threading
import time
import uuid
from datetime import timedelta

import httpx

from app.core.config import settings
from app.utils import utc_now

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_ID_OK = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_QUIET_PATHS = {"/health"}
log = logging.getLogger("app.observability")


# ---------------------------------------------------------------------------------- logging
class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            out.update(extra)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def configure_logging(level: str | None = None, fmt: str | None = None) -> None:
    """Idempotent: replaces the handler it installed before, leaves foreign handlers alone."""
    import time as _t

    logging.Formatter.converter = _t.gmtime
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_tc", False):
            root.removeHandler(h)
    handler = logging.StreamHandler()
    handler._tc = True  # type: ignore[attr-defined]
    handler.addFilter(_RequestIdFilter())
    if (fmt or settings.log_format).lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel((level or settings.log_level).upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines carry full URLs (query strings)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------------- request id
class RequestContextMiddleware:
    """Pure ASGI: sets the request id, adds the response header, writes one access-log line."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"x-request-id"), "")
        rid = incoming if _ID_OK.match(incoming) else uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        t0 = time.perf_counter()
        status = 500

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if path not in _QUIET_PATHS:
                ms = round((time.perf_counter() - t0) * 1000)
                log.info("%s %s -> %s (%d ms)", scope.get("method", ""), path, status, ms,
                         extra={"fields": {"method": scope.get("method"), "path": path, "status": status, "ms": ms}})
            request_id_var.reset(token)


# ---------------------------------------------------------------------------------- ledger
_queue: queue.Queue = queue.Queue(maxsize=10000)
_thread: threading.Thread | None = None
_lock = threading.Lock()
_SKIP_HOSTS = {"testserver", "test"}  # in-process test clients, not outside services


def _usage(resp: httpx.Response) -> tuple[int | None, int | None, float | None]:
    """Token counts and cost from an LLM-style JSON body that is already in memory."""
    try:
        if "json" not in resp.headers.get("content-type", "") or len(resp.content) > 2_000_000:
            return None, None, None
        u = (resp.json() or {}).get("usage")
        if not isinstance(u, dict):
            return None, None, None
        tin = u.get("prompt_tokens", u.get("input_tokens"))
        tout = u.get("completion_tokens", u.get("output_tokens"))
        cost = u.get("cost")
        return (tin if isinstance(tin, int) else None, tout if isinstance(tout, int) else None,
                float(cost) if isinstance(cost, (int, float)) else None)
    except Exception:  # noqa: BLE001  (not JSON, not read yet, or an unexpected shape)
        return None, None, None


def record_call(request: httpx.Request, started: float, response: httpx.Response | None = None,
                error: BaseException | None = None, stream: bool = False) -> None:
    if not settings.provider_ledger or request.url.host in _SKIP_HOSTS:
        return
    try:
        status = response.status_code if response is not None else None
        tin = tout = cost = None
        size = None
        if response is not None:
            cl = response.headers.get("content-length")
            size = int(cl) if cl and cl.isdigit() else None
            if not stream:
                tin, tout, cost = _usage(response)
                size = size if size is not None else len(response.content)
        rid = request_id_var.get()
        row = dict(
            host=request.url.host[:120], method=request.method[:10], path=request.url.path[:300],
            status=status, ok=error is None and status is not None and status < 400,
            duration_ms=round((time.perf_counter() - started) * 1000), bytes_in=size,
            tokens_in=tin, tokens_out=tout, cost_usd=cost,
            error=(type(error).__name__ if error is not None else None),
            request_id=None if rid == "-" else rid,
        )
        _ensure_thread()
        _queue.put_nowait(row)
    except Exception as e:  # noqa: BLE001  (observability must never break a call)
        log.debug("ledger skipped: %s", e)


def _ensure_thread() -> None:
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_drain, name="provider-ledger", daemon=True)
            _thread.start()


def _write(rows: list[dict]) -> None:
    from app.db.base import SessionLocal
    from app.db.models import ProviderCall

    db = SessionLocal()
    try:
        now = utc_now()
        db.add_all([ProviderCall(created_at=now, **r) for r in rows])
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("could not write %d provider call(s) to the ledger: %s", len(rows), e)
    finally:
        db.close()


def _drain() -> None:
    while True:
        rows = [_queue.get()]
        try:
            while len(rows) < 100:
                rows.append(_queue.get(timeout=0.2))
        except queue.Empty:
            pass
        try:
            _write(rows)
        except Exception as e:  # noqa: BLE001  (the writer thread must outlive any single failure)
            log.warning("ledger writer error: %s", e)
        finally:
            for _ in rows:
                _queue.task_done()


def flush() -> None:
    """Wait until everything queued has been written (tests, shutdown)."""
    if _thread is not None and _thread.is_alive():
        _queue.join()


_installed = False


def install_httpx_ledger() -> None:
    """Wrap httpx's send() once so every client in the process (existing code included) is recorded."""
    global _installed
    if _installed:
        return
    _installed = True
    orig_async = httpx.AsyncClient.send
    orig_sync = httpx.Client.send

    async def send_async(self, request, **kw):
        t0 = time.perf_counter()
        try:
            resp = await orig_async(self, request, **kw)
        except BaseException as e:
            record_call(request, t0, error=e)
            raise
        record_call(request, t0, resp, stream=bool(kw.get("stream")))
        return resp

    def send_sync(self, request, **kw):
        t0 = time.perf_counter()
        try:
            resp = orig_sync(self, request, **kw)
        except BaseException as e:
            record_call(request, t0, error=e)
            raise
        record_call(request, t0, resp, stream=bool(kw.get("stream")))
        return resp

    httpx.AsyncClient.send = send_async  # type: ignore[method-assign]
    httpx.Client.send = send_sync  # type: ignore[method-assign]


def purge_old_calls() -> int:
    from app.db.base import SessionLocal
    from app.db.models import ProviderCall

    db = SessionLocal()
    try:
        n = db.query(ProviderCall).filter(
            ProviderCall.created_at < utc_now() - timedelta(days=settings.provider_ledger_days)
        ).delete()
        db.commit()
        return n
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("ledger purge failed: %s", e)
        return 0
    finally:
        db.close()


def setup() -> None:
    """Called by the API and the worker at start."""
    configure_logging()
    install_httpx_ledger()
