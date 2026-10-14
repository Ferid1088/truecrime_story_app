"""Safety-mode checks that never start the full local stack."""
from __future__ import annotations

import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from alembic import command

from app.db import migrate
from scripts import db_reconcile

ROOT = Path(__file__).resolve().parents[1]


def test_dev_up_first_run_safe_status_prints_both_guards():
    result = subprocess.run(
        [str(ROOT / "scripts" / "dev_up.sh"), "--first-run-safe", "--status"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    assert "TRUECRIME_JOB_MAX_AUTO_RESUMES=0" in result.stdout
    assert "TRUECRIME_DISABLE_SCHEDULER=1" in result.stdout


def test_first_run_safe_worker_makes_no_calls_or_job_changes_for_one_minute(tmp_path, monkeypatch):
    database = tmp_path / "safe-start.db"
    command.upgrade(migrate.alembic_config(f"sqlite:///{database}"), "head")
    monkeypatch.setenv("TRUECRIME_SAFE_START_SECONDS", "60")
    before = db_reconcile.job_snapshot(database)

    db_reconcile.safe_start(database)

    assert db_reconcile.job_snapshot(database) == before
    assert before["provider_calls"] == 0
    assert os.environ["TRUECRIME_SAFE_START_SECONDS"] == "60"
