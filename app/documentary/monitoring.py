"""The Monitoring tab: what runs, what waits, what stopped and what the owner has to do about it, in one snapshot.

Every finding is an `Issue` with the place in the app where it is solved (a workspace tab, a page, or a job action)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.config import settings
from app.core.env import env_value
from app.db.base import get_db
from app.db.models import Case, DocumentaryJob, MvpFilmReview, ProviderCall, VisualPlan
from app.documentary import jobs as J
from app.services.budget import BudgetService
from app.utils import ensure_utc, utc_now

router = APIRouter(tags=["monitoring"])

ACTIVE = ("queued", "running", "cancelling")
STOPPED = ("failed", "interrupted", "cancelled", "partial")
RANK = {"problem": 0, "attention": 1, "info": 2}


@dataclass
class Action:
    label: str
    kind: str                 # tab | href | resume | cancel
    target: str               # the tab name, the path or the job id


@dataclass
class Issue:
    severity: str             # problem | attention | info
    area: str
    title: str
    detail: str
    actions: list[Action] = field(default_factory=list)


def seconds_since(moment) -> float | None:
    moment = ensure_utc(moment) if moment else None
    return None if moment is None else round(max((utc_now() - moment).total_seconds(), 0.0), 1)


def stage_base(name: str) -> str:
    return name.split(":")[0]


class Monitor:
    """What every monitor shares: the settings, the findings it collects and how jobs and services are described."""

    def __init__(self, db: Session):
        self.db = db
        self.cfg = ai_config.monitoring_settings()
        self.issues: list[Issue] = []
        self.case_titles: dict[int, str] = {}
        self.views: dict[int, dict] = {}

    # -- helpers ---------------------------------------------------------
    def tab_for(self, stage: str | None) -> str:
        return self.cfg.stage_tabs.get(stage_base(stage or ""), "Run")

    def open_tab(self, stage: str | None, label: str | None = None) -> Action:
        tab = self.tab_for(stage)
        return Action(label or f"Open {tab}", "tab", tab)

    def raise_issue(self, severity: str, area: str, title: str, detail: str, *actions: Action) -> None:
        self.issues.append(Issue(severity, area, title, detail, list(actions)))

    # -- jobs ------------------------------------------------------------
    def stage_view(self, job: DocumentaryJob) -> list[dict]:
        out = []
        for s in json.loads(job.stages_json or "[]"):
            started, finished = s.get("started_at"), s.get("finished_at")
            seconds = None
            if started:
                begin = ensure_utc(_parse(started))
                end = ensure_utc(_parse(finished)) if finished else utc_now()
                seconds = round(max((end - begin).total_seconds(), 0.0), 1)
            out.append({"name": s["name"], "status": s.get("status", "pending"), "attempts": s.get("attempts", 0),
                        "seconds": seconds, "error": s.get("error"), "tab": self.tab_for(s["name"])})
        return out

    def job_view(self, job: DocumentaryJob) -> dict:
        data = J.job_dict(job)
        stages = self.stage_view(job)
        call = (data["result"] or {}).get("call_progress") or {}
        return {
            "id": job.id, "case_id": job.case_id, "case_title": self.case_titles.get(job.case_id),
            "status": job.status, "stage": job.stage, "progress": data["progress"],
            "languages": data["languages"], "error": job.error, "next_action": data["next_action"],
            "worker_id": job.worker_id, "heartbeat_age_s": seconds_since(job.heartbeat_at),
            "age_s": seconds_since(job.created_at), "idle_s": seconds_since(job.updated_at),
            "stages": stages, "stage_counts": _counts(stages),
            "current_call": call if call.get("active") else None,
            "warnings": [{"stage": w.get("stage"), "message": w.get("message")}
                         for w in (data["result"] or {}).get("warnings") or []],
        }

    def providers(self) -> dict:
        since = utc_now() - timedelta(minutes=self.cfg.provider_window_minutes)
        rows = self.db.query(ProviderCall).filter(ProviderCall.created_at >= since).all()
        hosts: dict[str, dict] = {}
        for row in rows:
            h = hosts.setdefault(row.host, {"host": row.host, "calls": 0, "errors": 0, "last_status": None,
                                            "avg_ms": 0, "_ms": 0})
            h["calls"] += 1
            h["_ms"] += row.duration_ms or 0
            if not row.ok:
                h["errors"] += 1
                h["last_status"] = row.status
        for h in hosts.values():
            h["avg_ms"] = round(h.pop("_ms") / h["calls"])
            h["error_rate"] = round(h["errors"] / h["calls"], 3)
            if h["calls"] >= self.cfg.provider_min_calls and h["error_rate"] >= self.cfg.provider_error_rate:
                self.raise_issue("attention", "provider", f"{h['host']} fails {h['error_rate']:.0%} of its calls",
                                 f"{h['errors']} of {h['calls']} calls in the last "
                                 f"{int(self.cfg.provider_window_minutes)} min (last status {h['last_status']}).",
                                 Action("Open Operations", "href", "/ops"))
        errors = (self.db.query(ProviderCall).filter(ProviderCall.ok.is_(False))
                  .order_by(ProviderCall.id.desc()).limit(self.cfg.recent_provider_errors).all())
        return {"window_minutes": self.cfg.provider_window_minutes,
                "hosts": sorted(hosts.values(), key=lambda h: -h["calls"]),
                "recent_errors": [{"at": e.created_at, "host": e.host, "path": e.path, "status": e.status}
                                  for e in errors]}

    def services(self, running: list[DocumentaryJob]) -> list[dict]:
        started = perf_counter()
        self.db.execute(text("SELECT 1"))
        db_ms = round((perf_counter() - started) * 1000, 1)
        beats = [seconds_since(j.heartbeat_at) for j in running if j.heartbeat_at]
        lease = settings.job_lease_seconds
        if not running:
            worker = {"state": "idle", "detail": "No job is running, so the worker has nothing to report."}
        elif beats and min(beats) <= lease:
            worker = {"state": "ok", "detail": f"Last heartbeat {int(min(beats))} s ago."}
        else:
            worker = {"state": "problem", "detail": "A job is running but no worker reports for it."}
        search = bool(env_value("BRAVE_SEARCH_API_KEY"))
        if not search:
            self.raise_issue("problem", "search", "The Brave Search key is not configured",
                             "Research cannot run without it.", Action("Open Settings", "href", "/settings"))
        return [{"name": "API", "state": "ok", "detail": "Answering this request."},
                {"name": "Database", "state": "ok", "detail": f"{db_ms} ms"},
                {"name": "Worker", **worker},
                {"name": "Brave Search", "state": "ok" if search else "problem",
                 "detail": "Key present." if search else "Key missing."}]


class CaseMonitor(Monitor):
    def __init__(self, db: Session, case_id: int):
        super().__init__(db)
        self.case = db.get(Case, case_id)
        if self.case is None:
            raise HTTPException(404, "Case not found")

    def check_jobs(self, jobs: list[DocumentaryJob]) -> None:
        lease = settings.job_lease_seconds
        for job in jobs:
            view = self.views[job.id]
            if job.status == "running":
                beat = view["heartbeat_age_s"]
                if beat is not None and beat > lease:
                    self.raise_issue(
                        "problem", "worker", f"Job #{job.id}: the worker has not reported for {int(beat)} s",
                        f"A job is considered dead after {int(lease)} s without a heartbeat; the worker queues it "
                        "again by itself while it is alive. If it stays like this the worker is down: run "
                        "scripts/dev_up.sh --status.", Action("Open the Run tab", "tab", "Run"))
                running = next((s for s in view["stages"] if s["status"] == "running"), None)
                if running and (running["seconds"] or 0) > self.cfg.stuck_stage_minutes * 60:
                    self.raise_issue(
                        "attention", "stage",
                        f"Stage {running['name']} has run for {int(running['seconds'] // 60)} min",
                        "Longer than usual. Check the call in progress on the Run tab; cancel the job if it is stuck.",
                        self.open_tab(running["name"]), Action("Cancel the job", "cancel", str(job.id)))
            elif job.status == "queued" and (view["age_s"] or 0) > self.cfg.queued_wait_seconds:
                self.raise_issue(
                    "attention", "queue", f"Job #{job.id} has waited {int(view['age_s'])} s in the queue",
                    "No worker has taken it. Either every job slot is busy or the worker is not running.",
                    Action("Open the Run tab", "tab", "Run"))
            elif job.status in STOPPED:
                self.stopped_job(job, view)
            if job.status != "running":
                stale = [s["name"] for s in view["stages"] if s["status"] == "running"]
                if stale and job.status in STOPPED:
                    self.raise_issue("info", "stage", f"Job #{job.id}: stage shown as running while the job is {job.status}",
                                     f"{', '.join(stale)} was running when the job stopped; resuming runs it again.",
                                     self.open_tab(stale[0]))

    def stopped_job(self, job: DocumentaryJob, view: dict) -> None:
        portrait = "Awaiting portrait choice" in (job.error or "")
        awaiting = portrait or "Awaiting picture review" in (job.error or "")
        actions = []
        if portrait:
            actions.append(Action("Choose another first portrait", "href", f"/documentary/{job.case_id}/review?portraits=1"))
        elif awaiting:
            actions.append(Action("Review pictures", "href", f"/documentary/{job.case_id}/review"))
        elif view["stage"]:
            actions.append(self.open_tab(view["stage"]))
        if J.job_dict(job)["resume_allowed"]:
            actions.append(Action("Resume the job", "resume", str(job.id)))
        failed = next((s for s in view["stages"] if s["status"] in ("failed", "blocked")), None)
        detail = job.error or "The job stopped."
        if failed and failed.get("error"):
            detail = f"{failed['name']}: {failed['error']}"
        self.raise_issue("attention" if awaiting else "problem", "job",
                         f"Job #{job.id} {'is waiting for you' if awaiting else job.status}: "
                         f"{(job.error or '')[:140] or 'no message'}", f"{detail}\n{view['next_action'] or ''}".strip(),
                         *actions)

    # -- gates the owner decides ------------------------------------------
    def check_gates(self, languages: list[str]) -> None:
        for language in languages:
            plan = self.latest_direction(language)
            gaps = ((plan or {}).get("plan") or {}).get("gaps") or []
            if gaps:
                seconds = round(sum(g["end"] - g["start"] for g in gaps), 1)
                self.raise_issue(
                    "attention", "pictures",
                    f"{len(gaps)} moment(s) of the {language} film have nothing to show ({seconds} s)",
                    "Nothing is rendered until every moment has a picture, a video or a graphic. Approve pictures "
                    "the planner rejected or was unsure about, then resume.",
                    Action("Review pictures", "href", f"/documentary/{self.case.id}/review"))
        for review in self.db.query(MvpFilmReview).filter(
                MvpFilmReview.case_id == self.case.id, MvpFilmReview.state == "AWAITING_OWNER_REVIEW").all():
            self.raise_issue("attention", "review", f"The {review.language} film waits for your decision",
                             "A film is rendered and held at the owner-review gate; nothing else starts without it.",
                             Action("Open the Render tab", "tab", "Render"))

    def latest_direction(self, language: str) -> dict | None:
        for plan in self.db.query(VisualPlan).filter_by(case_id=self.case.id).order_by(VisualPlan.id.desc()).all():
            data = json.loads(plan.validation_json or "{}")
            if data.get("mvp", {}).get("language") == language and "plan" in data:
                return data
        return None

    # -- budget and providers ---------------------------------------------
    def budget(self) -> dict:
        snap = BudgetService(self.db).snapshot(self.case.id)
        used = snap.spent_eur / snap.cap_eur if snap.cap_eur else 0.0
        if snap.spent_eur >= snap.cap_eur:
            self.raise_issue("problem", "budget", "The cost cap of this case is reached",
                             f"EUR {snap.spent_eur:.2f} of EUR {snap.cap_eur:.2f} spent; paid calls stop cleanly.",
                             Action("Open the case costs", "href", f"/cases/{self.case.id}"))
        elif used >= self.cfg.budget_warn_fraction:
            self.raise_issue("attention", "budget", f"{used:.0%} of the cost cap is spent",
                             f"EUR {snap.spent_eur:.2f} of EUR {snap.cap_eur:.2f}.",
                             Action("Open the case costs", "href", f"/cases/{self.case.id}"))
        return {"cap_eur": snap.cap_eur, "spent_eur": snap.spent_eur, "remaining_eur": snap.remaining_eur,
                "used_fraction": round(used, 4), "status": snap.status}

    # -- the snapshot ----------------------------------------------------
    def collect(self) -> dict:
        """This case's findings (jobs, gates, cost) and the jobs it concerns; services and providers are shared."""
        own = (self.db.query(DocumentaryJob).filter(DocumentaryJob.case_id == self.case.id)
               .order_by(DocumentaryJob.id.desc()).limit(self.cfg.recent_jobs).all())
        active = self.db.query(DocumentaryJob).filter(DocumentaryJob.status.in_(ACTIVE)).order_by(
            DocumentaryJob.id).all()
        jobs = {j.id: j for j in [*own, *active]}.values()
        self.case_titles = {c.id: c.canonical_title for c in self.db.query(Case).filter(
            Case.id.in_({j.case_id for j in jobs})).all()}
        self.views = {j.id: self.job_view(j) for j in jobs}
        running = [j for j in active if j.status != "queued"]
        latest = own[0] if own else None
        # issues are this case's own: its active jobs and its latest job (older stopped jobs are history)
        self.check_jobs([j for j in jobs if j.case_id == self.case.id
                         and (j.status in ACTIVE or (latest and j.id == latest.id))])
        planning = any(stage_base(j.stage or "") in ("mvp_direct", "mvp_complete", "mvp_portraits")
                       for j in running if j.case_id == self.case.id)
        if not planning:      # while the picture plan is being redone the old gap list is out of date
            self.check_gates(sorted({lang for j in own[:1] for lang in json.loads(j.languages_json or "[]")}))
        return {"own": own, "active": active, "running": running, "latest": latest, "budget": self.budget()}

    def snapshot(self) -> dict:
        found = self.collect()
        running, latest = found["running"], found["latest"]
        providers = self.providers()
        services = self.services(running)
        issues = sorted(self.issues, key=lambda i: RANK[i.severity])
        return {"generated_at": utc_now(), "refresh_seconds": self.cfg.refresh_seconds,
                "state": overall(issues, running), "case": {"id": self.case.id, "title": self.case.canonical_title},
                "issues": [asdict(i) for i in issues],
                "running": [self.views[j.id] for j in running],
                "queued": [self.views[j.id] for j in found["active"] if j.status == "queued"],
                "latest_job": self.views[latest.id] if latest else None,
                "recent_jobs": [{k: self.views[j.id][k] for k in ("id", "status", "stage", "progress")}
                                | {"created_at": j.created_at} for j in found["own"]],
                "services": services, "budget": found["budget"], "providers": providers}


class AppMonitor(Monitor):
    """The whole app: every active job and every case whose newest job stopped lately, with one list of findings.
    Findings that belong to a case say which; a button that opens a workspace tab opens it on that case."""

    def snapshot(self) -> dict:
        active = self.db.query(DocumentaryJob).filter(DocumentaryJob.status.in_(ACTIVE)).order_by(
            DocumentaryJob.id).all()
        since = utc_now() - timedelta(days=self.cfg.recent_days)
        newest: dict[int, DocumentaryJob] = {}
        for job in self.db.query(DocumentaryJob).filter(DocumentaryJob.updated_at >= since).order_by(
                DocumentaryJob.id.desc()).all():
            newest.setdefault(job.case_id, job)
        case_ids = sorted({j.case_id for j in active} | {c for c, j in newest.items() if j.status in STOPPED})
        budgets, cases = [], []
        for case_id in case_ids:
            case = CaseMonitor(self.db, case_id)
            found = case.collect()
            self.views.update(case.views)
            self.case_titles.update(case.case_titles)
            self.issues.extend(self.localized(i, case.case) for i in case.issues)
            budgets.append({"case_id": case_id, "title": case.case.canonical_title, **found["budget"]})
            cases.append({"id": case_id, "title": case.case.canonical_title,
                          "job": (self.views.get(newest[case_id].id) if case_id in newest else None)})
        running = [j for j in active if j.status != "queued"]
        providers = self.providers()
        services = self.services(running)
        issues = sorted(self.issues, key=lambda i: RANK[i.severity])
        return {"generated_at": utc_now(), "refresh_seconds": self.cfg.refresh_seconds,
                "state": overall(issues, running), "issues": [asdict(i) for i in issues],
                "running": [self.views[j.id] for j in running],
                "queued": [self.views[j.id] for j in active if j.status == "queued"],
                "cases": cases, "services": services, "budgets": budgets, "providers": providers}

    @staticmethod
    def localized(issue: Issue, case: Case) -> Issue:
        actions = [Action(a.label, "href", f"/documentary/{case.id}?tab={a.target}") if a.kind == "tab" else a
                   for a in issue.actions]
        return Issue(issue.severity, issue.area, f"{case.canonical_title}: {issue.title}", issue.detail, actions)


def overall(issues: list[Issue], running: list[DocumentaryJob]) -> str:
    if any(i.severity == "problem" for i in issues):
        return "problem"
    if any(i.severity == "attention" for i in issues):
        return "attention"
    return "running" if running else "idle"


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _counts(stages: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in stages:
        out[s["status"]] = out.get(s["status"], 0) + 1
    return out


@router.get("/api/cases/{case_id}/monitoring")
def case_monitoring(case_id: int, db: Session = Depends(get_db)):
    return CaseMonitor(db, case_id).snapshot()


@router.get("/api/monitoring")
def app_monitoring(db: Session = Depends(get_db)):
    return AppMonitor(db).snapshot()
