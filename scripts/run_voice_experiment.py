"""Runs the controlled voice experiment (app/documentary/voice_experiment.py) with the real voice provider: BILLABLE. It refuses to start without
--confirm-spend, with a cap below the worst case of the selection, or when the case has no room for the cap; every request is counted and the one that would pass the
cap is not sent. The audio goes to data/experiments/<name>/, never into the narration folder of the film. Run scripts/plan_voice_experiment.py first."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from plan_voice_experiment import load  # noqa: E402

from app.core.ai_config import ai_config  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.documentary import voice_experiment as VE  # noqa: E402
from app.documentary import voice_prepare as VP  # noqa: E402
from app.documentary.pronunciation_planner import Planner  # noqa: E402
from app.providers.voice import get_voice_provider  # noqa: E402
from app.services.budget import BudgetService  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--language", default="fa")
    p.add_argument("--name", required=True, help="the folder under data/experiments")
    p.add_argument("--cap-eur", type=float, required=True)
    p.add_argument("--confirm-spend", action="store_true", help="the owner has approved the selection, the estimate and the cap")
    args = p.parse_args()
    with SessionLocal() as db:
        version, plan, voicing, covered = load(db, args.version, args.language)
        lang = ai_config.voice.for_language(args.language)
        planner_for = lambda: Planner(db, args.language, lang.voice_id, lang.model_id)    # noqa: E731
        selection = VE.select([b for b in plan["blocks"] if b["block_id"] in voicing.readings], voicing, planner_for(), covered)
        est = VE.estimate(selection, lang.model_id)
        snap = BudgetService(db).snapshot(version.case_id)
        VE.guard(est, args.cap_eur, snap.spent_eur, snap.cap_eur, args.confirm_spend)
        provider = VE.CountingProvider(get_voice_provider(), int(args.cap_eur / VE.eur_per_character(lang.model_id)))
        locators = await VP.ensure_dictionary(db, version, args.language)
        rules = VP.sent_rules(version)
        root = ROOT / "data" / "experiments" / args.name
        results, folders = [], {}
        for arm in VE.ARMS:
            result = await VE.run_arm(arm, db=db, case_id=version.case_id, version_id=version.id, language=args.language, plan=plan, selection=selection, voicing=voicing,
                                      planner_for=planner_for, provider=provider, work_dir=root / arm, locators=locators, rules=rules)
            results.append(result)
            folders[arm] = Path(result["folder"])
            print(json.dumps(result, ensure_ascii=False), flush=True)
        report = VE.prosody_report(folders, selection)
        clips = VE.register_clips(db, folders, selection, version.case_id, version.id, voicing.readings, root / "blind_key.json", args.language)
        after = BudgetService(db).snapshot(version.case_id)
        summary = {"estimate": est, "characters_sent": provider.used, "requests": provider.requests, "spent_before_eur": snap.spent_eur, "spent_after_eur": after.spent_eur,
                   "actual_cost_eur": round(after.spent_eur - snap.spent_eur, 4), "clips_registered_for_blind_labelling": clips, "arms": results, "prosody": report}
        (root / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({k: v for k, v in summary.items() if k not in ("prosody", "arms")}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
