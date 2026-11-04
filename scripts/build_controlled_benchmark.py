"""Builds a controlled benchmark set (config/benchmarks/<name>.json) for validating the acoustic review: BILLABLE (ElevenLabs), refused without
--confirm-spend, capped by --cap-eur and the case's budget; booked on --case. The clips go to the phonetic benchmark for blind labelling."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.ai_config import ai_config  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.documentary import voice_experiment as VE  # noqa: E402
from app.documentary.ppie_review import controlled as CB  # noqa: E402
from app.documentary.voice_render import VoiceRenderer  # noqa: E402
from app.providers.voice import get_voice_provider  # noqa: E402
from app.services.budget import BudgetService  # noqa: E402


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="controlled_fa")
    p.add_argument("--case", type=int, required=True)
    p.add_argument("--cap-eur", type=float, required=True)
    p.add_argument("--confirm-spend", action="store_true")
    args = p.parse_args()
    if not args.confirm_spend:
        raise SystemExit("billable: needs --confirm-spend")
    with SessionLocal() as db:
        before = BudgetService(db).snapshot(args.case)
        if before.spent_eur + args.cap_eur > before.cap_eur:
            raise SystemExit(f"the case has spent {before.spent_eur} of {before.cap_eur} EUR: no room for {args.cap_eur} EUR")
        model = ai_config.voice.for_language("fa").model_id
        provider = VE.CountingProvider(get_voice_provider(), int(args.cap_eur / VE.eur_per_character(model)))
        renderer = VoiceRenderer(provider=provider, use_asr=False, use_listener=False, db=db, budget_case_id=args.case, budget_stage="benchmark:controlled")
        result = await CB.build(db, args.name, renderer, args.case)
        from app.documentary.pronunciation import get_listener

        result["defective_recordings"] = CB.flag_defective(db, get_listener(), result["set"])
        after = BudgetService(db).snapshot(args.case)
    print(json.dumps({**{k: v for k, v in result.items() if k != "samples"}, "characters_sent": provider.used, "requests": provider.requests,
                      "spent_eur": round(after.spent_eur - before.spent_eur, 4), "case_spent_eur": after.spent_eur}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
