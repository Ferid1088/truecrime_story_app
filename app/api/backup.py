"""Backup settings, "back up now", and the history."""
from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import backup as B
from app.db.base import SessionLocal, engine, get_db
from app.db.models import BackupRun
from app.utils import ensure_utc

router = APIRouter(prefix="/api/backup", tags=["backup"])


class SettingsBody(BaseModel):
    enabled: bool = False
    dir: str = ""
    every_hours: int = 24
    start_time: str = "02:00"
    keep_last: int = 14


class RunBody(BaseModel):
    dir: str | None = None


def _run_dict(r: BackupRun) -> dict:
    return {"id": r.id, "started_at": ensure_utc(r.started_at).isoformat(),
            "finished_at": ensure_utc(r.finished_at).isoformat() if r.finished_at else None,
            "trigger": r.trigger, "status": r.status, "dest_dir": r.dest_dir, "file_path": r.file_path,
            "size_bytes": r.size_bytes, "error": r.error}


@router.get("")
def overview(db: Session = Depends(get_db)):
    s = B.load_settings(db)
    runs = db.query(BackupRun).order_by(BackupRun.started_at.desc(), BackupRun.id.desc()).limit(10).all()
    last_ok = next((r for r in runs if r.status == "ok"), None)
    nxt = B._as_utc(B.next_slot(s)) if s.enabled and s.dir else None
    return {
        "settings": asdict(s),
        "database": engine.dialect.name,
        "script_backup_dir": str(B.script_backup_dir()),
        "folder_backups": B.list_script_backups(),
        "next_run_at": nxt.isoformat() if nxt else None,
        "running": any(r.status == "running" for r in runs),
        "last_ok_at": ensure_utc(last_ok.started_at).isoformat() if last_ok else None,
        "runs": [_run_dict(r) for r in runs],
    }


@router.put("/settings")
def update_settings(body: SettingsBody, db: Session = Depends(get_db)):
    try:
        s = B.validate(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    B.save_settings(db, s)
    return asdict(s)


@router.post("/run")
async def run_now(body: RunBody | None = None, db: Session = Depends(get_db)):
    """Back up now. A folder in the request is used and remembered as the new default."""
    folder = body.dir if body and body.dir is not None else None
    if folder is not None:
        s = B.load_settings(db)
        s.dir = folder.strip()
        B.save_settings(db, s)
    try:
        run = await asyncio.to_thread(B.run_backup, SessionLocal, engine, "manual", folder)
    except B.BackupError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _run_dict(run)
