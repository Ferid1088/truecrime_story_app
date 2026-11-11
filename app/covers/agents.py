"""The cover planner: for one language it writes the title of the film's cover and of each short's cover and chooses the scene (a picture the film shows) that fits
each title, with a reason and its certainty. It proposes; it never approves. The English covers are planned first; every other language writes its titles again
in its own language (not a translation) with the English covers as a reference."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.covers import proposals as P
from app.covers import scene as SC
from app.db.models import Case, CoverProposal, EpisodeIdentity, StoryVersion
from app.documentary.blueprint import latest_blueprint
from app.documentary.host import _pack
from app.documentary.host_firewall import open_questions
from app.documentary.host_story import HostStory, load_story
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.shortform import clips as C


class CoverPlanner:
    def __init__(self):
        self.gen = get_generation_provider()
        self.cfg = ai_config.covers_settings().proposal

    def _story(self, db: Session, case: Case, version: StoryVersion) -> HostStory:
        bp = latest_blueprint(db, version.master_version_id) if version.master_version_id else None
        return load_story(db, case, version, _pack(db, case.id), bp)

    async def propose(self, db: Session, case: Case, language: str) -> list[CoverProposal]:
        """The proposals of one language, written as new rows (version after the earlier ones). The language needs its short clips; its scenes are the
        pictures its film shows."""
        clips = C.clips_of(db, case.id, language)
        if not clips:
            raise P.ProposalError(f"{language} has no short clips yet: the covers follow them")
        version = db.get(StoryVersion, clips[0].story_version_id)
        story = self._story(db, case, version)
        options = SC.candidates(db, case, language)[:self.cfg["max_candidates"]]
        keys = P.keys_of([c.clip_key for c in clips])
        cutoff = C.knowledge_cutoff(story)
        identity = db.query(EpisodeIdentity).filter(EpisodeIdentity.case_id == case.id, EpisodeIdentity.language == language).first()
        payload = {
            "language": language,
            "film": {"title": (identity.editorial_title if identity and identity.editorial_title else story.case_title), "central_question": story.central_question,
                     "open_questions": open_questions(story, 0)},
            "answer_acts": [a.title for a in story.hidden(cutoff)],
            "covers": [{"key": "long", "what": "the long film"}] + [
                {"key": c.clip_key, "angle": json.loads(c.script_json).get("angle"), "idea": json.loads(c.script_json).get("idea"), "hook": json.loads(c.script_json).get("hook")}
                for c in clips],
            "candidates": [{"code": o["code"], "kind": o["kind"], "what": o["what"]} for o in options],
            "limits": {"title_words": ai_config.covers_settings().title, "reason_max_words": self.cfg["reason_max_words"]}}
        if language != "en":
            payload["english_covers"] = [{"key": k, "title": r.title, "scene_code": r.scene_code} for k, r in P.current(db, case.id, "en").items()]
        system = prompt("covers/cover_planner")
        codes = {o["code"] for o in options}

        async def ask(extra: dict):
            with track_run(db, case.id, f"Cover Planner ({language})", input_summary=language) as run:
                raw, res = await run_agent("cover_planner", self.gen, json.dumps({**payload, **extra}, ensure_ascii=False), system=system)
                stamp_run(run, res, ai_config.agents["cover_planner"].role)
            return raw, res

        def problems_of(items: list[dict]) -> dict[str, list[dict]]:
            found = {i["key"] or f"#{n}": P.item_issues(i, codes, story, cutoff, language) for n, i in enumerate(items)}
            if extra := P.set_issues(items, keys, len(codes)):
                found["_set"] = extra
            return {k: v for k, v in found.items() if v}

        raw, res = await ask({})
        items, repairs = P.parse_items(raw), 0
        problems = problems_of(items)
        while problems and repairs < self.cfg["repairs"]:
            repairs += 1
            raw, res = await ask({"previous": raw, "problems_to_fix": problems})
            items = P.parse_items(raw)
            problems = problems_of(items)
        return self._store(db, case, language, keys, items, problems, repairs, getattr(res, "model", None), options)

    def _store(self, db: Session, case: Case, language: str, keys: list[str], items: list[dict], problems: dict, repairs: int, model: str | None,
               options: list[dict]) -> list[CoverProposal]:
        by_key = {i["key"]: i for i in items}
        earlier = P.current(db, case.id, language)
        rows = []
        for key in keys:
            item = by_key.get(key) or {"key": key, "title": "", "scene_code": None, "confidence": None, "reason": ""}
            issues = problems.get(key, []) + problems.get("_set", [])
            status = P.status_of(item, issues)
            suggested = item["scene_code"]
            scene = suggested if status == "proposed" else None          # an uncertain scene is only a suggestion: the cover stays open
            rows.append(CoverProposal(
                case_id=case.id, language=language, cover_key=key, kind="long" if key == "long" else "short", version=(earlier[key].version + 1) if key in earlier else 1,
                source="agent", status=status, title=item["title"], scene_code=scene, confidence=item["confidence"] if suggested else None, reason=item["reason"],
                validation_json=json.dumps({"issues": issues, "repair_iterations": repairs, "suggested_scene": suggested, "candidates": [o["code"] for o in options]},
                                           ensure_ascii=False), generation_model=model))
        db.add_all(rows)
        db.commit()
        for r in rows:
            db.refresh(r)
        return rows
