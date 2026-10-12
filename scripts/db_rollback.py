#!/usr/bin/env python3
"""Restore the local SQLite database from a verified backup."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from scripts import db_reconcile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup_file", type=Path)
    parser.add_argument("--target", type=Path,
                        default=Path(__file__).resolve().parents[1] / "truecrime.db")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not args.backup_file.is_file():
        parser.error(f"backup file does not exist: {args.backup_file}")
    subprocess.run([str(root / "scripts" / "dev_down.sh")], cwd=root, check=True)
    db_reconcile.ensure_live_quiescent(root, args.target)
    db_reconcile.restore_database(args.backup_file.resolve(), args.target.resolve())
    print(f"Rollback complete: restored {args.target.resolve()} from {args.backup_file.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
