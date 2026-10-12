"""Chapter plan construction and per-language card writing/auditing."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.agents.story import build_evidence_pack
from app.core.ai_config import ai_config
from app.core.member_ids import ensure_pipeline_member_ids
from app.db.models import Case, ChapterPlan, Contradiction, EditorialBlueprint, Fact, Source, StoryVersion
from app.documentary.chapters import AUDITOR_SYSTEM, WRITER_SYSTEM, _loads, _text, acts_of, cold_open, told_events
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run


class ChapterWriter:
    """Build a language-neutral plan, then write/audit each language alone."""

    def __init__(self, gen=None):
        self.gen = gen or get_generation_provider()

    def context(self, db: Session, case: Case, bp_row: EditorialBlueprint,
                master: StoryVersion | None, languages: list[str],
                spoken: dict[str, StoryVersion] | None = None) -> dict:
        blueprint = _loads(bp_row.blueprint_json, {})
        structure = _loads(master.narrative_structure, {}) if master else {}
        pack = build_evidence_pack(
            db.query(Fact).filter(Fact.case_id == case.id).all(),
            db.query(Contradiction).filter(Contradiction.case_id == case.id).all(),
            db.query(Source).filter(Source.case_id == case.id).all())
        # Shared chapter-plan inputs have no language list, naming/title, or
        # localized excerpt. They are acts, beats, neutral events, and breaks.
        return {
            "blueprint": blueprint,
            "structure": structure,
            "acts": acts_of(blueprint, structure),
            "events": told_events(blueprint, pack.get("timeline") or []),
            "cold_open": cold_open(blueprint),
            "case_status": getattr(case, "resolution_status", None) or "UNKNOWN",
        }

    def _payload(self, ctx: dict, language: str, spoken: StoryVersion | None,
                 fix: list[dict] | None = None) -> dict:
        payload = {
            "language": language,
            "languages": [language],
            "case_status": ctx["case_status"],
            "chapters": [{
                "act_id": a["act_id"], "number": a["number"],
                "planned_title": a["plan_title"], "about": a["purpose"],
                "reveals_in_this_chapter": a["reveals"],
                "revealed_later": [r for b in ctx["acts"][a["number"]:]
                                   for r in b["reveals"]][:12],
            } for a in ctx["acts"]],
            "events": [{"id": e["id"], "date": e["date"], "claim": e["claim"]}
                       for e in ctx["events"]],
            "narration_excerpt": (spoken.story_text[:1500]
                                  if spoken and spoken.story_text else None),
            "limits": {"title": ai_config.chapters.max_title_chars,
                       "label": ai_config.chapters.max_label_chars},
        }
        if fix:
            payload["fix"] = fix
        return payload

    def keys(self, ctx: dict) -> list[str]:
        out = ["film_title"] if ctx["cold_open"] and ai_config.chapters.title_card else []
        out += [f"chapter:{a['act_id']}" for a in ctx["acts"]]
        out += [f"event:{e['id']}" for e in ctx["events"]]
        return out

    @staticmethod
    def _parse(raw: dict, language: str) -> dict[str, str]:
        raw = raw if isinstance(raw, dict) else {}
        out: dict[str, str] = {}
        title = raw.get("film_title")
        if isinstance(title, dict):
            title = title.get(language)
        if title is not None:
            out["film_title"] = _text(title)
        for chapter in raw.get("chapters") or []:
            if not isinstance(chapter, dict) or not chapter.get("act_id"):
                continue
            value = chapter.get("title")
            if isinstance(value, dict):
                value = value.get(language)
            if value is not None:
                out[f"chapter:{chapter['act_id']}"] = _text(value)
        for event in raw.get("events") or []:
            if not isinstance(event, dict) or not event.get("id"):
                continue
            value = event.get("label")
            if isinstance(value, dict):
                value = value.get(language)
            if value is not None:
                out[f"event:{event['id']}"] = _text(value)
        return out

    @staticmethod
    def precheck(key: str, text: str | None) -> str | None:
        limit = (ai_config.chapters.max_label_chars if key.startswith("event:")
                 else ai_config.chapters.max_title_chars)
        if not text:
            return "missing"
        if len(text) > limit:
            return f"longer than {limit} characters"
        return None

    async def _write(self, db: Session, case: Case, ctx: dict, language: str,
                     spoken: StoryVersion | None, fix: list[dict] | None) -> dict[str, str]:
        system = WRITER_SYSTEM.format(max_title=ai_config.chapters.max_title_chars,
                                      max_label=ai_config.chapters.max_label_chars)
        payload = self._payload(ctx, language, spoken, fix)
        with track_run(db, case.id, "Chapter Writer",
                       input_summary=f"{len(fix) if fix else 'all'} items") as run:
            raw, res = await run_agent("documentary.chapters.write", self.gen,
                                       json.dumps(payload, ensure_ascii=False), system=system)
            stamp_run(run, res, "chapter_writer")
        return self._parse(raw, language)

    async def _audit(self, db: Session, case: Case, ctx: dict, language: str,
                     spoken: StoryVersion | None, texts: dict[str, str]) -> dict[str, dict]:
        payload = self._payload(ctx, language, spoken)
        payload["texts"] = texts
        with track_run(db, case.id, "Chapter Auditor",
                       input_summary=f"{len(texts)} items") as run:
            raw, res = await run_agent("documentary.chapters.audit", self.gen,
                                       json.dumps(payload, ensure_ascii=False), system=AUDITOR_SYSTEM)
            stamp_run(run, res, "chapter_auditor")
        return {str(v["key"]): v for v in (raw.get("verdicts") or [])
                if isinstance(v, dict) and v.get("key")} if isinstance(raw, dict) else {}

    async def create(self, db: Session, case: Case, bp_row: EditorialBlueprint,
                     master: StoryVersion | None, languages: list[str],
                     spoken: dict[str, StoryVersion] | None = None) -> ChapterPlan:
        ctx = self.context(db, case, bp_row, master, languages, spoken)
        langs = list(dict.fromkeys(languages))
        keys = self.keys(ctx)
        redos = ai_config.documentary.max_redos
        approved: dict[str, dict[str, str]] = {lang: {} for lang in langs}
        rounds: list[dict] = []
        left_out: list[dict] = []
        errors: list[str] = []

        for language in langs:
            version = (spoken or {}).get(language)
            pending = set(keys)
            drafts: dict[str, str] = {}
            reasons: dict[str, str] = {}
            language_error = None
            for attempt in range(redos + 1):
                fix = ([{"key": key, "previous": drafts.get(key), "language": language,
                          "auditor": reasons.get(key)} for key in sorted(pending)]
                       if attempt else None)
                try:
                    drafts.update(await self._write(db, case, ctx, language, version, fix))
                    verdicts = await self._audit(
                        db, case, ctx, language, version,
                        {key: drafts.get(key) for key in pending})
                except Exception as exc:
                    language_error = f"{type(exc).__name__}: {exc}"[:300]
                    errors.append(f"{language}: {language_error}")
                    break
                still: set[str] = set()
                for key in sorted(pending):
                    verdict = verdicts.get(key)
                    rejected = verdict is None or verdict.get("ok") is False
                    reason = ("the auditor did not judge it" if verdict is None else
                              str(verdict.get("reason") or "rejected")[:300])
                    pre = self.precheck(key, drafts.get(key))
                    if pre or rejected:
                        still.add(key)
                        reasons[key] = f"{language}: {pre}" if pre else reason
                    else:
                        approved[language][key] = drafts[key]
                rounds.append({"language": language, "round": attempt,
                               "approved": sorted(pending - still),
                               "rejected": sorted(still),
                               "reasons": {k: reasons[k] for k in sorted(reasons)}})
                pending = still
                if not pending:
                    break
            if pending or language_error:
                left_out.extend({"key": key, "language": language,
                                 "reason": reasons.get(key) or language_error}
                                for key in sorted(pending or keys))

        plan = {
            "languages": langs,
            "cold_open": ctx["cold_open"],
            "film_title": {lang: approved[lang].get("film_title") for lang in langs},
            "chapters": [{**a, "title": {
                lang: approved[lang].get(f"chapter:{a['act_id']}") for lang in langs}}
                for a in ctx["acts"]],
            "events": [{**event, "label": {
                lang: approved[lang].get(f"event:{event['id']}") for lang in langs}}
                for event in ctx["events"]],
        }
        plan = ensure_pipeline_member_ids(f"chapter-plan:{bp_row.id}", plan)
        status = "no_texts" if not any(approved.values()) and keys else (
            "partial" if left_out else "approved")
        count = db.query(ChapterPlan).filter(ChapterPlan.blueprint_id == bp_row.id).count()
        row = ChapterPlan(
            case_id=case.id, blueprint_id=bp_row.id, version=count + 1,
            status=status, languages_json=json.dumps(langs),
            plan_json=json.dumps(plan, ensure_ascii=False),
            audit_json=json.dumps({"rounds": rounds, "left_out": left_out,
                                   "error": "; ".join(errors) or None,
                                   "max_redos": redos}, ensure_ascii=False),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
