"""The agents of the short clips: the English director plans the clips of a film from its English spoken text; the native writer of every other language
writes them again in its language (not a translation)."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.agents.opening import quotes_of
from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import Case, ShortClip, StoryVersion
from app.documentary.blueprint import latest_blueprint
from app.documentary.host import _pack
from app.documentary.host_firewall import open_questions, visible_facts
from app.documentary.host_story import HostStory, load_story
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.shortform import clips as C


class ClipPlanner:
    def __init__(self):
        self.gen = get_generation_provider()
        self.cfg = ai_config.short_clips_settings()

    async def _json(self, db: Session, case_id: int, label: str, role: str, system: str, payload: dict):
        with track_run(db, case_id, label, input_summary=label) as run:
            raw, res = await run_agent(role, self.gen, json.dumps(payload, ensure_ascii=False), system=system)
            stamp_run(run, res, ai_config.agents[role].role)
        return raw, res

    def _story(self, db: Session, case: Case, version: StoryVersion) -> HostStory:
        bp = latest_blueprint(db, version.master_version_id) if version.master_version_id else None
        return load_story(db, case, version, _pack(db, case.id), bp)

    def _limits(self, language: str) -> dict:
        return {"max_clips": self.cfg.max_clips, "min_clips": self.cfg.min_clips, "angles": self.cfg.angles,
                "words": {part: list(C.words_for(part, language, self.cfg)) for part in ("hook", "context", "detail", "reaction")},
                "cta_max_words": self.cfg.cta_max_words}

    def _problems(self, clips: list[dict], story: HostStory, language: str) -> dict[str, list[dict]]:
        found = {c.get("clip_key") or f"#{i}": C.clip_issues(c, story, language, self.cfg) for i, c in enumerate(clips)}
        if extra := C.set_issues(clips, self.cfg):
            found["_set"] = extra
        return {k: v for k, v in found.items() if v}

    def _store(self, db: Session, case: Case, version: StoryVersion, language: str, kind: str, clips: list[dict], problems: dict, repairs: int,
               model: str | None, masters: dict[str, ShortClip] | None = None) -> list[ShortClip]:
        rows = []
        for clip in clips:
            key = clip["clip_key"]
            count = db.query(ShortClip).filter(ShortClip.case_id == case.id, ShortClip.language == language, ShortClip.clip_key == key).count()
            issues = problems.get(key, []) + problems.get("_set", [])
            rows.append(ShortClip(case_id=case.id, language=language, clip_key=key, kind=kind, story_version_id=version.id,
                                  master_clip_id=masters[key].id if masters and key in masters else None, version=count + 1,
                                  status="valid" if not issues else "needs_review", script_json=json.dumps(clip, ensure_ascii=False),
                                  validation_json=json.dumps({"issues": issues, "repair_iterations": repairs}, ensure_ascii=False), generation_model=model))
        db.add_all(rows)
        db.commit()
        for r in rows:
            db.refresh(r)
        return rows

    async def plan_english(self, db: Session, case: Case, version: StoryVersion) -> list[ShortClip]:
        """The English clips: planned from the English spoken text, checked, written again while a clip has issues."""
        story = self._story(db, case, version)
        language = version.language or "en"
        cutoff = C.knowledge_cutoff(story)
        told = story.visible(cutoff)
        payload = {
            "case": story.case_title, "central_question": story.central_question, "open_questions": open_questions(story, 0),
            "acts_you_may_use": [{"act": a.id, "title": a.title, "narration": a.text} for a in told],
            "facts_you_may_use": visible_facts(story, cutoff), "answer_acts": [a.title for a in story.hidden(cutoff)],
            "quotes": [q for a in told for q in quotes_of(a.text)][:30], "limits": self._limits(language)}
        system = prompt("shortform/clip_director")
        raw, res = await self._json(db, case.id, "Short Clip Director", "short_clip_director", system, payload)
        clips = C.parse_clips(raw)[:self.cfg.max_clips]
        problems, repairs = self._problems(clips, story, language), 0
        while problems and repairs < self.cfg.repairs:
            repairs += 1
            raw, res = await self._json(db, case.id, f"Short Clip Director repair {repairs}", "short_clip_director", system,
                                        {**payload, "previous": raw, "problems_to_fix": problems})
            clips = C.parse_clips(raw)[:self.cfg.max_clips]
            problems = self._problems(clips, story, language)
        return self._store(db, case, version, language, "master", clips, problems, repairs, getattr(res, "model", None))

    async def write_native(self, db: Session, case: Case, version: StoryVersion, masters: list[ShortClip]) -> list[ShortClip]:
        """One language's clips, written again in it from the English clips of the valid masters."""
        story = self._story(db, case, version)
        language = version.language or "en"
        english = [json.loads(m.script_json) for m in masters]
        told = " ".join(a.text for a in story.visible(C.knowledge_cutoff(story)))
        payload = {
            "language": language, "case": story.case_title, "limits": self._limits(language),
            "english_clips": [{k: c.get(k) for k in ("clip_key", "angle", "idea", *C.TEXT_PARTS)} for c in english],
            "story": " ".join(told.split()[:250]), "names": [list(e.spellings) for e in story.entities]}
        system = prompt("shortform/clip_native")
        raw, res = await self._json(db, case.id, f"Short Clip Native Writer ({language})", "short_clip_native_writer", system, payload)

        def own(raw_: dict) -> list[dict]:
            by_key = {m.clip_key: json.loads(m.script_json) for m in masters}
            out = []
            for c in C.parse_clips(raw_):
                if c.get("clip_key") in by_key:           # the idea, the angle and the evidence of a clip are the English clip's
                    base = by_key[c["clip_key"]]
                    out.append({**c, "angle": base.get("angle"), "idea": c.get("idea") or base.get("idea"),
                                "evidence_ids": base.get("evidence_ids", []), "act_ids": base.get("act_ids", [])})
            return out

        clips = own(raw)
        problems, repairs = self._problems_native(clips, story, language, masters), 0
        while problems and repairs < self.cfg.repairs:
            repairs += 1
            raw, res = await self._json(db, case.id, f"Short Clip Native Writer ({language}) repair {repairs}", "short_clip_native_writer", system,
                                        {**payload, "previous": raw, "problems_to_fix": problems})
            clips = own(raw)
            problems = self._problems_native(clips, story, language, masters)
        return self._store(db, case, version, language, "native", clips, problems, repairs, getattr(res, "model", None), {m.clip_key: m for m in masters})

    def _problems_native(self, clips: list[dict], story: HostStory, language: str, masters: list[ShortClip]) -> dict[str, list[dict]]:
        found = {c["clip_key"]: C.clip_issues(c, story, language, self.cfg) for c in clips}
        missing = {m.clip_key for m in masters} - set(found)
        for key in missing:
            found[key] = [{"type": "missing_clip", "fix": "Write this clip too."}]
        return {k: v for k, v in found.items() if v}
