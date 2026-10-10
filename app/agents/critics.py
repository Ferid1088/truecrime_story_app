"""The DocumentaryCritics agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.production.critics."""

from __future__ import annotations

from app.agents.runner import run_agent
import json
from sqlalchemy.orm import Session
from app.core.ai_config import ai_config
from app.db.models import ProductionScript, VisualAsset
from app.documentary.visuals.usage import annotate, asset_facts, record_media_usage
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.documentary.production.critics import (
    CRITIC_FOCUS,
    CRITIC_ROLES,
    CRITIC_SYSTEM,
    apply_fixes,
    describe_cut,
    deterministic_checks,
    previous_productions,
)


class DocumentaryCritics:
    def __init__(self):
        self.gen = get_generation_provider()

    async def review(self, db: Session, row: ProductionScript,
                     manifest_words: list[dict] | None = None, apply: bool = True) -> dict:
        from app.documentary.spoken import LANG_NAMES

        script = json.loads(row.script_json or "{}")
        if not manifest_words:  # caption chunks are precise enough for review
            manifest_words = [{"word": s["text"], "start": s["start"], "end": s["end"]}
                              for s in script.get("subtitles") or []]
        assets = {a.asset_code: a for a in db.query(VisualAsset).filter(
            VisualAsset.case_id == row.case_id).all()}
        previous = previous_productions(db, row)
        report = {"deterministic": deterministic_checks(script, assets, previous),
                  "critics": {}, "fixes": [],
                  "compared_with": [{"production_script_id": p["id"], "case_id": p["case_id"]}
                                    for p in previous]}
        cut = describe_cut(script, assets, manifest_words)
        async def critic(name: str, role: str):
            system = CRITIC_SYSTEM.format(language=LANG_NAMES.get(row.language, row.language),
                                          focus=CRITIC_FOCUS[name])
            with track_run(db, row.case_id, f"Documentary Critic: {name} ({row.language})",
                           input_summary=f"production_script={row.id}") as run:
                data, res = await run_agent("documentary.critic", self.gen, json.dumps({"cut": cut}, ensure_ascii=False), system=system, role=role)
                stamp_run(run, res, role)
            data = data if isinstance(data, dict) else {}
            return name, {
                "score": data.get("score"), "summary": data.get("summary"),
                "problems": [p for p in data.get("problems") or [] if isinstance(p, dict)][:8],
                "model": getattr(res, "model", None),
            }

        from app.core.concurrency import gather_limited

        named = [(n, CRITIC_ROLES[n]) for n in ai_config.documentary_critics.critics
                 if CRITIC_ROLES.get(n)]
        # the critics are independent: all at once
        for name, entry in await gather_limited(None, [critic(n, r) for n, r in named]):
            report["critics"][name] = entry
        if apply and ai_config.documentary_critics.max_fix_iterations:
            problems = [p for c in report["critics"].values() for p in c["problems"]]
            problems += [i for i in report["deterministic"]["issues"] if i.get("fix")]
            report["fixes"] = apply_fixes(script, problems, assets)
            if report["fixes"]:
                # appearance numbers and justifications follow the fixed cut
                annotate(script.get("shots") or [],
                         {c: asset_facts(a) for c, a in assets.items()},
                         script.get("sentence_entities"))
                report["after_fixes"] = deterministic_checks(script, assets, previous)
                row.script_json = json.dumps(script, ensure_ascii=False)
                record_media_usage(db, row, script)
        scores = [c["score"] for c in report["critics"].values()
                  if isinstance(c.get("score"), (int, float))]
        report["score"] = round(sum(scores) / len(scores), 1) if scores else None
        row.critique_json = json.dumps(report, ensure_ascii=False)
        row.status = "reviewed"
        db.commit()
        return report
