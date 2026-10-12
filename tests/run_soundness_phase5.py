"""Run the remaining real-pipeline snapshot-diff perturbations."""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from soundness_replay import PHASE5_TYPES, prepare_phase5
from soundness_template import ROOT, ensure_template, restore_template

SOURCES = tuple(PHASE5_TYPES)


def run_case(template: Path, parent: Path, source: str) -> dict:
    run = parent / source
    timing = restore_template(template, run)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(f"sqlite:///{run / 'pipeline.db'}")
    with Session(engine) as db:
        evidence = prepare_phase5(db, run, source)
    engine.dispose()
    env = os.environ.copy()
    env.pop("TC_TEST_DATABASE_URL", None)
    env.update(
        {
            "TC_COPY_DATABASE_URL": f"sqlite:///{run / 'pipeline.db'}",
            "SOUNDNESS_CHILD": "1",
            "SOUNDNESS_PHASE5": "1",
            "SOUNDNESS_FROM_ZERO": "1",
            "SOUNDNESS_CHAPTER_PAYLOAD": "1",
            "SOUNDNESS_RUN_ROOT": str(run / "files"),
            "SOUNDNESS_REPLAY": str(run / "overlay.json"),
            "SOUNDNESS_OBSERVATION": str(run / "after.json"),
            "SOUNDNESS_CAPTURE": str(run / "snapshot.json"),
            "SOUNDNESS_PAYLOAD_LOG": str(run / "requests.jsonl"),
            "PYTHONPATH": str(ROOT),
        }
    )
    started = time.perf_counter()
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-s",
            "tests/test_visual_production.py::test_four_language_shadow_pipeline_real_job",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    (run / "run.log").write_text(child.stdout + child.stderr)
    if child.returncode:
        raise RuntimeError(f"{source}: replay failed; see {run / 'run.log'}")
    after = json.loads((run / "after.json").read_text())
    before = evidence["baseline"]
    actual = sorted(
        key
        for key in before["hashes"].keys() | after["hashes"].keys()
        if before["hashes"].get(key) != after["hashes"].get(key)
    )
    predicted = evidence["predicted"]
    result = {
        "source": source,
        "predicted": predicted,
        "actual": actual,
        "hidden": sorted(set(actual) - set(predicted)),
        "over_invalidation": sorted(set(predicted) - set(actual)),
        "seconds": round(time.perf_counter() - started, 3),
        "restore_seconds": timing["restore_seconds"],
        "executed_stages": after["executed_stages"],
        "applied": after["source_applications"],
        "source_changed": evidence["source_changed"],
        "evidence": str(run),
    }
    (run / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    return result


def main() -> None:
    started = time.perf_counter()
    os.environ["SOUNDNESS_FROM_ZERO"] = "1"
    template, template_timing = ensure_template(ROOT / "data/dev/soundness/template")
    parent = Path(tempfile.mkdtemp(prefix="tc-soundness-phase5-")).resolve()
    (parent / "owned-by-soundness").touch()
    print(f"EVIDENCE {parent}", flush=True)
    results = []
    # The runs have separate DBs and output roots. Limit concurrency so ffmpeg
    # and the four language branches remain deterministic and resource-safe.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(run_case, template, parent, source) for source in SOURCES]
        for future in futures:
            result = future.result()
            results.append(result)
            print(json.dumps(result, sort_keys=True), flush=True)
    report = {
        "template": template_timing,
        "manifest": json.loads((template / "manifest.json").read_text()),
        "results": sorted(results, key=lambda row: row["source"]),
        "seconds": round(time.perf_counter() - started, 3),
    }
    (parent / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    assert all(row.get("status", "passed") == "unproven" or not row["hidden"] for row in results)


if __name__ == "__main__":
    main()
