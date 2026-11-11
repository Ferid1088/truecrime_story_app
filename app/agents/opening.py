"""The agents of the film's opening block: the English director (hook, context, music brief) and the native writer of every other language."""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import Case, EditorialBlueprint, FilmOpening, StoryVersion
from app.documentary import opening_block as OB
from app.documentary.host import _pack
from app.documentary.host_firewall import open_questions, visible_facts
from app.documentary.host_story import HostStory, load_story
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

_QUOTE = re.compile(r"[\"“„«]([^\"”“»]{12,300})[\"”“»]")


def quotes_of(text: str) -> list[str]:
    """What people said in a text, between quotation marks, as the text gives it."""
    return [m.group(1).strip() for m in _QUOTE.finditer(text or "")]


class OpeningWriter:
    def __init__(self):
        self.gen = get_generation_provider()
        self.cfg = ai_config.opening_block_settings()

    async def _json(self, db: Session, case_id: int, label: str, role: str, system: str, payload: dict):
        with track_run(db, case_id, label, input_summary=label) as run:
            raw, res = await run_agent(role, self.gen, json.dumps(payload, ensure_ascii=False), system=system)
            stamp_run(run, res, ai_config.agents[role].role)
        return raw, res

    def _story(self, db: Session, case: Case, version: StoryVersion, bp_row: EditorialBlueprint | None) -> HostStory:
        return load_story(db, case, version, _pack(db, case.id), bp_row)

    def _first_act(self, story: HostStory) -> str:
        return " ".join(a.text for a in story.visible(max(self.cfg.knowledge_acts, 1)))

    def _store(self, db: Session, case: Case, version: StoryVersion, language: str, kind: str, opening: dict, issues: list[dict],
               repairs: int, model: str | None, master_id: int | None = None) -> FilmOpening:
        count = db.query(FilmOpening).filter(FilmOpening.case_id == case.id, FilmOpening.language == language).count()
        row = FilmOpening(case_id=case.id, language=language, kind=kind, story_version_id=version.id, master_opening_id=master_id,
                          version=count + 1, status="valid" if not issues else "needs_review",
                          opening_json=json.dumps(opening, ensure_ascii=False),
                          validation_json=json.dumps({"issues": issues, "repair_iterations": repairs}, ensure_ascii=False),
                          generation_model=model)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    async def create_master(self, db: Session, case: Case, version: StoryVersion, bp_row: EditorialBlueprint | None = None) -> FilmOpening:
        """The English opening: hook candidates weighed and one chosen, the context text, the music brief; checked and written again while it has issues."""
        story = self._story(db, case, version, bp_row)
        language = version.language or "en"
        told = self._first_act(story)
        payload = {
            "case": story.case_title, "central_question": story.central_question, "open_questions": open_questions(story, 0),
            "story_first_act": told, "facts_you_may_use": visible_facts(story, self.cfg.knowledge_acts),
            "later_titles": [a.title for a in story.hidden(self.cfg.knowledge_acts)], "quotes": quotes_of(told),
            "limits": {"hook_max_words": OB.hook_max_words(language, self.cfg), "context_min_words": OB.context_limits(language, self.cfg)[0],
                       "context_max_words": OB.context_limits(language, self.cfg)[1], "candidates": self.cfg.hook_candidates}}
        system = prompt("documentary/opening/director_system")
        raw, res = await self._json(db, case.id, "Opening Director", "opening_director", system, payload)
        opening, problems = OB.parse_master(raw, self.cfg)
        issues = problems + (OB.opening_issues(opening, story, language, self.cfg) if not problems else [])
        repairs = 0
        while issues and repairs < self.cfg.repairs:
            repairs += 1
            raw, res = await self._json(db, case.id, f"Opening Director repair {repairs}", "opening_director", system,
                                        {**payload, "previous": raw, "problems_to_fix": issues})
            opening, problems = OB.parse_master(raw, self.cfg)
            issues = problems + (OB.opening_issues(opening, story, language, self.cfg) if not problems else [])
        return self._store(db, case, version, language, "master", opening, issues, repairs, getattr(res, "model", None))

    async def write_native(self, db: Session, case: Case, version: StoryVersion, master: FilmOpening,
                           bp_row: EditorialBlueprint | None = None) -> FilmOpening:
        """The opening of one language, written again in it from the English opening and this language's own story."""
        story = self._story(db, case, version, bp_row)
        language = version.language or "en"
        english = json.loads(master.opening_json or "{}")
        payload = {
            "language": language, "case": story.case_title,
            "limits": {"hook_max_words": OB.hook_max_words(language, self.cfg), "context_min_words": OB.context_limits(language, self.cfg)[0],
                       "context_max_words": OB.context_limits(language, self.cfg)[1]},
            "english_opening": {"hook": english.get("hook"), "context": english.get("context")},
            "story_first_act": self._first_act(story),
            "names": [list(e.spellings) for e in story.entities]}
        system = prompt("documentary/opening/native_system")
        raw, res = await self._json(db, case.id, f"Opening Native Writer ({language})", "opening_native_writer", system, payload)
        opening = OB.parse_native(raw, english)
        issues = OB.opening_issues(opening, story, language, self.cfg)
        repairs = 0
        while issues and repairs < self.cfg.repairs:
            repairs += 1
            raw, res = await self._json(db, case.id, f"Opening Native Writer ({language}) repair {repairs}", "opening_native_writer", system,
                                        {**payload, "previous": raw, "problems_to_fix": issues})
            opening = OB.parse_native(raw, english)
            issues = OB.opening_issues(opening, story, language, self.cfg)
        return self._store(db, case, version, language, "native", opening, issues, repairs, getattr(res, "model", None), master.id)
