"""A dry run of the pronunciation risk engine and the planner over a version's narration: reads the reading cache and the database, calls no model and no
voice provider, writes nothing to the memory. Prints the distribution and the changed and escalated words."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.ai_config import ai_config  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.documentary import voice_prepare as VP  # noqa: E402
from app.documentary.pronunciation_explain import load  # noqa: E402
from app.documentary.pronunciation_planner import CHANGES, Planner  # noqa: E402
from app.documentary.reading.block import block_inputs  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--language", default="fa")
    p.add_argument("--out", help="write the full plan report here")
    args = p.parse_args()
    with SessionLocal() as db:
        version, perf, voicing, covered = load(db, args.version, args.language)
        items = block_inputs(perf["blocks"], VP.english_sources(db, version), ai_config.semantic_review.neighbour_chars)
        readings, usable = voicing.readings, len(voicing.readings)
        lang = ai_config.voice.for_language(args.language)
        planner = Planner(db, args.language, lang.voice_id, lang.model_id)
        levels, strategies, changed = Counter(), Counter(), []
        for item in items:
            if item.block_id not in readings:
                continue
            plan = planner.plan(item.block_id, item.text, voicing, covered=covered)
            for r, d in zip(plan.risks, plan.decisions, strict=True):
                levels[r.level] += 1
                strategies[d.strategy] += 1
                if d.strategy != "keep":
                    changed.append({"block": item.block_id, "word": d.surface, "level": d.level, "strategy": d.strategy, "replacement": d.replacement, "basis": d.basis})
        total = sum(levels.values())
        print(json.dumps({"blocks_planned": usable, "blocks_without_usable_reading": len(items) - usable, "words": total, "levels": dict(levels), "strategies": dict(strategies),
                          "changed_fraction": round(sum(v for k, v in strategies.items() if k in CHANGES) / total, 4)}, indent=1))
        print(json.dumps({"escalated_words": [(e["surface"], len(e["blocks"]), e["level"]) for e in planner.escalations()]}, ensure_ascii=False))
        for c in [c for c in changed if c["strategy"] != "escalate"][:25]:
            print(json.dumps(c, ensure_ascii=False))
        if args.out:
            Path(args.out).write_text(json.dumps(planner.report(), ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
