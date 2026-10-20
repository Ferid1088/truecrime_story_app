"""The HostDirector agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.host (and host_planning,
host_firewall, host_story)."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import (
    Case,
    EditorialBlueprint,
    HostPlan,
    HostScene,
    HostSegments,
    StoryVersion,
)
from app.documentary.host import (
    _memory_index,
    _pack,
    archive,
    critic_input,
    critic_system_prompt,
    critic_verdict,
    dialogue_issues,
    director_input,
    director_system_prompt,
    estimate_seconds,
    meaning_input,
    meaning_problems,
    memories,
    parse_writer,
    persona_prompt,
    persona_sha256,
    recent_segments,
    remember,
    segment_output,
    writer_input,
    writer_system_prompt,
)
from app.documentary.host_firewall import reveal_issues
from app.documentary.host_planning import story_seconds, validate_host_plan
from app.documentary.host_story import HostStory, load_story
from app.documentary.identity_line import ends_with_identity, identity_line, strip_identity, with_identity
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run


class HostDirector:
    def __init__(self):
        self.gen = get_generation_provider()
        self.cfg = ai_config.host

    async def _json(self, db, case_id: int, label: str, role: str, system: str,
                    payload: dict, persona: bool = True) -> tuple[dict, object]:
        """One model call. Every host prompt carries the persona, except the fact checker's,
        which judges facts only."""
        if persona and persona_prompt(self.cfg) not in system:
            raise RuntimeError(f"the host persona is missing from the {label} prompt")
        with track_run(db, case_id, label,
                       input_summary=f"{label} persona_sha256={persona_sha256(self.cfg)}") as run:
            raw, res = await run_agent(role, self.gen, json.dumps(payload, ensure_ascii=False), system=system)
            stamp_run(run, res, ai_config.agents[role].role)
        return raw, res

    @staticmethod
    def _story(db: Session, case: Case, version: StoryVersion,
               bp_row: EditorialBlueprint | None) -> HostStory:
        return load_story(db, case, version, _pack(db, case.id), bp_row)

    # -- 1. plan -------------------------------------------------------------
    async def create_plan(self, db: Session, case: Case, bp_row: EditorialBlueprint | None,
                          master: StoryVersion) -> HostPlan:
        """The structure of the host's appearances, once for all languages. `bp_row` is the
        story's blueprint, or None for a story written to a story plan."""
        if bp_row is not None and bp_row.status == "invalid":
            raise RuntimeError("The blueprint is invalid; fix it before planning the host.")
        story = self._story(db, case, master, bp_row)
        narration = story_seconds(story)
        payload = director_input(story, narration)
        system = director_system_prompt(self.cfg, narration)

        raw, res = await self._json(db, case.id, "Host Director", "host_director", system, payload)
        plan, report = validate_host_plan(raw, story, self.cfg)
        repairs = 0
        while report["errors"] and repairs < self.cfg.max_repair_iterations:
            repairs += 1
            raw, res = await self._json(
                db, case.id, f"Host Director repair {repairs}", "host_director", system,
                {**payload, "previous_plan": raw, "errors_to_fix": report["errors"]})
            plan, report = validate_host_plan(raw, story, self.cfg)
        report["repair_iterations"] = repairs
        query = db.query(HostPlan)
        count = (query.filter(HostPlan.blueprint_id == bp_row.id).count() if bp_row is not None
                 else query.filter(HostPlan.story_plan_id == master.story_plan_id).count())
        row = HostPlan(case_id=case.id, blueprint_id=bp_row.id if bp_row is not None else None,
                       story_plan_id=None if bp_row is not None else master.story_plan_id,
                       version=count + 1, status=report["status"],
                       persona_file=self.cfg.persona_file, persona_sha256=persona_sha256(self.cfg),
                       plan_json=json.dumps(plan, ensure_ascii=False),
                       validation_json=json.dumps(report, ensure_ascii=False),
                       generation_model=getattr(res, "model", None))
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    # -- 2.+3. dialogue per language ----------------------------------------
    def _plan_story(self, db: Session, case: Case, version: StoryVersion,
                    plan_row: HostPlan) -> HostStory:
        bp_row = db.get(EditorialBlueprint, plan_row.blueprint_id) if plan_row.blueprint_id else None
        return self._story(db, case, version, bp_row)

    async def write(self, db: Session, case: Case, version: StoryVersion,
                    plan_row: HostPlan) -> HostSegments:
        """The host's dialogue in the version's language: one appearance after the other, in film
        order, each from the story up to its own cutoff."""
        if plan_row.status == "invalid":
            raise RuntimeError("The host plan is invalid.")
        language = version.language or "en"
        plan = json.loads(plan_row.plan_json or "{}")
        story = self._plan_story(db, case, version, plan_row)
        arch_mem = _memory_index(archive(db, case.id), memories(db, case.id))
        recent = recent_segments(db, case.id, language)
        written: dict[str, dict] = {}
        verdicts: dict[str, dict] = {}
        notes: list[dict] = []
        repairs = 0
        res = None
        for seg in plan.get("segments") or []:
            earlier = [w["dialogue"] for w in written.values() if w["dialogue"]]
            wi = writer_input(story, seg, language, arch_mem, recent, earlier, self.cfg)
            raw, res = await self._json(db, case.id, f"Host Writer {language} {seg['id']}",
                                        self.cfg.writer_agents[language],
                                        writer_system_prompt(language, self.cfg), wi)
            current = parse_writer(raw)
            verdict = await self._check(db, case.id, story, seg, wi, current, recent, earlier,
                                        arch_mem)
            tries = 0
            while not verdict["pass"] and tries < self.cfg.max_writer_repairs:
                tries += 1
                repairs += 1
                fix = {**wi, "previous_dialogue": current["dialogue"],
                       "problems_to_fix": verdict["problems"] + verdict["deterministic"]}
                raw, res = await self._json(
                    db, case.id, f"Host Writer {language} {seg['id']} repair {tries}",
                    self.cfg.writer_agents[language], writer_system_prompt(language, self.cfg), fix)
                fixed = parse_writer(raw)
                if not fixed["dialogue"]:
                    break
                current = fixed
                verdict = await self._check(db, case.id, story, seg, wi, current, recent, earlier,
                                            arch_mem)
            written[seg["id"]], verdicts[seg["id"]] = current, verdict
            if current["memory_note"] and verdict["pass"]:
                notes.append({**current["memory_note"], "segment_id": seg["id"]})

        segments = [segment_output(s, written[s["id"]], language, story, verdicts[s["id"]], arch_mem)
                    for s in plan.get("segments") or []]
        failed = [s["segment_id"] for s in segments if not s["quality"]["pass"]]
        status = "needs_review" if failed else "valid"
        report = {"status": status, "failed_segments": failed, "repair_iterations": repairs,
                  "host_seconds": round(sum(s["estimated_seconds"] for s in segments), 1),
                  "planned_seconds": round(sum(s["target_seconds"] for s in segments), 1),
                  "plan_status": plan_row.status}
        count = db.query(HostSegments).filter(
            HostSegments.story_version_id == version.id).count()
        row = HostSegments(case_id=case.id, story_version_id=version.id,
                           host_plan_id=plan_row.id, language=language, version=count + 1,
                           status=status,
                           persona_file=self.cfg.persona_file,
                           persona_sha256=persona_sha256(self.cfg),
                           segments_json=json.dumps(segments, ensure_ascii=False),
                           validation_json=json.dumps(report, ensure_ascii=False),
                           generation_model=getattr(res, "model", None))
        db.add(row)
        db.commit()
        db.refresh(row)
        if language == ai_config.language_pipeline.draft_language and notes:
            remember(db, case.id, plan_row, notes)
        return row

    async def _check(self, db, case_id: int, story: HostStory, seg: dict, wi: dict, written: dict,
                     recent: list[dict], earlier: list[str], arch_mem: dict[str, dict]) -> dict:
        """Deterministic checks first; the critic judges only a dialogue that passed them."""
        language = wi["language"]
        dialogue = written["dialogue"]
        ref = written["memory_used"]
        memory_text = (arch_mem[ref].get("text") or arch_mem[ref].get("central_question") or ""
                       if ref in arch_mem else "")
        deterministic = dialogue_issues(seg, dialogue, language, recent, earlier, self.cfg)
        deterministic += reveal_issues(story, seg["cutoff"], dialogue, memory_text, self.cfg)
        if ref and ref not in arch_mem:
            deterministic.append({"type": "unverified_memory", "ref": ref})
        if deterministic:
            return {"checks": {}, "problems": [], "deterministic": deterministic, "pass": False}
        raw, _ = await self._json(db, case_id, f"Host Critic {language} {seg['id']}", "host_critic",
                                  critic_system_prompt(language), critic_input(wi, dialogue))
        verdict = critic_verdict(raw)
        problems = list(verdict["problems"])
        if language in self.cfg.meaning_check_languages:
            raw, _ = await self._json(db, case_id, f"Host Meaning {language} {seg['id']}",
                                      "host_meaning_checker", prompt("documentary/host/meaning_system"),
                                      meaning_input(wi, dialogue), persona=False)
            problems += meaning_problems(raw, seg["id"])
        return {"checks": verdict["checks"], "problems": problems, "deterministic": [],
                "pass": verdict["pass"] and len(problems) == len(verdict["problems"])}

    # -- real-length repair ----------------------------------------------------
    async def retime_scene(self, db: Session, scene: HostScene, target_seconds: float,
                           avoid: Sequence[str] = ()) -> None:
        """The real voice of this appearance misses its planned seconds: the writer rewrites the
        dialogue at the length the measured speaking rate needs, from the same cutoff input, and
        the scene (and its stored segment) takes the new text. Bounded by the caller."""
        from app.documentary.host_scenes import _sha

        seg_row = db.get(HostSegments, scene.host_segments_id)
        assert seg_row is not None
        version = db.get(StoryVersion, seg_row.story_version_id)
        plan_row = db.get(HostPlan, seg_row.host_plan_id)
        case = db.get(Case, scene.case_id)
        assert version and plan_row and case, "the appearance belongs to a stored plan"
        story = self._plan_story(db, case, version, plan_row)
        plan = json.loads(plan_row.plan_json or "{}")
        seg = next(s for s in plan["segments"] if s["id"] == scene.host_segment_id)
        arch_mem = _memory_index(archive(db, case.id), memories(db, case.id))
        recent = recent_segments(db, case.id, scene.language)
        segments = json.loads(seg_row.segments_json or "[]")
        earlier = [s["avatar_dialogue"] for s in segments
                   if s["segment_id"] != scene.host_segment_id and s.get("avatar_dialogue")
                   and s["cutoff"] <= seg["cutoff"]]
        wi = writer_input(story, seg, scene.language, arch_mem, recent, earlier, self.cfg)
        # the fixed identity line closing an opening is never rewritten: only the words before it
        # are, to the seconds left for them
        fixed = seg["position"] == "opening" and ends_with_identity(scene.text, scene.language)
        body = strip_identity(scene.text, scene.language) if fixed else scene.text
        identity_seconds = estimate_seconds(identity_line(scene.language), scene.language) if fixed else 0.0
        words = len(body.split())
        measured = max((scene.voice_seconds or estimate_seconds(scene.text, scene.language))
                       - identity_seconds, 1.0)
        target_seconds = target_seconds - identity_seconds
        needed = max(int(round(words * target_seconds / measured)), 1)
        problems = [{"check": "pronunciation", "quote": ", ".join(avoid),
                     "fix": "The voice says these words wrongly even with vowel marks. Say the "
                            "same thing with other words (a synonym or another phrasing); keep "
                            "names as they are."}] if avoid else []
        fix = {**wi, "previous_dialogue": body, "problems_to_fix": problems + [{
            "check": "length", "quote": "",
            "fix": (f"This dialogue was spoken in {measured:.1f} s; the appearance must take "
                    f"{target_seconds:.0f} s. Rewrite it with {int(needed * 0.96)} to {int(needed * 1.04) + 1} words: "
                    f"{'cut the least important sentences' if needed < words else 'add one more thought from the acts you were given'}; "
                    "keep its voice, its address and its claims, add no new facts.")}]}
        text = ""
        for _attempt in range(1 + self.cfg.max_writer_repairs):
            raw, _ = await self._json(
                db, scene.case_id, f"Host Writer {scene.language} retime",
                self.cfg.writer_agents[scene.language],
                writer_system_prompt(scene.language, self.cfg), fix)
            text = parse_writer(raw)["dialogue"]
            if not text or text == body:
                raise RuntimeError("the writer did not change the host dialogue")
            issues = [i for i in dialogue_issues(seg, text, scene.language, recent, earlier,
                                                 self.cfg)
                      if i["type"] not in ("too_short", "too_long")]
            issues += reveal_issues(story, seg["cutoff"], text, cfg=self.cfg)
            if not issues:
                break
            fix = {**fix, "previous_dialogue": text,
                   "problems_to_fix": fix["problems_to_fix"][:len(problems) + 1] + issues}
        else:
            raise RuntimeError(f"the retimed host dialogue fails its checks: {issues[0]['type']}")
        if fixed:
            text = with_identity(text, scene.language)
        for entry in segments:
            if entry["segment_id"] == scene.host_segment_id:
                entry["avatar_dialogue"] = text
                entry["estimated_seconds"] = estimate_seconds(text, scene.language)
        seg_row.segments_json = json.dumps(segments, ensure_ascii=False)
        scene.text, scene.text_sha256 = text, _sha(text)
        db.commit()
