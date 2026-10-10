"""Stage + runner: the bookkeeping every pipeline stage shares.

A stage is a small class: a `name`, whether its failure may be survived
(`optional`), and `run()` returning its result dict. The runner saves each
stage's attempt, timing, errors and result on the job before the next stage
starts, honours cancellation, and skips stages that are already done."""

from __future__ import annotations

import json
import logging
from typing import ClassVar

from app.core.concurrency import gather_limited
from app.db.models import DocumentaryJob
from app.documentary.pipeline.errors import JobCancelled
from app.utils import utc_now

log = logging.getLogger(__name__)


class Stage:
    name: ClassVar[str]
    optional: ClassVar[bool] = False

    def __init__(self, ctx, lang: str | None = None):
        self.ctx = ctx
        self.lang = lang

    @property
    def stage_name(self) -> str:
        return f"{self.name}:{self.lang}" if self.lang else self.name

    async def run(self) -> dict | None:
        raise NotImplementedError


class StageRunner:
    def __init__(self, db, job: DocumentaryJob):
        self.db = db
        self.job = job
        self.stages = json.loads(job.stages_json or "[]")
        self.result = json.loads(job.result_json or "{}")
        self.running: list[str] = []
        self.lang_errors: dict[str, str] = {}

    # -- bookkeeping ---------------------------------------------------------
    def _save(self):
        self.job.stages_json = json.dumps(self.stages, ensure_ascii=False, default=str)
        self.job.result_json = json.dumps(self.result, ensure_ascii=False, default=str)
        done = sum(1 for s in self.stages if s["status"] in ("done", "skipped", "degraded"))
        self.job.progress = done / max(len(self.stages), 1)
        self.job.stage = (", ".join(self.running) or None) if self.running else None
        if self.job.stage and len(self.job.stage) > 60:
            self.job.stage = self.job.stage[:57] + "..."
        self.job.updated_at = utc_now()
        self.db.commit()

    def _check_cancel(self):
        self.db.refresh(self.job)
        if self.job.status == "cancelling":
            raise JobCancelled()

    def _entry(self, name: str) -> dict:
        st = next((s for s in self.stages if s["name"] == name), None)
        if st is None:  # jobs created before this stage existed
            st = {"name": name, "status": "pending", "detail": None}
            self.stages.append(st)
        return st

    async def _stage(self, name: str, fn):
        """Run one stage once: its result is saved on the job before the
        next stage starts. Every attempt is counted and timed, every
        failure kept (type + message) — a retry does not erase the last
        one. A result marked {"degraded": reason} is kept and shown as
        degraded (the film went on without that part; the reason is on
        the stage and in the job result) — a new run redoes it."""
        st = self._entry(name)
        if st["status"] in ("done", "skipped", "degraded"):
            return st.get("detail")
        self._check_cancel()
        st["status"] = "running"
        st["attempts"] = int(st.get("attempts") or 0) + 1
        st["started_at"] = utc_now().isoformat()
        st.pop("finished_at", None)
        self.running.append(name)
        self._save()
        try:
            detail = await fn()
        except Exception as e:
            st["status"] = "failed"
            st["detail"] = str(e)[:500]
            st["error_type"] = type(e).__name__
            st["finished_at"] = utc_now().isoformat()
            st["errors"] = (st.get("errors") or [])[-4:] + [
                {"at": st["finished_at"], "attempt": st["attempts"],
                 "type": type(e).__name__, "message": str(e)[:300]}]
            if name in self.running:
                self.running.remove(name)
            if not self.db.is_active:
                self.db.rollback()
            self._save()
            raise
        degraded = isinstance(detail, dict) and detail.get("degraded")
        st["status"] = ("skipped" if isinstance(detail, dict) and detail.get("skipped")
                        else "degraded" if degraded else "done")
        st["detail"] = detail
        st["finished_at"] = utc_now().isoformat()
        st.pop("error_type", None)
        if degraded:
            self.result.setdefault("degraded", {})[name] = str(degraded)[:300]
        if name in self.running:
            self.running.remove(name)
        self._save()
        return detail

    def _skip_language(self, lang: str, reason: str):
        for st in self.stages:
            if st["name"].endswith(f":{lang}") and st["status"] == "pending":
                st["status"] = "blocked"
                st["detail"] = reason[:300]
        self._save()

    # -- running stages --------------------------------------------------------
    async def execute(self, stage: Stage):
        """Run a stage object; an optional stage's failure is recorded but does
        not stop the job."""
        if not stage.optional:
            return await self._stage(stage.stage_name, stage.run)
        try:
            return await self._stage(stage.stage_name, stage.run)
        except JobCancelled:
            raise
        except Exception as e:
            log.warning("optional stage %s failed: %s", stage.stage_name, e)
            self.result.setdefault("warnings", {})[stage.stage_name] = \
                f"{type(e).__name__}: {e}"[:300]
            self._save()
            return None

    async def _parallel(self, coros: list, shared_from: int, shared_count: int | None = None):
        """Run branches together. Failures of language branches are
        recorded by the branches themselves; a cancelled job or a failed
        SHARED branch (indices shared_from .. +shared_count) ends the job
        after every branch has stopped."""
        results = await gather_limited(None, coros, return_exceptions=True)
        shared = range(shared_from, shared_from + (shared_count if shared_count is not None
                                                   else len(coros) - shared_from))
        for r in results:
            if isinstance(r, JobCancelled):
                raise r
        for k, r in enumerate(results):
            if isinstance(r, BaseException) and k in shared:
                raise r
