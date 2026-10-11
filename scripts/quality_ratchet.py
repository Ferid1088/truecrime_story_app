#!/usr/bin/env python3
"""Fail quality gates only when Ruff or Mypy introduces a new finding.

The baseline intentionally stores finding fingerprints rather than counts: fixing
an old issue is allowed, while replacing it with a different issue still fails.
Use ``--update`` only when deliberately reviewing a baseline change.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "config" / "quality_baseline.json"
_MYPY = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+): error: (?P<message>.*)$")


def _run(command: list[str]) -> tuple[int, str]:
    proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def ruff_findings() -> list[str]:
    _, output = _run([sys.executable, "-m", "ruff", "check", "app", "scripts", "tests",
                      "--output-format", "json"])
    try:
        rows = json.loads(output or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ruff did not return JSON findings: {output[:500]}") from exc
    return sorted(
        f"{row['filename']}:{row['location']['row']}:{row['code']}:{row['message']}"
        for row in rows
    )


def mypy_findings() -> list[str]:
    _, output = _run([sys.executable, "-m", "mypy", "app"])
    findings = []
    for line in output.splitlines():
        match = _MYPY.match(line)
        if match:
            findings.append(
                f"{match['file']}:{match['line']}:{match['message']}"
            )
    return sorted(findings)


def read_baseline() -> dict[str, list[str]]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tool", choices=("ruff", "mypy", "all"), default="all", nargs="?")
    parser.add_argument("--update", action="store_true",
                        help="replace the stored baseline with current findings")
    args = parser.parse_args()
    current = {
        "ruff": ruff_findings() if args.tool in ("ruff", "all") else [],
        "mypy": mypy_findings() if args.tool in ("mypy", "all") else [],
    }
    if args.update:
        baseline = read_baseline() if BASELINE.exists() else {"ruff": [], "mypy": []}
        for tool, findings in current.items():
            if args.tool == "all" or args.tool == tool:
                baseline[tool] = findings
        BASELINE.write_text(json.dumps(baseline, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        print(f"updated {BASELINE.relative_to(ROOT)}")
        return 0

    baseline = read_baseline()
    new = {tool: sorted(set(findings) - set(baseline.get(tool, [])))
           for tool, findings in current.items() if args.tool in ("all", tool)}
    for tool, findings in new.items():
        print(f"{tool}: {len(current[tool])} current, {len(findings)} new")
        for finding in findings:
            print(f"  NEW {finding}")
    return 1 if any(new.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
