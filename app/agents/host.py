"""The HostDirector agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.host."""

from __future__ import annotations

from app.agents.runner import run_agent
import json
from sqlalchemy.orm import Session
from app.core.ai_config import ai_config
from app.db.models import Case, EditorialBlueprint, HostPlan, HostSegments, StoryVersion
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.documentary.host import (
    _dialogues,
    _evidence_items,
    _memory_index,
    _pack,
    archive,
    beat_texts,
    critic_input,
    critic_system_prompt,
    critic_verdicts,
    dialogue_issues,
    director_input,
    director_system_prompt,
    memories,
    recent_segments,
    remember,
    segment_output,
    validate_host_plan,
    writer_input,
    writer_system_prompt,
)


class HostDirector:
    def __init__(self):
        self.gen = get_generation_provider()
        self.cfg = ai_config.host

    async def _json(self, db, case_id: int, label: str, role: str, system: str,
                    payload: dict) -> tuple[dict, object]:
        with track_run(db, case_id, label, input_summary=label) as run:
            raw, res = await run_agent(role, self.gen, json.dumps(payload, ensure_ascii=False), system=system)
            stamp_run(run, res, role)
        return raw, res

    # -- 1. plan -------------------------------------------------------------
    async def create_plan(self, db: Session, case: Case, bp_row: EditorialBlueprint,
                          master: StoryVersion) -> HostPlan:
        if bp_row.status == "invalid":
            raise RuntimeError("The blueprint is invalid; fix it before planning the host.")
        blueprint = json.loads(bp_row.blueprint_json or "{}")
        texts = beat_texts(master, blueprint)
        pack = _pack(db, case.id)
        arch, mem = archive(db, case.id), memories(db, case.id)
        recent = recent_segments(db, case.id, None)
        payload = director_input(case, blueprint, texts, pack, arch, mem, recent)
        evidence_ids = {e["id"] for e in payload["evidence"]}
        refs = set(_memory_index(arch, mem))
        narration_seconds = sum(len(t.split()) for t in texts.values()) / \
            ai_config.documentary.wpm(master.language or "en") * 60
        system = director_system_prompt(self.cfg)

        raw, res = await self._json(db, case.id, "Host Director", "host_director",
                                    system, payload)
        plan, report = validate_host_plan(raw, blueprint, evidence_ids, refs,
                                          narration_seconds, self.cfg)
        repairs = 0
        while report["errors"] and repairs < self.cfg.max_repair_iterations:
            repairs += 1
            raw, res = await self._json(
                db, case.id, f"Host Director repair {repairs}", "host_director", system,
                {**payload, "previous_plan": raw, "errors_to_fix": report["errors"]})
            plan, report = validate_host_plan(raw, blueprint, evidence_ids, refs,
                                              narration_seconds, self.cfg)
        report["repair_iterations"] = repairs
        count = db.query(HostPlan).filter(HostPlan.blueprint_id == bp_row.id).count()
        row = HostPlan(case_id=case.id, blueprint_id=bp_row.id, version=count + 1,
                       status=report["status"],
                       plan_json=json.dumps(plan, ensure_ascii=False),
                       validation_json=json.dumps(report, ensure_ascii=False),
                       generation_model=getattr(res, "model", None))
        db.add(row)
        db.commit()
        db.refresh(row)
        if row.status != "invalid":
            remember(db, case.id, row, plan.get("memory_updates") or [])
        return row

    # -- 2.+3. dialogue per language ----------------------------------------
    async def write(self, db: Session, case: Case, version: StoryVersion,
                    plan_row: HostPlan) -> HostSegments:
        if plan_row.status == "invalid":
            raise RuntimeError("The host plan is invalid.")
        language = version.language or "en"
        plan = json.loads(plan_row.plan_json or "{}")
        bp_row = db.get(EditorialBlueprint, plan_row.blueprint_id)
        blueprint = json.loads(bp_row.blueprint_json or "{}")
        beat_ids = [b["id"] for b in blueprint.get("beats") or []]
        texts = beat_texts(version, blueprint)
        pack_items = {e["id"]: e for e in _evidence_items(_pack(db, case.id))}
        arch_mem = _memory_index(archive(db, case.id), memories(db, case.id))
        recent = recent_segments(db, case.id, language)
        wi = writer_input(plan, texts, beat_ids, language, pack_items, arch_mem, recent,
                          self.cfg)
        by_id = {s["id"]: s for s in plan.get("segments") or []}
        written: dict[str, str] = {}
        verdicts: dict[str, dict] = {}
        res = None
        if wi["segments"]:
            raw, res = await self._json(db, case.id, f"Host Writer {language}", "host_writer",
                                        writer_system_prompt(language, self.cfg), wi)
            written = _dialogues(raw)
            verdicts = await self._check(db, case.id, language, wi, written, recent, by_id,
                                         set(by_id))
        repairs = 0
        while repairs < self.cfg.max_repair_iterations:
            failing = [sid for sid, v in verdicts.items() if not v["pass"]]
            if not failing:
                break
            repairs += 1
            sub = {**wi, "segments": [
                {**s, "previous_dialogue": written.get(s["id"], ""),
                 "problems_to_fix": verdicts[s["id"]]["problems"]
                 + verdicts[s["id"]]["deterministic"]}
                for s in wi["segments"] if s["id"] in failing]}
            raw, res = await self._json(db, case.id, f"Host Writer {language} repair {repairs}",
                                        "host_writer", writer_system_prompt(language, self.cfg),
                                        sub)
            fixed = {k: v for k, v in _dialogues(raw).items() if k in failing and v.strip()}
            if not fixed:
                break
            written.update(fixed)
            verdicts.update(await self._check(db, case.id, language, wi, written, recent,
                                              by_id, set(fixed)))

        segments = [segment_output(by_id[s["id"]], written.get(s["id"], ""), language,
                                   blueprint, verdicts.get(s["id"]), arch_mem)
                    for s in wi["segments"]]
        failed = [s["segment_id"] for s in segments if not s["quality"]["pass"]]
        status = "needs_review" if failed else "valid"
        report = {"status": status, "failed_segments": failed, "repair_iterations": repairs,
                  "host_seconds": round(sum(s["estimated_seconds"] for s in segments), 1),
                  "plan_status": plan_row.status}
        count = db.query(HostSegments).filter(
            HostSegments.story_version_id == version.id).count()
        row = HostSegments(case_id=case.id, story_version_id=version.id,
                           host_plan_id=plan_row.id, language=language, version=count + 1,
                           status=status,
                           segments_json=json.dumps(segments, ensure_ascii=False),
                           validation_json=json.dumps(report, ensure_ascii=False),
                           generation_model=getattr(res, "model", None))
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    async def _check(self, db, case_id: int, language: str, wi: dict, written: dict[str, str],
                     recent: list[dict], by_id: dict[str, dict], ids: set[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for sid in ids:
            others = [t for k, t in written.items() if k != sid]
            out[sid] = {"deterministic": dialogue_issues(
                by_id[sid], written.get(sid, ""), language, recent, others, self.cfg)}
        sub = {**wi, "segments": [s for s in wi["segments"] if s["id"] in ids]}
        raw, _ = await self._json(db, case_id, f"Host Critic {language}", "host_critic",
                                  critic_system_prompt(language), critic_input(written, sub))
        model = critic_verdicts(raw)
        for sid in ids:
            v = model.get(sid) or {"checks": {k: False for k in ("verified",)},
                                   "problems": [{"check": "not_reviewed",
                                                 "fix": "the critic returned no verdict"}],
                                   "pass": False}
            out[sid].update(checks=v["checks"], problems=v["problems"],
                            **{"pass": v["pass"] and not out[sid]["deterministic"]})
        return out
