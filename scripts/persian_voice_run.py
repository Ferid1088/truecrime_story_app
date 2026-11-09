"""The Persian narration of a case from a fresh spoken version, step by step with the real agents and the real voice: performance direction (with its punctuation),
PPIE reading (risk-gated checker), the ElevenLabs voice. No host, no music, no video. Every step prints what it cost; a step that stopped on a
provider error is run again by this script and only pays for what is missing. Nothing it keeps is read by another run."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.agents.stages import get_stage_agent  # noqa: E402
from app.core.ai_config import ai_config  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.db.models import Case, StoryVersion  # noqa: E402
from app.documentary.performance import performance_for_version  # noqa: E402
from app.documentary.spoken import spoken_blueprint  # noqa: E402
from app.services.budget import BudgetService  # noqa: E402

LANG = "fa"


class Run:
    def __init__(self, db, case_id: int, version_id: int, out: Path):
        self.db, self.case_id, self.out = db, case_id, out
        self.case = db.get(Case, case_id)
        self.version = db.get(StoryVersion, version_id)
        self.report: dict = {"version": version_id, "steps": {}}
        out.mkdir(parents=True, exist_ok=True)

    def spent(self) -> float:
        return BudgetService(self.db).snapshot(self.case_id).spent_eur

    def by_stage(self, since: datetime) -> dict:
        rows = self.db.execute(text("select stage, count(*), coalesce(sum(cost_eur),0) from provider_calls where case_id=:c and created_at>=:t group by 1 order by 3 desc"),
                               {"c": self.case_id, "t": since}).all()
        return {r[0]: {"calls": r[1], "eur": round(float(r[2]), 4)} for r in rows}

    async def step(self, name: str, fn, tries: int = 12, pause: int = 90):
        start, before, began = datetime.now(timezone.utc), self.spent(), time.time()
        for attempt in range(1, tries + 1):
            try:
                detail = await fn()
                break
            except Exception as e:  # noqa: BLE001  a provider error stops the attempt; the next one continues from what is kept
                self.db.rollback()
                transient = any(k in str(e).lower() for k in ("503", "overloaded", "timed out", "truncated at max_tokens"))           # a provider hiccup or a runaway answer: the next attempt asks only for what is missing
                print(f"[{name}] attempt {attempt} failed: {str(e)[:160]}", flush=True)
                if not transient or attempt == tries:
                    self.report["steps"][name] = {"failed": str(e)[:400], "cost_eur": round(self.spent() - before, 4)}
                    self.save()
                    raise
                await asyncio.sleep(pause)
        self.db.expire_all()
        self.report["steps"][name] = {"detail": detail, "cost_eur": round(self.spent() - before, 4), "seconds": round(time.time() - began), "attempts": attempt,
                                      "by_stage": self.by_stage(start)}
        print(f"[{name}] done: cost EUR {self.report['steps'][name]['cost_eur']}, total EUR {self.spent():.2f}", flush=True)
        self.save()

    def save(self) -> None:
        (self.out / "run_report.json").write_text(json.dumps(self.report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--case", type=int, required=True)
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--final-sha256", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    with SessionLocal() as db:
        run = Run(db, args.case, args.version, Path(args.out))
        final = db.get(StoryVersion, run.version.master_version_id)
        assert final.language == LANG and hashlib.sha256(final.story_text.encode()).hexdigest() == args.final_sha256, "the pinned Persian final changed"
        assert run.version.kind == "spoken" and run.version.status == "ready"
        holder: dict = {}

        async def performance():
            from app.documentary.voice_performance import latest_performance

            existing = latest_performance(db, run.version.id)
            if existing is not None and existing.status in ("ready", "partial"):
                return {"voice_performance_id": existing.id, "reused_in_this_run": True}
            bp = spoken_blueprint(db, run.version)
            row = await get_stage_agent("voice_performance").create(db, run.case, run.version, bp)
            return {"voice_performance_id": row.id, "status": row.status}

        async def reading():
            from app.documentary import voice_prepare as VP
            from app.documentary.reading.block import block_inputs
            from app.documentary.reading.stage import summary
            from app.documentary.reading.text import ReadingStage

            holder["plan"] = performance_for_version(db, run.version)
            items = block_inputs(holder["plan"]["blocks"], VP.english_sources(db, run.version), ai_config.semantic_review.neighbour_chars)
            stage = ReadingStage(db, args.case, run.version, LANG, items, run_cache=run.out / "reading_cache.json")
            read = await stage.run()
            holder["reading"] = (items, read)
            return {**summary(items, read, LANG), "blind_checker": stage.stats}

        async def voice():
            from app.documentary import pronunciation_explain as PE
            from app.documentary.production.audio import render_documentary_audio
            from app.documentary.pronunciation_planner import Planner
            from app.documentary.reading import stage as RS
            from app.documentary.voice_render import VoiceRenderer

            plan = holder["plan"]
            renderer = VoiceRenderer(db=db, budget_case_id=args.case, budget_stage=f"tts:{LANG}", use_asr=False, use_listener=False, arbiter=None)
            items, read = holder["reading"]
            renderer.voicing = await RS.voicing_of(db, LANG, read, items)
            voice_cfg = ai_config.voice.for_language(LANG)
            renderer.planner = Planner(db, LANG, voice_cfg.voice_id, voice_cfg.model_id)
            manifest = await render_documentary_audio(plan, case_id=args.case, story_version_id=run.version.id, db=db, renderer=renderer, with_music=False)
            folder = renderer.out_dir(args.case, LANG, run.version.id)
            PE.save_run(db, run.version, LANG, plan, renderer.planner, renderer.voicing, folder)
            return {"seconds": manifest.get("duration_seconds"), "characters_paid": manifest.get("characters_paid"), "flags": manifest.get("flags"), "folder": str(folder)}

        for name, fn in (("performance", performance), ("reading", reading), ("voice", voice)):
            await run.step(name, fn)
        print(json.dumps(run.report["steps"], ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    asyncio.run(main())
