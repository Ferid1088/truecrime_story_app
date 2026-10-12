"""Run the bounded 9K real-pipeline skip proof on disposable copies.

The first child builds one four-language job from the real research stage. All
later children restore the same persistent DB/output template, so every mode
comparison is a rerun of the saved job rather than a new synthetic fixture.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from soundness_template import ROOT, ensure_template, restore_template


def _env(**values: str) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("TC_TEST_DATABASE_URL", "TRUECRIME_DATABASE_URL", "DATABASE_URL"):
        env.pop(key, None)
    env.update({key: str(value) for key, value in values.items()})
    env["PYTHONPATH"] = f"{ROOT}:{ROOT / 'tests'}"
    env["SOUNDNESS_FROM_ZERO"] = "1"
    env["SOUNDNESS_CHAPTER_PAYLOAD"] = "1"
    env["SOUNDNESS_CHILD"] = "1"
    return env


def _run(command: list[str], env: dict[str, str], log: Path) -> float:
    started = time.perf_counter()
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"child failed ({result.returncode}); evidence: {log}")
    return elapsed


def _event_table(result: dict) -> dict[str, dict[str, int]]:
    table: dict[str, dict[str, int]] = {}
    initial_decision_seen: set[str] = set()
    for event in result["events"]:
        stage = event["stage"] or "<unknown>"
        row = table.setdefault(stage, {key: 0 for key in (
            "would_skip", "would_run", "match", "mismatch"
        )})
        decision = event["decision"]
        if event["rule_id"] in {"INCREMENTAL_SKIP", "INCREMENTAL_RUN"}:
            if stage in initial_decision_seen:
                continue
            initial_decision_seen.add(stage)
        if decision == "skip":
            row["would_skip"] += 1
        elif decision == "run":
            row["would_run"] += 1
        elif decision == "verify_match":
            row["match"] += 1
        elif decision == "verify_mismatch":
            row["mismatch"] += 1
    return table


def _restore(snapshot_template: Path, parent: Path, name: str) -> Path:
    run = parent / name
    restore_template(snapshot_template, run)
    return run


def _child(run: Path, *, mode: str, scenario: str, force: bool = False,
           gate: str | None = None, api: bool = False) -> tuple[dict, float]:
    output = run / "result.json"
    env = _env(
        TC_COPY_DATABASE_URL=f"sqlite:///{run / 'pipeline.db'}",
        SOUNDNESS_RUN_ROOT=run / "files",
        SOUNDNESS_PAYLOAD_LOG=run / "requests.jsonl",
        K_OUTPUT=output,
        K_MODE=mode,
        K_SCENARIO=scenario,
        K_FORCE="1" if force else "0",
        K_API="1" if api else "0",
    )
    if gate:
        env["K_GATE"] = gate
    elapsed = _run(
        [sys.executable, "-m", "pytest", "-q", "-s",
         "tests/test_9k_skip_rerun.py::test_9k_rerun_real_job"],
        env,
        run / "run.log",
    )
    return json.loads(output.read_text()), elapsed


def main() -> None:
    template, template_timing = ensure_template(ROOT / "data/dev/soundness/template")
    parent = Path(tempfile.mkdtemp(prefix="tc-9k-skip-proof-")).resolve()

    # Keep the persistent template as the source of the media library, but use
    # a fresh DB and fresh output root for the real from-research first run.
    baseline_db = parent / "baseline.db"
    baseline_files = parent / "baseline-files"
    baseline_files.mkdir()
    if (template / "files" / "lib").exists():
        shutil.copytree(template / "files" / "lib", baseline_files / "lib")
    initial_env = _env(
        TC_COPY_DATABASE_URL=f"sqlite:///{baseline_db}",
        SOUNDNESS_RUN_ROOT=baseline_files,
        SOUNDNESS_CAPTURE=parent / "baseline-snapshot.json",
        SOUNDNESS_PAYLOAD_LOG=parent / "baseline-requests.jsonl",
        SOUNDNESS_SNAPSHOT_VALUES=parent / "baseline-values.json",
        SOUNDNESS_SEED_MASTER="1",
    )
    initial_seconds = _run(
        [sys.executable, "-m", "pytest", "-q", "-s",
         "tests/test_visual_production.py::test_four_language_shadow_pipeline_real_job"],
        initial_env,
        parent / "initial.log",
    )
    # The child owns the SQLite connection; make the persistent rerun source a
    # complete main file before copying it into independent scenarios.
    with sqlite3.connect(baseline_db) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # Build a restorable snapshot template from that completed real job.  The
    # manifest is intentionally minimal; restore_template only needs the
    # backend and source root for the disposable SQLite clone.
    snapshot_template = parent / "rerun-template"
    snapshot_template.mkdir()
    shutil.copy2(baseline_db, snapshot_template / "pipeline.db")
    shutil.copytree(baseline_files, snapshot_template / "files")
    (snapshot_template / "manifest.json").write_text(json.dumps({
        "identity": {"backend": "sqlite"},
        "source_files_root": str(baseline_files.resolve()),
    }, indent=2) + "\n")

    verify: dict[str, dict] = {}
    enforce: dict[str, dict] = {}
    forced: dict[str, dict] = {}
    scenarios = ("unchanged", "naming", "spoken_de", "one_voice_take")
    for scenario in scenarios:
        run = _restore(snapshot_template, parent, f"verify-{scenario}")
        result, elapsed = _child(run, mode="verify", scenario=scenario)
        result["subprocess_wall_clock_seconds"] = round(elapsed, 3)
        result["table"] = _event_table(result)
        verify[scenario] = result
        assert not any(row["mismatch"] for row in result["table"].values()), result

    for scenario in ("unchanged", "naming", "spoken_de"):
        run = _restore(snapshot_template, parent, f"enforce-{scenario}")
        result, elapsed = _child(
            run, mode="enforce", scenario=scenario, api=scenario == "unchanged"
        )
        result["subprocess_wall_clock_seconds"] = round(elapsed, 3)
        enforce[scenario] = result

        forced_run = _restore(snapshot_template, parent, f"forced-{scenario}")
        forced_result, forced_elapsed = _child(
            forced_run, mode="off", scenario=scenario, force=True
        )
        forced_result["subprocess_wall_clock_seconds"] = round(forced_elapsed, 3)
        forced[scenario] = forced_result
        if result["after_snapshot"] != forced_result["after_snapshot"]:
            raise RuntimeError(
                f"STOP: enforce/full snapshot mismatch for {scenario}; "
                f"evidence {run} and {forced_run}"
            )

    gates = {}
    for gate in ("out_of_band", "pending_edit"):
        run = _restore(snapshot_template, parent, f"gate-{gate}")
        result, elapsed = _child(run, mode="enforce", scenario="unchanged", gate=gate)
        result["subprocess_wall_clock_seconds"] = round(elapsed, 3)
        gates[gate] = result

    report = {
        "template": template_timing,
        "evidence_root": str(parent),
        "initial": {
            "subprocess_wall_clock_seconds": round(initial_seconds, 3),
            "snapshot": json.loads((parent / "baseline-snapshot.json").read_text()),
        },
        "verify": verify,
        "enforce": enforce,
        "forced": forced,
        "gates": gates,
    }
    (parent / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("NINE_K_SKIP_REPORT", json.dumps({
        "evidence_root": str(parent),
        "initial_seconds": report["initial"]["subprocess_wall_clock_seconds"],
        "verify_mismatch": {
            scenario: sum(row["mismatch"] for row in result["table"].values())
            for scenario, result in verify.items()
        },
        "enforce": {
            scenario: {
                "executed": sorted(name for name, decision in
                                    result["policy_stage_decisions"].items()
                                    if decision == "run"),
                "reused": sorted(name for name, decision in
                                  result["policy_stage_decisions"].items()
                                  if decision == "skip"),
                "provider_calls": result["provider_calls"],
                "wall_clock": result["subprocess_wall_clock_seconds"],
            }
            for scenario, result in enforce.items()
        },
        "provider_calls_saved": {
            scenario: {
                provider: forced[scenario]["provider_calls"].get(provider, 0)
                - enforce[scenario]["provider_calls"].get(provider, 0)
                for provider in sorted(set(forced[scenario]["provider_calls"])
                                       | set(enforce[scenario]["provider_calls"]))
            }
            for scenario in enforce
        },
        "byte_compare": {scenario: True for scenario in enforce},
        "gates": {
            gate: [event for event in result["events"]
                   if event["decision"] == "run"]
            for gate, result in gates.items()
        },
    }, sort_keys=True))


if __name__ == "__main__":
    main()
