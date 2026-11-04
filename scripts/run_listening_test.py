"""Voices a listening test (prompts/pronunciation/listening_tests/<name>.json) into data/previews/<name>/ for a person to judge in the Pronunciation tab
(BILLABLE, ElevenLabs). Each item is sent exactly as written (no planner, no dictionary), with the voice, model and settings of the language's narration.
The cost is booked on the given case (a scratch case, never a film's). Refuses to start without --confirm-spend or when the cap does not fit the case's
remaining budget; the request that would pass the cap is not sent."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.ai_config import ai_config  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.documentary import narration_preview as NP  # noqa: E402
from app.documentary import voice_experiment as VE  # noqa: E402
from app.documentary.voice_render import VoiceRenderer  # noqa: E402
from app.providers.voice import get_voice_provider  # noqa: E402
from app.services.budget import BudgetService  # noqa: E402
from app.utils import utc_now  # noqa: E402


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--spec", required=True)
    p.add_argument("--case-id", type=int, required=True)
    p.add_argument("--cap-eur", type=float, required=True)
    p.add_argument("--pause-ms", type=int, default=1500)
    p.add_argument("--confirm-spend", action="store_true")
    args = p.parse_args()
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    plan, bumps, labels = NP.listening_plan(spec, args.pause_ms)
    with SessionLocal() as db:
        lang = ai_config.voice.for_language(spec["language"])
        snap = BudgetService(db).snapshot(args.case_id)
        if not args.confirm_spend:
            raise SystemExit("billable: needs --confirm-spend")
        if snap.spent_eur >= snap.cap_eur or snap.spent_eur + args.cap_eur > snap.cap_eur:
            raise SystemExit(f"the case has spent {snap.spent_eur} of {snap.cap_eur} EUR: no room for a cap of {args.cap_eur} EUR")
        provider = VE.CountingProvider(get_voice_provider(), int(args.cap_eur / VE.eur_per_character(lang.model_id)))
        out_root = NP.folder(spec["name"])
        kept = ai_config.voice.work_dir
        ai_config.voice.work_dir = str(out_root)
        try:
            renderer = VoiceRenderer(db=db, budget_case_id=args.case_id, budget_stage=f"listening_test:{spec['name']}", provider=provider, use_asr=False,
                                     use_listener=False)
            renderer.seed_bumps.update(bumps)
            manifest = await renderer.render(plan, case_id=args.case_id, story_version_id=0)
            folder = renderer.out_dir(args.case_id, spec["language"], 0)
        finally:
            ai_config.voice.work_dir = kept
        after = BudgetService(db).snapshot(args.case_id)
        sent = {b["block_id"]: b.get("tts_text") or next(x["text"] for x in plan["blocks"] if x["block_id"] == b["block_id"]) for b in manifest["blocks"]}
        NP.write_info(spec["name"], {
            "title": spec["title"], "case_id": args.case_id, "story_version_id": 0, "language": spec["language"], "created_at": utc_now().isoformat(),
            "audio": str((folder / "narration.mp3").relative_to(out_root)), "duration_seconds": manifest["duration_seconds"],
            "voice_id": lang.voice_id, "model_id": lang.model_id, "settings": ai_config.voice.settings_for("neutral_documentary", spec["language"]),
            "source": spec["about"],
            "cost": {"characters_sent": provider.used, "requests": provider.requests, "actual_eur": round(after.spent_eur - snap.spent_eur, 4), "cap_eur": args.cap_eur,
                     "spent_before_eur": snap.spent_eur, "spent_after_eur": after.spent_eur, "case_cap_eur": after.cap_eur},
            "blocks": [{"block_id": b["block_id"], "start": b["start"], "end": b["end"], "text": sent[b["block_id"]], "label": labels[b["block_id"]]}
                       for b in manifest["timeline"]["blocks"]]})
        print(json.dumps({"preview": spec["name"], "duration_seconds": manifest["duration_seconds"], "characters_sent": provider.used,
                          "actual_eur": round(after.spent_eur - snap.spent_eur, 4)}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
