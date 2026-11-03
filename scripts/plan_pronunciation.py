"""A dry run of the pronunciation risk engine and the planner over a version's narration: reads the reading cache and the database, calls no model and no
voice provider, writes nothing to the memory. Prints the distribution and the changed and escalated words."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.ai_config import ai_config  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.db.models import ReadingApproval, StoryVersion  # noqa: E402
from app.documentary import voice_prepare as VP  # noqa: E402
from app.documentary.performance import performance_for_version  # noqa: E402
from app.documentary.pronunciation_planner import Planner  # noqa: E402
from app.documentary.reading.block import block_inputs  # noqa: E402
from app.documentary.reading.table import FilmTable  # noqa: E402
from app.documentary.reading.voicing import Voicing, WordSignal  # noqa: E402
from app.documentary.reading.words import key_of  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--language", default="fa")
    p.add_argument("--out", help="write the full plan report here")
    args = p.parse_args()
    with SessionLocal() as db:
        version = db.get(StoryVersion, args.version)
        cache = json.loads((Path(ai_config.voice.work_dir) / str(version.case_id) / ai_config.reading.folder / f"{args.language}_v{version.id}.json").read_text(encoding="utf-8"))
        items = block_inputs(performance_for_version(db, version)["blocks"], VP.english_sources(db, version), ai_config.semantic_review.neighbour_chars)
        approvals = [{"surface": a.surface, "latin": a.latin, "kind": a.kind} for a in db.query(ReadingApproval).filter(ReadingApproval.language == args.language)]
        table = FilmTable.build(cache["reader"], approvals)
        readings, signals, usable = {}, {}, 0
        for item in items:
            saved = cache["blocks"].get(item.block_id)
            if not saved or len(saved["readings"]) != len(item.words):
                continue
            doubted = {d["n"] for d in saved["doubts"]}
            overridden = {int(m.group(1)) for note in saved["notes"] if (m := re.match(r"word (\d+)", note))}
            readings[item.block_id] = [r["latin"] for r in saved["readings"]]
            signals[item.block_id] = [WordSignal(r["confidence"], r["n"] in doubted, r["n"] in overridden) for r in saved["readings"]]
            usable += 1
        fixed = {key_of(a["surface"]): a["latin"] for a in approvals}
        voicing = Voicing(fixed, readings, signals, frozenset(k for e in table.entries for k in e.keys))
        lang = ai_config.voice.for_language(args.language)
        planner = Planner(db, args.language, lang.voice_id, lang.model_id)
        covered = frozenset(key_of(r.get("string_to_replace") or "") for r in VP.sent_rules(version))
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
                          "changed_fraction": round(sum(v for k, v in strategies.items() if k in ("respelling", "selective_marks", "dictionary_rule")) / total, 4)}, indent=1))
        print(json.dumps({"escalated_words": [(e["surface"], len(e["blocks"]), e["level"]) for e in planner.escalations()]}, ensure_ascii=False))
        for c in [c for c in changed if c["strategy"] != "escalate"][:25]:
            print(json.dumps(c, ensure_ascii=False))
        if args.out:
            Path(args.out).write_text(json.dumps(planner.report(), ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
