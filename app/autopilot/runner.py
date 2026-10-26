"""The autopilot's orchestrator. One run walks the existing stages in order: research, the texts of
every language, one film job per language, the title suggestions, the Shorts, and the dry-run
publish queue. It adds no new way of producing anything and bypasses no gate: whatever only the
owner may decide, or whatever stopped, goes on the run's "needs you" list with a plain reason.

Every phase looks at what exists in the database and does only what is missing, so a restart of
the worker (or a retry of a film job through the app) simply carries on."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.autopilot import publish as P
from app.autopilot import state as S
from app.core.ai_config import ai_config
from app.db.base import SessionLocal
from app.db.models import (
    AutopilotRun,
    Case,
    CaseTitleCandidate,
    ChannelSetting,
    DocumentaryJob,
    Fact,
    RenderedShort,
    ShortProductionJob,
    StoryVersion,
    Video,
)
from app.documentary.pipeline.errors import JobPaused
from app.services.budget import BudgetService, BudgetStopped
from app.utils import utc_now

log = logging.getLogger("truecrime.autopilot")

FILM_DONE = ("completed", "completed_warnings")
FILM_ACTIVE = ("queued", "running", "cancelling")
SHORT_ACTIVE = ("queued", "running")
TERMINAL = ("completed", "cancelled")


class Blocked(Exception):
    """A phase cannot go on without the owner (or a fix): the reason is shown on the run page."""

    def __init__(self, kind: str, message: str, language: str | None = None):
        super().__init__(message)
        self.kind, self.language = kind, language


class Paused(Exception):
    """The run's case was paused (or the global stop is on): the run waits in
    "paused" until the case control resumes it."""


class Cancelled(Exception):
    pass


class Ports:
    """The calls that reach providers. The runner never names a model; tests replace this class."""

    async def authorize(self) -> None:
        from app.api import deps

        try:
            await deps.require_generation_authorized()
            await deps.require_search_available()
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            raise Blocked("provider", str(detail.get("message") or detail)) from exc

    async def research(self, db: Session, case: Case, check_cancel: Callable[[], None]) -> None:
        from app.documentary.jobs import research_case

        await research_case(db, case, check_cancel)

    async def write_texts(self, db: Session, case: Case, languages: list[str]) -> dict:
        from app.language_pipeline.orchestrator import LanguagePipeline
        from app.naming.agent import default_embedder
        from app.services.readiness import build_readiness

        minutes = float(ai_config.language_pipeline.requested_minutes_default)
        report = build_readiness(db, case.id, minutes)
        status = report["master_readiness"]["status"]
        if status in ("incomplete_evidence", "failed", "insufficient_research"):
            raise Blocked("evidence", f"The evidence is not enough to write the film "
                                      f"({report['master_readiness']['reason']}).")
        return await LanguagePipeline(embedder=default_embedder()).run_case(
            case.id, languages, minutes, parallel_languages=True)

    async def write_titles(self, db: Session, case: Case, languages: list[str]) -> None:
        from app.naming import agent as naming_agent
        from app.naming import api as naming_api

        await naming_agent.generate_case_titles(
            db, case, naming_api._gen(), naming_api._embedder(), languages)


def approved_final(db: Session, case_id: int, language: str) -> StoryVersion | None:
    return (db.query(StoryVersion)
            .filter(StoryVersion.case_id == case_id, StoryVersion.kind == "final",
                    StoryVersion.language == language, StoryVersion.approved_at.isnot(None))
            .order_by(StoryVersion.id.desc()).first())


def live_titles(db: Session, case_id: int, language: str) -> list[CaseTitleCandidate]:
    return (db.query(CaseTitleCandidate)
            .filter(CaseTitleCandidate.case_id == case_id, CaseTitleCandidate.language == language,
                    CaseTitleCandidate.status != "rejected")
            .order_by(CaseTitleCandidate.id).all())


PHASE_LABEL = {"research": "Research", "texts": "Writing the texts", "titles": "Title suggestions"}


def _youtube_clips_needing_review(db: Session, video: Video) -> list[str]:
    """V5: the YouTube-clip assets this film's production script uses that still carry
    `editorial_review_required` — the owner must approve or reject them, never a silent skip."""
    from app.db.models import ProductionScript, VisualAsset

    if video.production_script_id is None:
        return []
    ps = db.get(ProductionScript, video.production_script_id)
    if ps is None:
        return []
    shots = json.loads(ps.script_json or "{}").get("shots") or []
    codes = {s.get("asset_id") for s in shots if s.get("asset_id")}
    if not codes:
        return []
    reviewable = []
    for a in db.query(VisualAsset).filter(
            VisualAsset.asset_code.in_(codes),
            VisualAsset.provider == "youtube_clip",
            VisualAsset.rights_status == "editorial_review_required"):
        # P2d: a clip from an automatically proven official channel counts as reviewed;
        # every other YouTube clip keeps the blocking need
        spec = json.loads(a.spec_json or "{}")
        proof = ((spec.get("source_record") or {}).get("channel_proof")) or {}
        if proof.get("status") == "proven":
            continue
        reviewable.append(a.asset_code)
    return sorted(reviewable)


def explain_unapproved(db: Session, case_id: int, language: str) -> str | None:
    """Why the newest final text of a language is not approved, in the words of its own checks."""
    final = (db.query(StoryVersion)
             .filter(StoryVersion.case_id == case_id, StoryVersion.kind == "final",
                     StoryVersion.language == language, StoryVersion.status != "retired")
             .order_by(StoryVersion.id.desc()).first())
    if final is None:
        return None
    notes = json.loads(final.critic_notes or "{}")
    issues = (notes.get("checks") or {}).get("open_issues") or []
    if issues:
        return "; ".join(f"{i.get('severity')} {i.get('kind')}: {i.get('problem')}" for i in issues)
    failures = (notes.get("quality_gates") or {}).get("failures") or []
    return "; ".join(map(str, failures)) or None


class AutopilotRunner:
    def __init__(self, session_factory=SessionLocal, ports: Ports | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.session_factory = session_factory
        self.ports = ports or Ports()
        self.sleep = sleep
        self.cfg = ai_config.autopilot_settings()
        # the texts of all languages are written off the pass path: one task per run, so a
        # language's film starts as soon as its own final is approved instead of waiting for
        # every language's text
        self._texts_tasks: dict[int, asyncio.Task] = {}

    # -- the loop --------------------------------------------------------------------------
    async def run(self, run_id: int) -> None:
        while True:
            db = self.session_factory()
            try:
                run = db.get(AutopilotRun, run_id)
                if run is None or run.status in TERMINAL:
                    self._texts_tasks.pop(run_id, None)
                    return
                try:
                    finished = await self.advance(db, run)
                except Cancelled:
                    return
                except (Paused, JobPaused):
                    run = db.get(AutopilotRun, run_id)
                    if run is not None:
                        run.status = "paused"
                        run.updated_at = utc_now()
                        db.commit()
                    return
                except Exception as exc:  # noqa: BLE001 - one bad pass must not end the run
                    log.exception("autopilot run %s: pass failed", run_id)
                    db.rollback()
                    self._record_crash(db, run_id, exc)
                    finished = False
                if finished:
                    return
            finally:
                db.close()
            await self.sleep(self.cfg.tick_seconds)

    def _record_crash(self, db: Session, run_id: int, exc: Exception) -> None:
        run = db.get(AutopilotRun, run_id)
        assert run is not None
        S.add_need(run, "run:all:autopilot_error", "autopilot_error",
                   f"The autopilot hit an unexpected error and will try again: "
                   f"{type(exc).__name__}: {exc}"[:400])
        run.status, run.error = "waiting_owner", str(exc)[:1000]
        db.commit()

    async def advance(self, db: Session, run: AutopilotRun) -> bool:
        """One pass over the phases. True when the run is complete."""
        case = db.get(Case, run.case_id)
        assert case is not None
        languages = json.loads(run.languages_json)
        S.drop_blocking_needs(run)
        run.status, run.error, run.updated_at = "running", None, utc_now()
        db.commit()
        for phase in S.PHASES:
            if S.step_status(run, phase) == "done":
                continue
            self._check_cancel(db, run)
            S.set_step(run, phase, "running")
            try:
                done = await getattr(self, f"_{phase}")(db, run, case, languages)
            except Blocked as b:
                S.add_need(run, f"{phase}:{b.language or 'all'}:{b.kind}", b.kind, str(b),
                           language=b.language)
                done = False
            except BudgetStopped as stop:
                S.add_need(run, f"{phase}:all:budget", "budget", stop.reason)
                done = False
            waiting = any(n["blocking"] and n["key"].startswith(f"{phase}:") for n in S.needs(run))
            S.set_step(run, phase, "done" if done else ("waiting" if waiting else "running"),
                       S.step_detail(run, phase))
            db.commit()
            # Research must finish before writing can start.  Film jobs, however, are
            # deliberately allowed to start for languages whose text is already approved
            # while the remaining language texts are being written.  Nothing downstream of
            # the film phase may be advertised as running until every film is finished.
            if phase == "films" and not done:
                for later in ("titles", "shorts", "publish"):
                    if S.step_status(run, later) != "done":
                        S.set_step(run, later, "pending")
                db.commit()
                break
            if phase == "research" and not done:
                break
        all_done = all(S.step_status(run, p) == "done" for p in S.PHASES)
        blocking = any(n["blocking"] for n in S.needs(run))
        run.status = "completed" if all_done else ("waiting_owner" if blocking else "running")
        run.stage = next((p for p in S.PHASES if S.step_status(run, p) != "done"), None)
        run.updated_at = utc_now()
        if all_done:
            run.completed_at = utc_now()
        db.commit()
        return all_done

    def _check_cancel(self, db: Session, run: AutopilotRun) -> None:
        db.refresh(run)
        if run.status == "cancelled":
            raise Cancelled()
        from app.services import case_control
        if (case_control.global_stop_active(db)
                or case_control.case_held(db, run.case_id)):
            raise Paused()

    def _budget(self, db: Session, case: Case) -> None:
        BudgetService(db).check(case.id, "autopilot")

    # -- phases ------------------------------------------------------------------------------
    async def _try(self, db: Session, run: AutopilotRun, phase: str, call, *,
                   settled_on_return: bool) -> None:
        """One paid call of a phase, at most `max_attempts_per_phase` times per run: after that the
        autopilot only reports why it stopped. A call that returns is settled when its outcome is
        deterministic (the same inputs would give the same unapproved text)."""
        limit = self.cfg.max_attempts_per_phase
        if S.attempts(run, phase) >= limit:
            failure = S.recall(run, phase, "failure")
            if failure:
                raise Blocked(failure["kind"], failure["message"])
            return
        await self.ports.authorize()
        S.set_attempts(run, phase, S.attempts(run, phase) + 1)
        db.commit()
        try:
            await call()
        except (Cancelled, Paused, JobPaused, BudgetStopped):
            raise
        except Blocked as b:
            S.remember(run, phase, "failure", {"kind": b.kind, "message": str(b)})
            S.set_attempts(run, phase, limit)
            db.commit()
            raise
        except Exception as exc:  # noqa: BLE001 - shown to the owner
            message = f"{PHASE_LABEL[phase]} stopped: {exc}"
            S.remember(run, phase, "failure", {"kind": f"{phase}_failed", "message": message})
            db.commit()
            raise Blocked(f"{phase}_failed", message) from exc
        S.remember(run, phase, "failure", None)
        if settled_on_return:
            S.set_attempts(run, phase, limit)
        db.commit()

    async def _research(self, db: Session, run: AutopilotRun, case: Case, languages) -> bool:
        if db.query(Fact).filter(Fact.case_id == case.id).count():
            return True
        self._budget(db, case)
        await self._try(db, run, "research",
                        lambda: self.ports.research(db, case, lambda: self._check_cancel(db, run)),
                        settled_on_return=False)
        return bool(db.query(Fact).filter(Fact.case_id == case.id).count())

    async def _write_texts(self, case_id: int, languages: list[str]) -> dict:
        """One pass's background work: the shared draft, the English final, then the other
        languages in parallel. Runs on its own session, off the pass path — the run keeps
        advancing so a finished language's film starts while the rest are still written."""
        db = self.session_factory()
        try:
            case = db.get(Case, case_id)
            assert case is not None
            return await self.ports.write_texts(db, case, languages)
        finally:
            db.rollback()  # release the connection; close() would detach shared fixtures

    async def _texts(self, db: Session, run: AutopilotRun, case: Case, languages) -> bool:
        if all(approved_final(db, case.id, lang) is not None for lang in languages):
            return True
        self._budget(db, case)
        approved = sum(approved_final(db, case.id, lang) is not None for lang in languages)
        outcomes = S.recall(run, "texts", "outcomes", {})
        # A previous pass may have reached the attempt limit because English was still open.
        # English can become approved later (for example when its documentary job finishes),
        # so that old base-block is recoverable automatically.  Treat it as a new text fan-out,
        # without asking the owner to click anything and without retrying already approved text.
        english = ai_config.language_pipeline.draft_language
        base_recovered = (
            approved < len(languages)
            and approved_final(db, case.id, english) is not None
            and any("no approved" in str(outcomes.get(lang) or "").lower()
                    for lang in languages if lang != english)
        )
        provider_review_recovered = (
            approved < len(languages)
            and not S.recall(run, "texts", "provider_fallback_retry_used", False)
            and any(
                any(marker in str(outcomes.get(lang) or "").lower()
                    for marker in ("permission/quota", "content-risk", "risk rule"))
                for lang in languages if lang != english
            )
        )
        if base_recovered or provider_review_recovered:
            S.set_attempts(run, "texts", 0)
            S.remember(run, "texts", "failure", None)
            S.remember(run, "texts", "outcomes", {})
            if provider_review_recovered:
                S.remember(run, "texts", "provider_fallback_retry_used", True)
            outcomes = {}
        if approved > S.recall(run, "texts", "approved", 0):
            # more texts are approved than at the last attempt (the owner got one through):
            # the languages that wait on it may be tried again
            S.set_attempts(run, "texts", 0)
        limit = self.cfg.max_attempts_per_phase
        task = self._texts_tasks.get(run.id)
        if task is None and S.attempts(run, "texts") < limit:
            try:
                await self.ports.authorize()
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                raise Blocked("provider", str(detail.get("message") or detail)) from exc
            S.set_attempts(run, "texts", S.attempts(run, "texts") + 1)
            db.commit()
            task = asyncio.create_task(self._write_texts(case.id, languages))
            self._texts_tasks[run.id] = task
            await asyncio.sleep(0)  # one slice: a cheap fake or a cached draft settles inline
        if task is not None:
            if not task.done():
                return False
            self._texts_tasks.pop(run.id, None)
            if task.cancelled():
                return False
            err = task.exception()
            if err is not None:
                if isinstance(err, (Cancelled, Paused, JobPaused, BudgetStopped)):
                    raise err
                if isinstance(err, Blocked):
                    S.remember(run, "texts", "failure",
                               {"kind": err.kind, "message": str(err)})
                    S.set_attempts(run, "texts", limit)
                    db.commit()
                    raise err
                message = f"{PHASE_LABEL['texts']} stopped: {err}"
                S.remember(run, "texts", "failure",
                           {"kind": "texts_failed", "message": message})
                db.commit()
                raise Blocked("texts_failed", message) from err
            result = task.result() or {}
            S.remember(run, "texts", "outcomes", {
                o["language"]: o.get("error") or o.get("status")
                for o in result.get("languages", [])})
            S.remember(run, "texts", "failure", None)
            # a finished write is settled: a text that stayed unapproved is not paid for again
            S.set_attempts(run, "texts", limit)
            db.commit()
        else:
            failure = S.recall(run, "texts", "failure")
            if failure:
                raise Blocked(failure["kind"], failure["message"])
        db.expire_all()
        S.remember(run, "texts", "approved",
                   sum(approved_final(db, case.id, lang) is not None for lang in languages))
        outcomes = S.recall(run, "texts", "outcomes", {})
        still = [lang for lang in languages if approved_final(db, case.id, lang) is None]
        for lang in still:
            why = explain_unapproved(db, case.id, lang) or outcomes.get(lang) or "its gates are open"
            S.add_need(run, f"texts:{lang}:not_approved", "text_not_approved",
                       f"The {lang} final text is not approved: {why}.", language=lang)
        return not still

    def _visual_searches_done(self, db: Session, run: AutopilotRun, ids: dict[str, int],
                              english: str, languages: list[str]) -> bool:
        """The non-base film jobs wait until the base language's job has run its case-level
        visual searches: the search ledger dedups only queries already recorded, so two jobs
        whose gap filling overlaps would pay for the same search twice. The gate opens when the
        English job's mvp_research stage settled, when the job itself settled (a broken English
        film never blocks the others), or when English's text failed for good."""
        if english not in languages:
            return True
        job_id = ids.get(english)
        if job_id is not None:
            job = db.get(DocumentaryJob, job_id)
            if job is None or job.status not in ("queued", "running"):
                return True
            stage = next((s for s in json.loads(job.stages_json or "[]")
                          if s["name"] == "mvp_research"), None)
            return stage is not None and stage["status"] in (
                "done", "skipped", "degraded", "failed")
        if approved_final(db, run.case_id, english) is not None:
            return False  # the English film starts on this pass; the others wait for it
        return (S.step_status(run, "texts") == "done"
                or S.recall(run, "texts", "failure") is not None
                or S.recall(run, "texts", "outcomes") is not None
                or S.attempts(run, "texts") >= self.cfg.max_attempts_per_phase)

    async def _films(self, db: Session, run: AutopilotRun, case: Case, languages) -> bool:
        ids = S.film_job_ids(run)
        from app.documentary import jobs as J

        english = ai_config.language_pipeline.draft_language
        gate_open = self._visual_searches_done(db, run, ids, english, languages)
        held: list[str] = []
        for lang in sorted(languages, key=lambda lg: lg != english):
            final = approved_final(db, case.id, lang)
            if final is None or lang in ids:
                continue
            if lang != english and not gate_open:
                held.append(lang)
                continue
            self._budget(db, case)
            await self.ports.authorize()
            job = J.create_job(db, case, final, [lang], "full", None, self.cfg.render_profile)
            ids[lang] = job.id
            run.film_job_ids_json = json.dumps(ids)
            db.commit()
            J.launch(job.id)
        finished = 0
        for lang, job_id in ids.items():
            film = db.get(DocumentaryJob, job_id)
            if film is None:
                continue
            db.refresh(film)
            if film.status in FILM_DONE:
                finished += 1
            elif film.status not in FILM_ACTIVE:
                if self._auto_resume_transient_film(db, film, J):
                    continue
                S.add_need(run, f"films:{lang}:film_stopped", "film_stopped",
                           f"The {lang} film (job {film.id}) is {film.status}: "
                           f"{film.error or 'no reason recorded'}", language=lang)
        detail = f"{finished} of {len(languages)} films finished"
        if held:
            detail += (f"; {', '.join(held)} wait for the {english} film's "
                       "visual searches to finish")
        S.set_step(run, "films", "running", detail)
        return finished == len(languages)

    @staticmethod
    def _auto_resume_transient_film(db: Session, film: DocumentaryJob, jobs_module) -> bool:
        """Retry bounded provider overloads without turning them into owner blockers."""
        if film.status != "failed":
            return False
        error = (film.error or "").lower()
        transient_markers = (
            "http 503", "http 429", "cpu overloaded", "temporarily unavailable",
            "timed out", "timeout", "connection reset", "connection refused",
            "completion truncated at max_tokens", "non-json output",
        )
        if not any(marker in error for marker in transient_markers):
            return False
        from app.core.config import settings

        # ``auto_resumes`` predates provider retry handling and is also used by
        # worker-crash recovery.  Use the dedicated provider budget here so a
        # couple of crash recoveries do not consume the film's transient retry
        # allowance.
        limit = max(1, settings.job_max_provider_retries)
        if (film.auto_resumes or 0) >= limit:
            return False
        jobs_module.prepare_resume(film, count_auto_resume=True)
        db.commit()
        jobs_module.launch(film.id)
        return True

    def _ready_jobs(self, db: Session, run: AutopilotRun) -> dict[str, DocumentaryJob]:
        out = {}
        for lang, job_id in S.film_job_ids(run).items():
            job = db.get(DocumentaryJob, job_id)
            if job is not None and job.status in FILM_DONE:
                out[lang] = job
        return out

    async def _titles(self, db: Session, run: AutopilotRun, case: Case, languages) -> bool:
        target = ai_config.case_naming.candidates_per_language
        ready = list(self._ready_jobs(db, run))
        tried = S.recall(run, "titles", "tried", [])
        short = [lang for lang in ready
                 if len(live_titles(db, case.id, lang)) < target and lang not in tried]
        if short:
            self._budget(db, case)
            await self._try(db, run, "titles", lambda: self.ports.write_titles(db, case, short),
                            settled_on_return=False)
            S.remember(run, "titles", "tried", sorted({*tried, *short}))
            db.commit()
            db.expire_all()
        for lang in ready:
            have = len(live_titles(db, case.id, lang))
            if have < target:
                S.add_need(run, f"titles:{lang}:titles_short", "titles_short",
                           f"Only {have} of {target} title suggestions exist for {lang}.",
                           language=lang)
        return len(ready) == len(languages) and all(
            len(live_titles(db, case.id, lang)) >= target for lang in ready)

    async def _shorts(self, db: Session, run: AutopilotRun, case: Case, languages) -> bool:
        from app.shortform.pipeline import enqueue_for_finished_job

        jobs = self._ready_jobs(db, run)
        for job in jobs.values():
            if not db.query(ShortProductionJob).filter(
                    ShortProductionJob.film_job_id == job.id).count():
                enqueue_for_finished_job(db, job.id)
        rows = (db.query(ShortProductionJob)
                .filter(ShortProductionJob.film_job_id.in_([j.id for j in jobs.values()]))
                .all()) if jobs else []
        for row in rows:
            if row.status == "failed":
                S.add_need(run, f"shorts:{row.id}:short_failed", "short_failed",
                           f"A {row.language or ''} Shorts item failed: {row.error}",
                           language=row.language)
            elif row.status == "skipped":
                S.add_need(run, f"shorts:{row.id}:short_skipped", "short_skipped",
                           f"A {row.language or ''} short was skipped by its gate: {row.error}",
                           language=row.language, blocking=False)
        S.set_step(run, "shorts", "running",
                   f"{sum(r.status == 'done' for r in rows)} of {len(rows)} Shorts items done")
        return (len(jobs) == len(languages) and bool(rows)
                and not any(r.status in SHORT_ACTIVE or r.status == "failed" for r in rows))

    async def _publish(self, db: Session, run: AutopilotRun, case: Case, languages) -> bool:
        if not all(S.step_status(run, p) == "done" for p in ("films", "titles", "shorts")):
            return False
        films, shorts, handles, avatar_missing, outfit_missing, clip_review = \
            self._publish_inputs(db, run, case)
        for lang in outfit_missing:
            # O2: the final version needs an outfit chosen for its language; drafts do not.
            S.add_need(run, f"publish:{lang}:choose_outfit", "choose_outfit",
                       f"Needs you: choose outfit ({lang}) in the Autopilot start dialog "
                       "before starting the run.", language=lang)
        for lang, codes in clip_review.items():
            # V5: a YouTube clip in the film whose rights still need a human look is never a
            # silent skip — the owner approves or rejects it in the visuals review queue.
            S.add_need(run, f"publish:{lang}:review_youtube_clips", "review_youtube_clips",
                       f"Needs you: review the YouTube clips used in this film ({lang}): "
                       + ", ".join(codes)
                       + " — approve or reject them in the case's visuals review queue.",
                       language=lang)
        for lang in avatar_missing:
            # Owner decision 2026-10-22 (A3): the autopilot never publishes a film with no
            # approved avatar look for its language; it only ever asks the owner to approve one.
            S.add_need(run, f"publish:{lang}:approve_avatar", "approve_avatar",
                       f"No approved avatar look for {lang}: the selected outfit photo could not "
                       "be prepared for this film.", language=lang)
        planned, skipped = P.plan_requests(films, shorts, handles, utc_now())
        P.store_requests(db, run.id, case.id, planned)
        for item in skipped:
            S.add_need(run, f"publish:{item['short_id']}:{item['platform']}:skipped",
                       "publish_skipped",
                       f"Short {item['short_id']} ({item['language']}) is not queued for "
                       f"{item['platform']}: {item['reason']}", language=item["language"],
                       blocking=False)
        waiting = (db.query(RenderedShort)
                   .filter(RenderedShort.film_job_id.in_(S.film_job_ids(run).values()),
                           RenderedShort.status == "done",
                           RenderedShort.approval_state != "approved").all())
        for lang in languages:
            count = sum(1 for r in waiting if r.language == lang)
            if count:
                S.add_need(run, f"publish:{lang}:approve_shorts", "approve_shorts",
                           f"{count} {lang} shorts wait for your approval before real publishing.",
                           language=lang, blocking=False)
        S.add_need(run, "publish:all:confirm_titles", "confirm_titles",
                   "Choose one title per language; the dry run used suggestion 1 as an "
                   "unconfirmed default.", blocking=False)
        S.set_step(run, "publish", "running",
                   f"{len(planned)} dry-run requests queued; nothing was sent")
        return True

    def _publish_inputs(self, db: Session, run: AutopilotRun, case: Case
                        ) -> tuple[list[P.FilmInput], list[P.ShortInput],
                                  dict[tuple[str, str], str], list[str], list[str],
                                       dict[str, list[str]]]:
        from app.documentary import storage
        from app.documentary.production import host_timeline as HT

        job_ids = list(S.film_job_ids(run).values())
        films = []
        avatar_missing = []
        outfit_missing = []
        clip_review: dict[str, list[str]] = {}
        for video in db.query(Video).filter(Video.job_id.in_(job_ids),
                                            Video.state == "rendered").all():
            if ai_config.host.required_for_film and video.production_script_id is not None:
                from app.documentary import outfits as OF
                if OF.needs_outfit_choice(db, case.id, video.language):
                    outfit_missing.append(video.language)
                    continue
                if not HT.avatar_present(db, video.production_script_id):
                    avatar_missing.append(video.language)
                    continue
            codes = _youtube_clips_needing_review(db, video)
            if codes:
                clip_review[video.language] = codes
                continue
            titles = live_titles(db, case.id, video.language)
            films.append(P.FilmInput(
                language=video.language, video_id=video.id, file_path=video.file_path or "",
                title=titles[0].title if titles else (video.title or ""),
                description=video.youtube_description or "",
                tags=json.loads(video.youtube_tags_json or "[]")))
        shorts = []
        for short in db.query(RenderedShort).filter(RenderedShort.film_job_id.in_(job_ids),
                                                    RenderedShort.status == "done").all():
            files = json.loads(short.files_json or "{}")
            packages = {}
            for platform, rel in (files.get("platforms") or {}).items():
                path = storage.resolve(rel)
                if path is not None and path.exists():
                    packages[platform] = json.loads(path.read_text(encoding="utf-8"))
            shorts.append(P.ShortInput(short_id=short.id, language=short.language,
                                       packages=packages))
        handles = {(c.language, c.platform): c.handle for c in db.query(ChannelSetting).all()}
        return films, shorts, handles, avatar_missing, outfit_missing, clip_review
