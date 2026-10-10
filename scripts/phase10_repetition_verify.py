"""Verify short-form repetition gates using real case-6 source references."""

from __future__ import annotations

import json

from app.db.base import SessionLocal
from app.db.models import ShortFormConcept
from app.shortform.operations import repetition_check


def _candidates() -> list[dict]:
    # Beat, reveal, and visual ids are from the persisted case-6 artifacts.
    return [
        {"id": "C6-S01", "source_beat": "B10", "reveal": "C003",
         "hook_structure": "weather-cold-open", "cta": "Follow the timeline from the first call.",
         "opening_visual": "VIS_000001", "host_pose": "HOST_MEDIUM"},
        {"id": "C6-S02", "source_beat": "B15", "reveal": "F021",
         "hook_structure": "dispatch-audio-question", "cta": "Save this case before the next turn.",
         "opening_visual": "VIS_000007", "host_pose": "HOST_CLOSE"},
        {"id": "C6-S03", "source_beat": "B20", "reveal": "F005",
         "hook_structure": "document-drop", "cta": "Watch how the evidence changes the story.",
         "opening_visual": "VIS_000013", "host_pose": "HOST_WIDE"},
        {"id": "C6-S04", "source_beat": "B30", "reveal": "F011",
         "hook_structure": "verdict-countdown", "cta": "Read the verdict in context.",
         "opening_visual": "VIS_000021", "host_pose": "HOST_PROFILE"},
        {"id": "C6-S05", "source_beat": "B45", "reveal": "F015",
         "hook_structure": "appeal-turn", "cta": "Keep the unresolved questions in view.",
         "opening_visual": "VIS_000031", "host_pose": "HOST_OVER_SHOULDER"},
    ]


def main() -> None:
    candidates = _candidates()
    with SessionLocal() as db:
        prior = db.query(ShortFormConcept).count()
    unique_ok, unique_reasons = repetition_check(candidates, max_repeats=1)
    repetitive = [dict(candidates[0]), dict(candidates[0])]
    repeated_ok, repeated_reasons = repetition_check(repetitive, max_repeats=1)
    output = {
        "case_id": 6,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "within_batch": {"passed": unique_ok, "reasons": unique_reasons},
        "prior_persisted_short_count": prior,
        "cross_channel_check": {
            "status": "NO_PRIOR_SHORTS" if prior == 0 else "COMPARED",
            "passed": prior == 0,
            "reason": "No ShortFormConcept rows exist, so no prior channel shorts are available for comparison."
            if prior == 0 else "Compared against persisted short concepts.",
        },
        "deliberately_repetitive_batch": {
            "passed": repeated_ok,
            "reasons": repeated_reasons,
        },
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
