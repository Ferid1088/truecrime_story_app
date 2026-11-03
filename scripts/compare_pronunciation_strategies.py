"""Voices a few blocks of a film once per pronunciation strategy (none, finglish_alias, persian_diacritic_alias) with the same voice, model, settings and
seed, and keeps the exact requests. The blocks and their rules are found by the risk analyzer from the Reading Engine's output, not chosen by hand.
Paid (ElevenLabs): python scripts/compare_pronunciation_strategies.py ENGINE.json OUT_DIR [--blocks 5] [--seed 7] [--max-chars 320]"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.core.ai_config import ai_config  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.db.models import StoryVersion  # noqa: E402
from app.documentary import voice_prepare as VP  # noqa: E402
from app.documentary.performance import performance_for_version  # noqa: E402
from app.documentary.reading import dictionary as D  # noqa: E402
from app.documentary.reading.block import block_inputs  # noqa: E402
from app.documentary.reading.engine import Outcome  # noqa: E402
from app.providers.voice import VoiceRequest, get_voice_provider  # noqa: E402

STRATEGIES = ("none", "finglish_alias", "persian_diacritic_alias")


def pick(rules: dict[str, D.Rules], texts: dict[str, str], count: int, seed: int, max_chars: int) -> list[str]:
    """The blocks with the highest total risk (the first half) and, for contrast, blocks drawn by chance among the others that have rules."""
    ranked = sorted((b for b, r in rules.items() if r.rules and len(texts[b]) <= max_chars), key=lambda b: -sum(c["risk"] for c in rules[b].chosen))
    top = ranked[: (count + 1) // 2]
    rest = [b for b in ranked if b not in top]
    random.Random(seed).shuffle(rest)
    return top + rest[: count - len(top)]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("engine")
    ap.add_argument("out")
    ap.add_argument("--blocks", type=int, default=5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--max-chars", type=int, default=320)
    ap.add_argument("--version", type=int, default=289)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    outcomes = [Outcome(**o) for o in json.loads(Path(args.engine).read_text(encoding="utf-8"))["outcomes"]]
    by_block: dict[str, list[Outcome]] = defaultdict(list)
    for o in outcomes:
        by_block[o.block_id].append(o)
    with SessionLocal() as db:
        version = db.get(StoryVersion, args.version)
        items = {i.block_id: i for i in block_inputs(performance_for_version(db, version)["blocks"], VP.english_sources(db, version), 400)}
    texts = {b: items[b].text for b in by_block}
    rules = {b: {s: D.build(by_block[b], texts[b], s) for s in STRATEGIES} for b in by_block}
    chosen = pick({b: r["finglish_alias"] for b, r in rules.items()}, texts, args.blocks, args.seed, args.max_chars)
    provider = get_voice_provider()
    cfg = ai_config.voice
    lang, settings = cfg.for_language("fa"), cfg.settings_for("neutral_documentary", "fa")
    record = []
    for block in chosen:
        for strategy in STRATEGIES:
            built = rules[block][strategy]
            made = await provider.create_pronunciation_dictionary(f"ppie_{strategy}_{block}", built.rules) if built.rules else None
            locators = [{"pronunciation_dictionary_id": made["id"], "version_id": made["version_id"]}] if made else []
            req = VoiceRequest(text=texts[block], voice_id=lang.voice_id, model_id=lang.model_id, settings=settings, seed=1, language="fa", language_code="fa",
                               pronunciation_dictionaries=locators)
            result = await provider.synthesize(req)
            (out / f"{block}__{strategy}.mp3").write_bytes(result.audio)
            record.append({"block": block, "strategy": strategy,
                           "create_dictionary": {"name": f"ppie_{strategy}_{block}", "rules": built.rules} if built.rules else None,
                           "dictionary": made, "tts_request": provider._body(req), "rules_chosen": built.chosen, "rules_skipped": built.skipped})
            print(block, strategy, len(built.rules), "rules", flush=True)
    (out / "requests.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
