"""Voices the whole narration of a version with the pronunciation risk engine and the adaptive planner into data/previews/<name>/ (BILLABLE, ElevenLabs). The
narration folder of the film is not touched: the configured work folder is swapped for the preview's own inside this process. One take per block, no listening
retakes. Refuses to start without --confirm-spend or when the cap does not fit the case's remaining budget; every request is counted and the one that would pass
the cap is not sent."""

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
from app.documentary import voice_prepare as VP  # noqa: E402
from app.documentary.pronunciation_explain import load  # noqa: E402
from app.documentary.pronunciation_planner import Planner  # noqa: E402
from app.documentary.voice_render import VoiceRenderer  # noqa: E402
from app.providers.voice import get_voice_provider  # noqa: E402
from app.services.budget import BudgetService  # noqa: E402
from app.utils import utc_now  # noqa: E402


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--language", default="fa")
    p.add_argument("--name", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--cap-eur", type=float, required=True)
    p.add_argument("--confirm-spend", action="store_true")
    args = p.parse_args()
    with SessionLocal() as db:
        version, plan, voicing, _ = load(db, args.version, args.language)
        lang = ai_config.voice.for_language(args.language)
        snap = BudgetService(db).snapshot(version.case_id)
        if not args.confirm_spend:
            raise SystemExit("billable: needs --confirm-spend")
        if snap.spent_eur >= snap.cap_eur or snap.spent_eur + args.cap_eur > snap.cap_eur:
            raise SystemExit(f"the case has spent {snap.spent_eur} of {snap.cap_eur} EUR: no room for a cap of {args.cap_eur} EUR")
        provider = VE.CountingProvider(get_voice_provider(), int(args.cap_eur / VE.eur_per_character(lang.model_id)))
        plan = {**plan, "pronunciation_dictionary": await VP.ensure_dictionary(db, version, args.language), "pronunciation_rules": VP.sent_rules(version)}
        out_root = NP.folder(args.name)
        kept = ai_config.voice.work_dir
        ai_config.voice.work_dir = str(out_root)
        try:
            renderer = VoiceRenderer(db=db, budget_case_id=version.case_id, budget_stage=f"preview:{args.name}", provider=provider, use_asr=False, use_listener=False)
            renderer.voicing = voicing
            renderer.planner = Planner(db, args.language, lang.voice_id, lang.model_id)
            manifest = await renderer.render(plan, case_id=version.case_id, story_version_id=version.id)
            folder = renderer.out_dir(version.case_id, args.language, version.id)
        finally:
            ai_config.voice.work_dir = kept
        after = BudgetService(db).snapshot(version.case_id)
        texts = {b["block_id"]: (b.get("tts_text") or b["text"]) for b in plan["blocks"]}
        NP.write_info(args.name, {
            "title": args.title, "case_id": version.case_id, "story_version_id": version.id, "language": args.language, "created_at": utc_now().isoformat(),
            "audio": str((folder / "narration.mp3").relative_to(out_root)), "duration_seconds": manifest["duration_seconds"],
            "voice_id": lang.voice_id, "model_id": lang.model_id, "settings": ai_config.voice.settings_for("neutral_documentary", args.language),
            "source": "the pronunciation risk engine and the adaptive planner; one take per block; voice only (no music, no listening retakes)",
            "cost": {"characters_sent": provider.used, "requests": provider.requests, "actual_eur": round(after.spent_eur - snap.spent_eur, 4), "cap_eur": args.cap_eur,
                     "spent_before_eur": snap.spent_eur, "spent_after_eur": after.spent_eur, "case_cap_eur": after.cap_eur},
            "blocks": [{"block_id": b["block_id"], "start": b["start"], "end": b["end"], "text": texts.get(b["block_id"], "")} for b in manifest["timeline"]["blocks"]],
            "plan_file": manifest.get("pronunciation_plan")})
        print(json.dumps({"preview": args.name, "audio": str(folder / "narration.mp3"), "duration_seconds": manifest["duration_seconds"], "characters_sent": provider.used,
                          "actual_eur": round(after.spent_eur - snap.spent_eur, 4), "spent_after_eur": after.spent_eur}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
