"""On-demand full-run proof. Leaves raw snapshots/logs and never promotes SOUND."""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from soundness_replay import no_effect_edges, prepare
from soundness_template import ROOT, ensure_template, restore_template
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def run_case(template, parent, source):
    run = parent / source
    timing = restore_template(template, run)
    engine = create_engine(f"sqlite:///{run / 'pipeline.db'}")
    with Session(engine) as db:
        evidence = prepare(db, run, source)
    engine.dispose()
    env = os.environ.copy()
    env.pop("TC_TEST_DATABASE_URL", None)
    env.update(
        {
            "TC_COPY_DATABASE_URL": f"sqlite:///{run / 'pipeline.db'}",
            "SOUNDNESS_CHAPTER_PAYLOAD": "1",
            "SOUNDNESS_CHILD": "1",
            "SOUNDNESS_FROM_ZERO": "1",
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
        raise RuntimeError(f"{source}: replay failed, see {run / 'run.log'}")
    after = json.loads((run / "after.json").read_text())
    before = evidence["baseline"]
    actual = sorted(
        key
        for key in before["hashes"].keys() | after["hashes"].keys()
        if before["hashes"].get(key) != after["hashes"].get(key)
    )
    predicted = evidence["predicted"]
    if evidence["source_changed"]:
        source_key = evidence["source_changed"][0]
        source_type, _, source_language = source_key.partition(":")
        no_effect = no_effect_edges(
            source_type, None if source_language == "shared" else source_language, actual,
            asset_used=source == "used_visual",
        )
    else:
        no_effect = []
    result = {
        "source": source,
        "predicted": predicted,
        "actual": actual,
        "hidden": sorted(set(actual) - set(predicted)),
        "over_invalidation": sorted(set(predicted) - set(actual)),
        "no_effect_edges": no_effect,
        "other_languages_changed": [
            k
            for k in actual
            if source == "spoken_de" and k.rsplit(":", 1)[1] not in ("de", "shared")
        ],
        "seconds": time.perf_counter() - started,
        "restore_seconds": timing["restore_seconds"],
        "executed_stages": after["executed_stages"],
        "source_changed": evidence["source_changed"],
        "evidence": str(run),
    }
    (run / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    return result


def main():
    started = time.perf_counter()
    os.environ["SOUNDNESS_FROM_ZERO"] = "1"
    template, timing = ensure_template(ROOT / "data/dev/soundness/template")
    parent = Path(tempfile.mkdtemp(prefix="tc-soundness-phase4-")).resolve()
    (parent / "owned-by-soundness").touch()
    print(f"EVIDENCE {parent}", flush=True)
    control = run_case(template, parent, "control")
    print(json.dumps(control, sort_keys=True), flush=True)
    assert control["actual"] == [], (
        "Unchanged forced replay is not identical; stop before perturbations"
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda source: run_case(template, parent, source),
                ("spoken_de", "naming", "used_visual"),
            )
        )
    report = {
        "manifest": json.loads((template / "manifest.json").read_text()),
        "control": control,
        "results": results,
        "seconds": time.perf_counter() - started,
        "template": timing,
    }
    (parent / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    assert all(
        not r["hidden"] and not r["other_languages_changed"]
        and all(edge["reason"].strip() for edge in r["no_effect_edges"])
        for r in results
    )


if __name__ == "__main__":
    main()
