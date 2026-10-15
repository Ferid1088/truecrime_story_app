"""Shared process-identity helpers: which commit is actually running.

The API can report its own commit directly (it is the process /health
runs in), but the worker is a separate process with no HTTP endpoint of
its own. It writes a small state file at startup; /health reads it so an
operator can see both processes' commits — and a mismatch — in one place
instead of having to separately inspect two running processes.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKER_HEALTH_PATH = ROOT / "data" / "dev" / "worker.health.json"


def commit_hash() -> str | None:
    """The commit this process is running, however it was started."""
    value = os.environ.get("TRUECRIME_PROCESS_COMMIT")
    if value and len(value) == 40:
        return value
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=2,
        )
        value = result.stdout.strip()
        return value if len(value) == 40 else None
    except Exception:
        return None


def write_worker_health(worker_id: str) -> None:
    """Called once, at worker startup: record which commit and pid are running."""
    WORKER_HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    from app.utils import utc_now

    WORKER_HEALTH_PATH.write_text(json.dumps({
        "worker_id": worker_id,
        "pid": os.getpid(),
        "commit": commit_hash(),
        "started_at": utc_now().isoformat(),
    }), encoding="utf-8")


def read_worker_health() -> dict | None:
    """What the worker last reported about itself, or None if it never has."""
    try:
        return json.loads(WORKER_HEALTH_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def pid_alive(pid: int | None) -> bool:
    """Whether *pid* names a live process (a stale health file outlives a
    crashed or killed worker — this stops it from being read as current)."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else
    return True
