"""Golden test: pins the observable shape of the story pipeline so a migration step cannot change it unnoticed.

Providers are scripted (no credits). The snapshot holds structure, not prose: the sequence of provider
calls, the act plan, evidence usage and word counts. Regenerate on purpose with
`TC_UPDATE_GOLDEN=1 pytest tests/test_golden_pipeline.py`, then review the diff.
"""
import asyncio
import json
import os
from pathlib import Path

from tests.test_master_pipeline import ScriptGen, _add_fact, _mk_case, _pipeline

GOLDEN = Path(__file__).parent / "golden" / "story_pipeline.json"


def _run_once(db_session, monkeypatch) -> dict:
    fake = ScriptGen()
    fake.structured["story_director"] = {
        "title": "t", "central_question": "q",
        "acts": [
            {"id": "a1", "purpose": "p1", "evidence_ids": ["F001"]},
            {"id": "a2", "purpose": "p2", "evidence_ids": ["F002"]},
            {"id": "a3", "purpose": "p3", "evidence_ids": []},
        ],
    }
    pipe = _pipeline(fake, monkeypatch)
    case = _mk_case(db_session)
    for claim in ("fact one", "fact two", "fact three"):
        _add_fact(db_session, case, claim)
    version = asyncio.run(pipe.run(db_session, case, target_minutes=10, language="en", tone="t", iterations=1))
    struct = json.loads(version.narrative_structure)
    usage = struct["evidence_usage"]
    calls: dict[str, int] = {}
    for kind, role, _ in fake.calls:
        key = f"{kind}:{role}"
        calls[key] = calls.get(key, 0) + 1
    return {
        "calls": dict(sorted(calls.items())),
        "call_sequence": [f"{k}:{r}" for k, r, _ in fake.calls],
        "act_ids": [a["id"] for a in struct["acts"]],
        "act_words_positive": all(a["target_words"] > 0 for a in struct["acts"]),
        "used_evidence_ids": sorted(usage["used_evidence_ids"]),
        "unused_evidence_ids": sorted(usage["unused_evidence_ids"]),
        "structure_keys": sorted(struct.keys()),
        "story_has_text": len(version.story_text.split()) > 0,
    }


def test_pipeline_output_matches_golden(db_session, monkeypatch):
    actual = _run_once(db_session, monkeypatch)
    if os.environ.get("TC_UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected


def test_pipeline_is_deterministic_across_runs(db_session, monkeypatch):
    assert _run_once(db_session, monkeypatch) == _run_once(db_session, monkeypatch)
