"""Prepares the controlled voice experiment (see app/documentary/voice_experiment.py): which blocks, what each arm is sent, the exact characters, the cost and a
cap. No provider is called and nothing is written to the memory."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.ai_config import ai_config  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.db.models import ReadingApproval, StoryVersion  # noqa: E402
from app.documentary import voice_experiment as VE  # noqa: E402
from app.documentary import voice_prepare as VP  # noqa: E402
from app.documentary.performance import performance_for_version  # noqa: E402
from app.documentary.pronunciation_planner import Planner  # noqa: E402
from app.documentary.reading import stage as RS  # noqa: E402
from app.documentary.reading.answer import ReadingWord  # noqa: E402
from app.documentary.reading.block import block_inputs  # noqa: E402
from app.documentary.reading.table import FilmTable  # noqa: E402
from app.documentary.reading.voicing import Voicing, WordSignal  # noqa: E402
from app.documentary.reading.words import key_of  # noqa: E402


def load(db, version_id: int, language: str):
    version = db.get(StoryVersion, version_id)
    cache = json.loads((Path(ai_config.voice.work_dir) / str(version.case_id) / ai_config.reading.folder / f"{language}_v{version.id}.json").read_text(encoding="utf-8"))
    plan = performance_for_version(db, version)
    items = block_inputs(plan["blocks"], VP.english_sources(db, version), ai_config.semantic_review.neighbour_chars)
    approvals = [{"surface": a.surface, "latin": a.latin, "kind": a.kind} for a in db.query(ReadingApproval).filter(ReadingApproval.language == language)]
    table = FilmTable.build(cache["reader"], approvals)
    readings, signals = {}, {}
    for item in items:
        saved = cache["blocks"].get(item.block_id)
        if not saved or len(saved["readings"]) != len(item.words):
            continue
        doubted = {d["n"] for d in saved["doubts"]}
        overridden = {int(m.group(1)) for note in saved["notes"] if (m := re.match(r"word (\d+)", note))}
        readings[item.block_id] = [r["latin"] for r in saved["readings"]]
        signals[item.block_id] = [WordSignal(r["confidence"], r["n"] in doubted, r["n"] in overridden) for r in saved["readings"]]
    opinions = RS.opinions_of({b: [ReadingWord(**r) for r in saved["readings"]] for b, saved in cache["blocks"].items() if b in readings})
    voicing = Voicing({key_of(a["surface"]): a["latin"] for a in approvals}, readings, signals, RS.names_of(table), opinions)
    covered = frozenset(key_of(r.get("string_to_replace") or "") for r in VP.sent_rules(version))
    return version, plan, voicing, covered


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--language", default="fa")
    p.add_argument("--out", help="write the experiment plan here")
    args = p.parse_args()
    with SessionLocal() as db:
        version, plan, voicing, covered = load(db, args.version, args.language)
        lang = ai_config.voice.for_language(args.language)
        selection = VE.select([b for b in plan["blocks"] if b["block_id"] in voicing.readings], voicing, Planner(db, args.language, lang.voice_id, lang.model_id), covered)
        out = {"version": version.id, "language": args.language, "voice_id": lang.voice_id, "model_id": lang.model_id, "settings": ai_config.voice.settings_for("neutral_documentary", args.language),
               "seed": ai_config.voice.seed, **VE.estimate(selection, lang.model_id), **selection.describe()}
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
