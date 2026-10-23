"""Case controls: pause / resume / stop (keep | delete) / restore / global stop.

States live on ``cases.control_state`` so a pause survives a restart:

* ``None``/``"running"`` — normal
* ``"pausing"`` — asked to pause; running jobs stop at their next step boundary,
  queued jobs are held (never claimed)
* ``"paused"`` — nothing of this case runs
* ``"stopped"`` — stopped, data kept, resumable
* ``"trashed"`` — soft-delete: ``purge_after`` hours in the trash, then the
  cleanup pass removes rows and media. Ledger rows, tombstone and audit events
  are kept forever.

Per-language pause uses ``paused_languages_json``: the job stops at the next
boundary belonging to a paused language (a documentary job is sequential — other
languages already past that point continue on resume; documented limitation).
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db.models import (
    AutopilotRun,
    Case,
    CaseEvent,
    CaseTombstone,
    DocumentaryJob,
    ProviderCall,
)
from app.utils import ensure_utc, utc_now

log = logging.getLogger(__name__)

PAUSING, PAUSED, STOPPED, TRASHED = "pausing", "paused", "stopped", "trashed"
HELD_STATES = {PAUSING, PAUSED, STOPPED, TRASHED}
GLOBAL_STOP_KEY = "control:global_stop"
TRASH_RETENTION = timedelta(hours=24)

ROOT = Path(__file__).resolve().parents[2]


class ControlError(ValueError):
    """A control request that conflicts with the case's current state."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- helpers
def _audit(db: Session, *, case: Case | None, action: str, actor: str,
           reason: str | None = None, stage: str | None = None,
           before: str | None = None, after: str | None = None,
           detail: dict | None = None,
           case_id: int | None = None, case_name: str | None = None) -> CaseEvent:
    row = CaseEvent(
        case_id=case.id if case else case_id,
        case_name=case.canonical_title if case else case_name,
        action=action, actor=actor or "owner", stage=stage, reason=reason,
        state_before=before, state_after=after,
        cost_eur_at_event=case_cost_eur(db, case.id) if case else None,
        detail_json=json.dumps(detail or {}, ensure_ascii=False),
        created_at=utc_now())
    db.add(row)
    return row


def case_cost_eur(db: Session, case_id: int) -> float:
    return float(db.query(func.coalesce(func.sum(ProviderCall.cost_eur), 0.0))
                 .filter(ProviderCall.case_id == case_id,
                         ProviderCall.is_test_data.is_(False)).scalar() or 0.0)


def paused_languages(case: Case) -> list[str]:
    try:
        return list(json.loads(case.paused_languages_json or "[]"))
    except (TypeError, ValueError):
        return []


def _case_jobs(db: Session, case_id: int) -> list[DocumentaryJob]:
    return db.query(DocumentaryJob).filter(DocumentaryJob.case_id == case_id).all()


def _running_jobs(db: Session, case_id: int) -> list[DocumentaryJob]:
    return [j for j in _case_jobs(db, case_id) if j.status in ("running", "cancelling")]


def effective_state(db: Session, case: Case) -> str:
    """The control state with the pausing→paused flip applied: pausing becomes paused as
    soon as nothing of the case still runs. Persisted lazily."""
    state = case.control_state or "running"
    if state == PAUSING and not _running_jobs(db, case.id):
        case.control_state = PAUSED
        db.commit()
        return PAUSED
    return state


def held_case_ids(db: Session) -> set[int]:
    """Cases whose jobs must not be claimed (paused/stopped/trashed)."""
    return {cid for (cid,) in db.query(Case.id)
            .filter(Case.control_state.in_(HELD_STATES)).all()}


def case_held(db: Session, case_id: int) -> bool:
    state = (db.query(Case.control_state).filter(Case.id == case_id).scalar())
    return state in HELD_STATES


# --------------------------------------------------------------------------- global stop
def global_stop_status(db: Session) -> dict:
    from app.db.models import AppSetting

    row = db.get(AppSetting, GLOBAL_STOP_KEY)
    try:
        payload = json.loads(row.value_json) if row else {}
    except (TypeError, ValueError):
        payload = {}
    return {"active": bool(payload.get("active")), "at": payload.get("at"),
            "by": payload.get("by"), "stopped": payload.get("stopped", {})}


def global_stop_active(db: Session) -> bool:
    return bool(global_stop_status(db)["active"])


def set_global_stop(db: Session, active: bool, actor: str) -> dict:
    """The emergency stop: nothing that can spend money may be claimed while active.
    Autopilot runs are cancelled (the owner can start a new one); queued and running
    jobs stay held by the claim filter and pause at their next boundary."""
    from app.db.models import AppSetting

    row = db.get(AppSetting, GLOBAL_STOP_KEY) or AppSetting(key=GLOBAL_STOP_KEY)
    payload = {"active": active, "at": utc_now().isoformat(), "by": actor}
    if active:
        stopped = {
            "queued_jobs": db.query(DocumentaryJob)
            .filter(DocumentaryJob.status == "queued").count(),
            "running_jobs": db.query(DocumentaryJob)
            .filter(DocumentaryJob.status.in_(("running", "cancelling"))).count(),
            "autopilot_runs": db.query(AutopilotRun)
            .filter(AutopilotRun.status == "running").count(),
        }
        for run in db.query(AutopilotRun).filter(AutopilotRun.status == "running").all():
            run.status = "cancelled"
            run.error = "global emergency stop"
            run.updated_at = utc_now()
        payload["stopped"] = stopped
        _audit(db, case=None, action="global_stop", actor=actor, after="active",
               detail=stopped)
    else:
        _audit(db, case=None, action="global_stop_release", actor=actor, after="inactive")
    row.value_json = json.dumps(payload, ensure_ascii=False)
    db.add(row)
    db.commit()
    return payload


# --------------------------------------------------------------------------- pause / resume
def pause_case(db: Session, case: Case, actor: str, languages: list[str] | None = None,
               reason: str | None = None) -> dict:
    if case.control_state == TRASHED:
        raise ControlError("case_trashed", "the case is in the trash")
    if case.control_state == STOPPED:
        raise ControlError("case_stopped", "the case is stopped; resume it first")
    before = effective_state(db, case)
    if languages:
        # per-language hold: the case keeps running; stages of these languages pause at
        # their next boundary (a job is sequential — stages of other languages already
        # queued after a paused one wait behind it; see module docstring)
        langs = sorted(set(paused_languages(case)) | set(languages))
        case.paused_languages_json = json.dumps(langs)
        _audit(db, case=case, action="pause_languages", actor=actor, reason=reason,
               before=before, after=case.control_state or "running",
               detail={"languages": langs})
        db.commit()
        return _view(db, case, before, case.control_state or "running")
    if before in (PAUSING, PAUSED):
        return _view(db, case, before, case.control_state or "running")
    case.control_state = PAUSING
    _audit(db, case=case, action="pause", actor=actor, reason=reason,
           before=before, after=PAUSING)
    db.commit()
    return _view(db, case, before, PAUSING)


def resume_case(db: Session, case: Case, actor: str) -> dict:
    before = effective_state(db, case)
    held_langs = paused_languages(case)
    if before == TRASHED:
        raise ControlError("case_trashed", "the case is in the trash; restore it first")
    if before not in (PAUSING, PAUSED, STOPPED) and not held_langs:
        return _view(db, case, before, before)      # idempotent no-op
    # Paused jobs go back to the queue unchanged (finished stages are already skipped by
    # the runner); cancelled/interrupted jobs get the resume reset the job endpoint does.
    requeued, resumed_ids = 0, []
    for job in _case_jobs(db, case.id):
        if job.status == PAUSED:
            job.status, job.worker_id, job.heartbeat_at = "queued", None, None
            requeued += 1
            resumed_ids.append(job.id)
        elif job.status in ("cancelled", "interrupted"):
            stages = json.loads(job.stages_json or "[]")
            for s in stages:
                if s["status"] in ("failed", "running", "blocked"):
                    s["status"] = "pending"
            job.stages_json = json.dumps(stages)
            job.status, job.worker_id, job.heartbeat_at = "queued", None, None
            job.auto_resumes = 0
            requeued += 1
            resumed_ids.append(job.id)
        elif job.status == "queued" and case.control_state == STOPPED:
            resumed_ids.append(job.id)
    for run in db.query(AutopilotRun).filter(
            AutopilotRun.case_id == case.id, AutopilotRun.status == "paused").all():
        run.status = "running"
        run.updated_at = utc_now()
    case.control_state = None
    case.paused_languages_json = "[]"
    _audit(db, case=case, action="resume", actor=actor,
           before=before, after="running", detail={"jobs": resumed_ids})
    db.commit()
    return _view(db, case, before, "running", requeued=requeued,
                 resumed_jobs=resumed_ids)


# --------------------------------------------------------------------------- stop
def trash_preview(db: Session, case: Case) -> dict:
    """What 'stop and delete' would remove — shown in the dialog before the typed-name
    confirmation: files, size, DB rows, euros spent so far."""
    files, size = 0, 0
    for path in _case_media_paths(case.id):
        if path.is_dir():
            for p in path.rglob("*"):
                if p.is_file():
                    files += 1
                    size += p.stat().st_size
        elif path.is_file():
            files += 1
            size += path.stat().st_size
    rows = 0
    for name, col in _scoped_tables().items():
        if col is not None:
            rows += db.execute(text(f"SELECT count(*) FROM {name} WHERE case_id = :cid"),
                               {"cid": case.id}).scalar() or 0
    return {"files": files, "bytes": size, "db_rows": rows,
            "cost_eur": case_cost_eur(db, case.id)}


def _case_media_paths(case_id: int) -> list[Path]:
    return [
        ROOT / "data" / "cases" / str(case_id),
        ROOT / "data" / "maps" / str(case_id),
        ROOT / "data" / "outfit_uploads" / f"case_{case_id}",
    ] + list((ROOT / "data" / "research_snapshots").glob(f"snap-case{case_id}-*.json"))


def stop_case(db: Session, case: Case, mode: str, actor: str,
              reason: str | None = None, confirm_name: str | None = None) -> dict:
    before = effective_state(db, case)
    if before == TRASHED:
        raise ControlError("case_trashed", "the case is already in the trash")
    if mode not in ("keep", "delete"):
        raise ControlError("bad_mode", "stop mode must be 'keep' or 'delete'")
    if mode == "delete" and (confirm_name or "").strip() != case.canonical_title:
        raise ControlError("confirm_name",
                           "type the case name exactly to delete it")
    # halt every queued and running job: cancelling lets in-flight paid calls finish,
    # queued ones never start, paused ones close at once
    halted: list[str] = []
    for job in _case_jobs(db, case.id):
        if job.status in ("queued", "running"):
            job.status = "cancelling"
            halted.append(f"job:{job.id}")
        elif job.status in (PAUSING, PAUSED):
            job.status = "cancelled"
            job.worker_id = None
            job.completed_at = job.updated_at = utc_now()
            halted.append(f"job:{job.id}")
    for run in db.query(AutopilotRun).filter(
            AutopilotRun.case_id == case.id,
            AutopilotRun.status.in_(("running", "paused", "waiting_owner"))).all():
        run.status = "cancelled"
        run.updated_at = utc_now()
        halted.append(f"autopilot:{run.id}")
    detail = {"halted": halted}
    if mode == "keep":
        case.control_state = STOPPED
        _audit(db, case=case, action="stop_keep", actor=actor, reason=reason,
               before=before, after=STOPPED, detail=detail)
    else:
        now = utc_now()
        case.control_state = TRASHED
        case.trashed_at = now
        case.purge_after = now + TRASH_RETENTION
        preview = trash_preview(db, case)
        db.add(CaseTombstone(
            case_id=case.id, name=case.canonical_title, slug=case.slug, who=actor,
            total_cost_eur=preview["cost_eur"], deleted_at=now,
            restore_until=case.purge_after))
        db.execute(text("UPDATE provider_calls SET case_deleted = true WHERE case_id = :cid"),
                   {"cid": case.id})
        detail.update(preview)
        _audit(db, case=case, action="stop_delete", actor=actor, reason=reason,
               before=before, after=TRASHED, detail=detail)
    db.commit()
    return _view(db, case, before, case.control_state or "running")


def restore_case(db: Session, case: Case, actor: str) -> dict:
    if case.control_state != TRASHED or case.trashed_at is None:
        raise ControlError("not_trashed", "only a case in the trash can be restored")
    purge_at = ensure_utc(case.purge_after)
    if purge_at is not None and utc_now() > purge_at:
        raise ControlError("restore_expired", "the 24h restore window has passed")
    before = TRASHED
    case.control_state = STOPPED
    case.trashed_at = None
    case.purge_after = None
    tomb = (db.query(CaseTombstone)
            .filter_by(case_id=case.id, purged_at=None, restored_at=None)
            .order_by(CaseTombstone.id.desc()).first())
    if tomb is not None:
        tomb.restored_at = utc_now()
    _audit(db, case=case, action="restore", actor=actor, before=before, after=STOPPED)
    db.commit()
    return _view(db, case, before, STOPPED)


# --------------------------------------------------------------------------- purge
def _scoped_tables() -> dict[str, str | None]:
    """Case-data tables: every table owning a ``case_id`` column plus children only
    reachable through one. Tables that must survive a case delete are excluded:
    the cost ledger, the audit trail, tombstones, settings and reference data."""
    from app.db.models import Base

    keep = {"cases", "provider_calls", "case_events", "case_tombstones",
            "alembic_version", "app_settings", "channel_settings", "backup_runs",
            "monitor_runs", "extraction_cache", "closing_clips", "shared_reel_assets",
            "host_looks", "host_led_music_tracks", "music_tracks"}
    out: dict[str, str | None] = {}
    for t in Base.metadata.tables.values():
        if t.name in keep:
            continue
        if "case_id" in t.c:
            out[t.name] = "case_id"
    # children without case_id: nearest FK parent chain into the scoped set
    changed = True
    while changed:
        changed = False
        for t in Base.metadata.tables.values():
            if t.name in keep or t.name in out:
                continue
            for fk in t.foreign_keys:
                if fk.column.table.name in out:
                    out[t.name] = None
                    changed = True
                    break
    return out


def _row_filter(name: str, paths: dict[str, list[tuple[str, str]]],
                scoped: dict[str, str | None]) -> str:
    """A WHERE clause matching this scoped table's rows of the case: ``case_id = :cid``
    for own-keyed tables, an FK IN-chain for children without one."""
    if scoped.get(name) is not None:
        return "case_id = :cid"
    path = paths[name]
    last_parent = path[-1][1]
    inner = (f"SELECT id FROM {last_parent} WHERE "
             f"{'id' if last_parent == 'cases' else 'case_id'} = :cid")
    # path[j] = (f_j → P_{j+1}): P_{j+1} is referenced by node_j's f_j
    for j in range(len(path) - 1, 0, -1):
        fk_col, parent = path[j][0], path[j - 1][1]
        inner = f"SELECT id FROM {parent} WHERE {fk_col} IN ({inner})"
    return f"{path[0][0]} IN ({inner})"


def _null_keep_fks(db: Session, case_id: int, scoped: dict[str, str | None]) -> None:
    """Keep-tables that point into case data get their FK nulled before parents go:
    host_looks.host_outfit_id is the live one today; the loop covers any future one."""
    from app.db.models import Base

    paths = _child_paths()
    scoped_names = set(scoped)
    for t in Base.metadata.tables.values():
        if t.name in scoped_names or t.name == "cases":
            continue
        for fk in t.foreign_keys:
            parent = fk.column.table
            if parent.name in scoped_names:
                stmt = (t.update()
                        .where(t.c[fk.parent.name].in_(text(
                            f"(SELECT id FROM {parent.name} WHERE "
                            f"{_row_filter(parent.name, paths, scoped)})")))
                        .values(**{fk.parent.name: None}))
                db.execute(stmt, {"cid": case_id})


def _child_paths() -> dict[str, list[tuple[str, str]]]:
    """For each scoped table without a case_id column: one FK path to the nearest
    case-owning table, as ``[(fk_column, parent_table), …]`` hops where the last
    parent has a ``case_id`` column or is ``cases`` itself."""
    from app.db.models import Base

    scoped = _scoped_tables()
    has_cid = {n for n, c in scoped.items() if c is not None}
    paths: dict[str, list[tuple[str, str]]] = {}
    for name in scoped:
        if scoped[name] is not None:
            continue
        frontier: list[tuple[str, list[tuple[str, str]]]] = [(name, [])]
        seen = {name}
        found = None
        while frontier and found is None:
            current, path = frontier.pop(0)
            for fk in Base.metadata.tables[current].foreign_keys:
                ptable = fk.column.table.name
                step = path + [(fk.parent.name, ptable)]
                if ptable == "cases" or ptable in has_cid:
                    found = step
                    break
                if ptable in scoped and ptable not in seen:
                    seen.add(ptable)
                    frontier.append((ptable, step))
        if found is None:
            raise RuntimeError(f"no FK path to a case table for scoped table {name!r}")
        paths[name] = found
    return paths


def delete_case_rows(db: Session, case_id: int) -> int:
    """Every row of this case, deepest children first. The table set and order come from
    the SQLAlchemy FK graph, so a new case-scoped table needs no change here. Kept
    forever: provider_calls (marked case_deleted), case_events, tombstones."""
    scoped = _scoped_tables()
    paths = _child_paths()
    _null_keep_fks(db, case_id, scoped)
    total = 0
    # one pass, deepest FK-depth first — children (keyed or not) always precede parents
    depth = _depths(scoped)
    for name in sorted(scoped, key=lambda n: -depth.get(n, 0)):
        total += int(db.execute(
            text(f"DELETE FROM {name} WHERE {_row_filter(name, paths, scoped)}"),
            {"cid": case_id}).rowcount or 0)  # type: ignore[attr-defined]
    # polymorphic case-scoped logs and settings keys
    for table in ("edit_log", "override_log"):
        total += int(db.execute(text(
            f"DELETE FROM {table} WHERE scope_type IN "
            "('case','case_action','story_version','short_form','documentary_job','autopilot_run')"
            " AND (scope_id = :cid OR scope_id = :cidstr)"),
            {"cid": case_id, "cidstr": str(case_id)}).rowcount or 0)  # type: ignore[attr-defined]
    total += int(db.execute(text(
        "DELETE FROM app_settings WHERE key LIKE :pat"),
        {"pat": f"%:{case_id}:%"}).rowcount or 0)  # type: ignore[attr-defined]
    db.commit()
    return total


def _depths(scoped: dict[str, str | None]) -> dict[str, int]:
    from app.db.models import Base

    names = set(scoped) | {"cases"}
    edges: dict[str, list[str]] = {n: [] for n in names}
    for n in names:
        for fk in Base.metadata.tables[n].foreign_keys:
            if fk.column.table.name in names:
                edges[n].append(fk.column.table.name)
    memo: dict[str, int] = {}

    def depth(name: str, trail: frozenset[str]) -> int:
        if name in memo:
            return memo[name]
        if name in trail:
            return 0
        d = 1 + max((depth(p, trail | {name}) for p in edges.get(name, ())), default=0)
        memo[name] = d
        return d

    return {n: depth(n, frozenset()) for n in names}


def purge_expired_trash(db: Session) -> dict:
    """The cleanup job: cases whose 24h in the trash are over lose their rows and media.
    Called by the worker tick; safe to run any time."""
    from app.db.models import Base  # noqa: F401 — populate metadata

    now = utc_now()
    expired = db.query(Case).filter(
        Case.control_state == TRASHED, Case.purge_after.isnot(None),
        Case.purge_after <= now).all()
    out: dict = {"purged": [], "rows": 0, "files": 0}
    for case in expired:
        cid, name = case.id, case.canonical_title
        rows = delete_case_rows(db, cid)
        files = 0
        for path in _case_media_paths(cid):
            if path.is_dir():
                files += sum(1 for p in path.rglob("*") if p.is_file())
                shutil.rmtree(path, ignore_errors=True)
            elif path.is_file():
                files += 1
                path.unlink(missing_ok=True)
        db.delete(case)
        tomb = (db.query(CaseTombstone)
                .filter_by(case_id=cid, purged_at=None).order_by(CaseTombstone.id.desc())
                .first())
        if tomb is not None:
            tomb.purged_at = utc_now()
        _audit(db, case=None, action="purge", actor="system",
               case_id=cid, case_name=name,
               detail={"rows": rows, "files": files})
        db.commit()
        out["purged"].append({"case_id": cid, "name": name, "rows": rows, "files": files})
        out["rows"] += rows
        out["files"] += files
        log.warning("purged trashed case %s (%s): %s rows, %s files", cid, name, rows, files)
    return out


def _view(db: Session, case: Case, before: str, after: str, **extra) -> dict:
    return {"case_id": case.id, "state_before": before, "state": after,
            "paused_languages": paused_languages(case),
            "trashed_at": case.trashed_at, "purge_after": case.purge_after,
            **extra}


def control_view(db: Session, case: Case) -> dict:
    """What the UI shows: resolved state, pause languages, trash info, audit-safe
    preview for the stop dialog."""
    state = effective_state(db, case)
    running = [j.id for j in _case_jobs(db, case.id) if j.status in ("running", "cancelling")]
    paused_jobs = [j.id for j in _case_jobs(db, case.id) if j.status == PAUSED]
    from app.core.ai_config import ai_config

    view = {"case_id": case.id, "state": state, "pausing_step": bool(running),
            "running_jobs": running, "paused_jobs": paused_jobs,
            "languages": list(ai_config.channels),
            "paused_languages": paused_languages(case),
            "trashed_at": case.trashed_at, "purge_after": case.purge_after,
            "restorable": bool(state == TRASHED and case.purge_after is not None
                               and ensure_utc(case.purge_after) is not None
                               and utc_now() <= (ensure_utc(case.purge_after)
                                                 or utc_now())),
            "cost_eur": case_cost_eur(db, case.id)}
    if state == TRASHED:
        view["preview"] = None
    return view
