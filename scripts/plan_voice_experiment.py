"""Prepares the controlled voice experiment (see app/documentary/voice_experiment.py): which blocks, what each arm is sent, the exact characters, the cost and a
cap. No provider is called and nothing is written to the memory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.ai_config import ai_config  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.documentary import voice_experiment as VE  # noqa: E402
from app.documentary.pronunciation_explain import load  # noqa: E402
from app.documentary.pronunciation_planner import Planner  # noqa: E402


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
