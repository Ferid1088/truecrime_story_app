"""Generate the Phase 6 matrix and status from snapshot-diff records."""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.artifact_dependencies import (
    ARTIFACT_CONFIG_INPUTS,
    ARTIFACT_DEPENDENCIES,
    ARTIFACT_SCOPES,
    direct_dependents,
)

ROOT = Path(__file__).resolve().parents[1]
PHASE4 = ROOT / "tests/golden/soundness_phase4_results.json"
PHASE5 = ROOT / "tests/golden/soundness_phase5_results.json"
RESEARCH_RESULT = ROOT / "tests/golden/soundness_research_result.json"
MATRIX = ROOT / "docs/architecture/soundness-matrix.md"
STATUS = ROOT / "config/soundness_status.json"

PHASE5_ROOTS = {
    "research_result": "research_result", "master_story": "master_story",
    "one_beat": "beats", "one_voice_take": "voice_takes",
    "unused_visual": "visual_asset", "host_plan": "host_plan",
    "naming": "naming",
    "used_visual_metadata": "visual_asset",
    "unused_visual_metadata": "visual_asset",
}
CONFIG_SOURCES = {
    "language_profile", "pronunciation_voice", "channel_identity_logo",
    "intro_design", "agent_model_routing", "music_history_library",
    "logo_bytes", "typed_config_documentary", "typed_config_render",
    "typed_config_chapters", "typed_config_visual", "typed_config_voice",
    "typed_config_audio", "typed_config_host", "typed_config_music",
}
CONFIG_SECTIONS = {
    "language_profile": {"language_profile"},
    "pronunciation_voice": {"pronunciation_voice"},
    "channel_identity_logo": {"channel_identity"},
    "intro_design": {"intro_design"},
    "agent_model_routing": {"documentary"},
    "music_history_library": {"music_history_library"},
    "logo_bytes": {"logo_bytes"},
    "typed_config_documentary": {"documentary"},
    "typed_config_render": {"render"},
    "typed_config_chapters": {"chapters"},
    "typed_config_visual": {"visual"},
    "typed_config_voice": {"voice"},
    "typed_config_audio": {"audio"},
    "typed_config_host": {"host"},
    "typed_config_music": {"music"},
}
CODE_REASON = re.compile(r"\b[\w./-]+\.py:\d+(?:-\d+)?\b")


def _scoped_key(artifact_type: str, language: str | None = None) -> str:
    if ARTIFACT_SCOPES[artifact_type] == "per_language":
        return f"{artifact_type}:{language or 'en'}"
    return f"{artifact_type}:shared"


def direct_consumer_keys(row: dict) -> list[str]:
    """Return immediate registry consumers of the changed source."""
    if row.get("config_source"):
        roots = {
            artifact for artifact, inputs in ARTIFACT_CONFIG_INPUTS.items()
            if inputs & CONFIG_SECTIONS.get(row["source"], set())
        }
        return sorted(_scoped_key(root) for root in roots)
    root = row.get("root")
    if not root:
        return []
    language = "de" if row.get("source") == "spoken_de" else None
    return sorted(_scoped_key(target, language) for target in direct_dependents(root))


def strong_record(row: dict) -> tuple[bool, str]:
    """Require every immediate edge to change or have a code-level reason."""
    if row.get("status", "passed") != "passed" or row.get("hidden"):
        return False, "failed status or hidden input"
    direct = direct_consumer_keys(row)
    actual = set(row.get("actual", ()))
    explained = {
        edge["to"] for edge in row.get("no_effect_edges", ())
        if CODE_REASON.search(edge.get("reason", ""))
    }
    missing = [key for key in direct if key not in actual and key not in explained]
    if missing:
        return False, "missing strong direct-consumer effect: " + ", ".join(missing)
    if not direct:
        return False, "no direct-consumer edge was exercised"
    return True, "all direct consumers changed or have code-level file:line reasons"


def _type(key: str) -> str:
    return key.split(":", 1)[0]


def _closure(root: str) -> set[str]:
    reached = {root}
    changed = True
    while changed:
        changed = False
        for target, deps in ARTIFACT_DEPENDENCIES.items():
            if set(deps) & reached and target not in reached:
                reached.add(target)
                changed = True
    return reached


def load_records() -> list[dict]:
    phase4 = json.loads(PHASE4.read_text())['results']
    phase5 = [
        row for row in json.loads(PHASE5.read_text())['results']
        if row["source"] != "research_result"
    ]
    research_result = json.loads(RESEARCH_RESULT.read_text())['results']
    records = []
    for row in phase4:
        root = {"spoken_de": "spoken_version", "naming": "naming",
                "used_visual": "visual_asset"}[row["source"]]
        records.append({**row, "root": root, "phase": 4})
    for row in phase5:
        root = PHASE5_ROOTS.get(row["source"])
        records.append({**row, "root": root, "phase": 5,
                        "config_source": row["source"] in CONFIG_SOURCES})
    for row in research_result:
        # The run record stores the observed source diff; the predicted set is
        # the registry closure computed from the same declared root.
        predicted_types = _closure("research_result")
        predicted = []
        for artifact_type in sorted(predicted_types):
            if ARTIFACT_SCOPES[artifact_type] == "per_language":
                predicted.extend(f"{artifact_type}:{language}" for language in ("en", "de", "fa", "ar"))
            else:
                predicted.append(f"{artifact_type}:shared")
        record = {**row, "predicted": predicted, "root": "research_result", "phase": 5,
                  "config_source": False}
        record["over_invalidation"] = sorted(
            set(record["predicted"]) - set(record.get("actual", ()))
        )
        records.append(record)
    for record in records:
        record["direct_consumers"] = direct_consumer_keys(record)
        record["strong"], record["strength_reason"] = strong_record(record)
    return sorted(records, key=lambda row: (row["source"], row["phase"]))


def _can_reach(row: dict, target: str) -> bool:
    if row.get("config_source"):
        return target in {_type(key) for key in row.get("predicted", ())}
    root = row.get("root")
    return root is not None and target in _closure(root)


def build_status(records: list[dict]) -> dict:
    statuses = {}
    for target in sorted(ARTIFACT_SCOPES):
        relevant = [row for row in records if _can_reach(row, target)]
        failures = [row for row in relevant if not row.get("strong")]
        if relevant and not failures:
            statuses[target] = "SOUND"
        else:
            reasons = []
            if not relevant:
                reasons.append("no passing snapshot-diff perturbation record reaches this type")
            for row in failures:
                reasons.append(f"{row['source']}: {row.get('strength_reason', 'not strong')}")
            statuses[target] = "UNPROVEN: " + "; ".join(reasons)
    return statuses


def build_matrix(records: list[dict], statuses: dict) -> str:
    # Keep the per-type view explicit.  The perturbation rows below are useful
    # for audit detail, but they are too easy to misread as a type verdict.
    per_type = []
    for target in sorted(ARTIFACT_SCOPES):
        relevant = [row for row in records if _can_reach(row, target)]
        evidence = []
        missing = []
        for row in relevant:
            actual = set(row.get("actual", ()))
            keys = {key for key in actual if _type(key) == target}
            if keys and row.get("strong"):
                grade = "strong"
            elif keys:
                grade = "weak"
            else:
                grade = "none"
            evidence.append(f"{row['source']}={grade}")
            if grade != "strong":
                missing.append(row["source"])
        per_type.append((target, relevant, evidence, missing))
    lines = [
        "# Generated soundness matrix", "",
        "Generated by `tests/generate_soundness_status.py` from the committed",
        "Phase 4 and Phase 5 snapshot-diff records. Actual sets are saved-output",
        "snapshot differences; predicted sets are registry downstream closures.", "",
        "| perturbation | predicted set | actual set | direct consumers | hidden inputs | no-effect edges | over-invalidation | strong | status |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in records:
        lines.append("| " + " | ".join([
            row["source"], ", ".join(row.get("predicted", ())) or "—",
            ", ".join(row.get("actual", ())) or "—",
            ", ".join(row.get("direct_consumers", ())) or "—",
            ", ".join(row.get("hidden", ())) or "none",
            "; ".join(
                f"{edge['from']} → {edge['to']}: {edge['reason']}"
                for edge in row.get("no_effect_edges", ())
            ) or "none",
            ", ".join(row.get("over_invalidation", ())) or "none",
            str(bool(row.get("strong"))).lower(),
            row.get("status", "passed"),
        ]) + " |")
    lines += [
        "", "## Full per-type evidence table", "",
        "Effect grades are saved-output snapshot results: `strong` means the "
        "record passed the direct-consumer/hidden-input checks, `weak` means "
        "the type changed but the record did not pass those checks, and `none` "
        "means the type did not change. Missing lists identify records that "
        "still need a stronger perturbation or a code-level no-effect proof.", "",
        "| artifact type | perturbations that can reach it | effect count/evidence (strong/weak/none) | verdict | exactly missing |",
        "|---|---|---|---|---|",
    ]
    for target, relevant, evidence, missing in per_type:
        counts = {grade: sum(item.endswith(f"={grade}") for item in evidence)
                  for grade in ("strong", "weak", "none")}
        reach = ", ".join(row["source"] for row in relevant) or "none"
        ev = ", ".join(evidence) or "none"
        miss = ", ".join(missing) or "none"
        lines.append(
            f"| {target} | {reach} | {counts['strong']}/{counts['weak']}/{counts['none']} "
            f"({ev}) | {statuses[target]} | {miss} |"
        )
    sound = [target for target, status in statuses.items() if status == "SOUND"]
    unproven = [target for target, status in statuses.items() if status != "SOUND"]
    lines += [
        "", "## Final verdict lists", "",
        "SOUND: " + (", ".join(sound) or "none"),
        "UNPROVEN: " + (", ".join(unproven) or "none"),
        "", "## Per-type result", "", "| artifact type | status |", "|---|---|",
    ]
    lines += [f"| {target} | {statuses[target]} |" for target in sorted(statuses)]
    lines += ["", "No type is marked SOUND without a strong snapshot-diff record for every perturbation that can reach it.",
              "A no-effect edge is strong only when its reason contains a code-level file:line reference.", ""]
    return "\n".join(lines)


def generate() -> tuple[str, str]:
    records = load_records()
    statuses = build_status(records)
    matrix = build_matrix(records, statuses)
    status_payload = {
        "generated_by": "tests/generate_soundness_status.py",
        "record_files": [
            str(PHASE4.relative_to(ROOT)), str(PHASE5.relative_to(ROOT)),
            str(RESEARCH_RESULT.relative_to(ROOT)),
        ],
        "statuses": {key: ("SOUND" if value == "SOUND" else "UNPROVEN")
                     for key, value in sorted(statuses.items())},
        "sound_types": sorted(key for key, value in statuses.items() if value == "SOUND"),
        "unproven_types": sorted(key for key, value in statuses.items() if value != "SOUND"),
        "reasons": {key: value for key, value in sorted(statuses.items())
                    if value != "SOUND"},
        "passing_records": sorted(row["source"] for row in records if row.get("strong")),
        "strong_records": [
            {"source": row["source"], "actual_count": len(row.get("actual", ())),
             "direct_consumers": row.get("direct_consumers", [])}
            for row in records if row.get("strong")
        ],
    }
    return matrix + "\n", json.dumps(status_payload, indent=2, sort_keys=True) + "\n"


def write() -> None:
    matrix, status = generate()
    MATRIX.write_text(matrix)
    STATUS.write_text(status)


if __name__ == "__main__":
    write()
    print(MATRIX)
    print(STATUS)
