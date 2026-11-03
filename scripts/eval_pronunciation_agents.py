"""Evaluates the pronunciation agents on their HELD-OUT examples (never in a prompt): the agent is asked with its complete system prompt and its answer is
scored on the decision fields. Agents that need a real recording (audio_observer, prosody) are not evaluated here: their examples are hypothetical.
Calls the language models (a few cents): python scripts/eval_pronunciation_agents.py [agent ...] [--out FILE]"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.documentary import pronunciation_agents as PA  # noqa: E402

TEXT_AGENTS = ("linguist", "risk", "intervention", "memory", "qa")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("agents", nargs="*", default=list(TEXT_AGENTS))
    parser.add_argument("--out", type=Path, default=Path("pronunciation_agents_eval.json"))
    args = parser.parse_args()
    out, results = args.out, []
    for agent in args.agents:
        for example in PA.examples(agent, "heldout"):
            try:
                answer = await PA.run(agent, example["input"])
                results.append({"agent": agent, "example": example["id"], "score": PA.score(agent, answer, example), "answer": answer.model_dump()})
            except Exception as e:  # noqa: BLE001 - the report keeps what failed
                results.append({"agent": agent, "example": example["id"], "error": f"{type(e).__name__}: {e}"[:300]})
            print(agent, example["id"], results[-1].get("score") or results[-1].get("error"), flush=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
