"""The listening agents and the phoneme recognizer give their opinions on benchmark clips that have none (agent calls are billed to the case)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.documentary import phonetic_benchmark as PB  # noqa: E402


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--case", type=int, required=True, help="the case the agent calls are billed to")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--language", default="fa")
    p.add_argument("--no-recognizer", action="store_true")
    args = p.parse_args()
    with SessionLocal() as db:
        recognizers = {} if args.no_recognizer else {"wav2vec2": PB.wav2vec2_phones}
        print(json.dumps(await PB.propose_all(db, PB.agent_runner(db, args.case), recognizers, limit=args.limit, language=args.language), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
