"""Chapter plan construction and per-language card writing/auditing."""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.agents.story import build_evidence_pack
from app.core.ai_config import ai_config
from app.core.member_ids import ensure_pipeline_member_ids
from app.db.models import Case, ChapterPlan, Contradiction, EditorialBlueprint, Fact, Source, StoryVersion
from app.documentary.chapters import AUDITOR_SYSTEM, WRITER_SYSTEM, _loads, _text, acts_of, cold_open, safe_beats, story_beats, told_events, youtube_count
from app.core.prompts import prompt
from app.documentary import youtube_chapters as YT
from app.documentary import youtube_description as YD
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run


ALL_TASKS = frozenset({"cards", "chapters", "description"})


def _enabled(tasks: frozenset[str] | None) -> frozenset[str]:
    """The tasks of a call: the ones asked for (all by default) that the configuration has switched on."""
    wanted = ALL_TASKS if tasks is None else frozenset(tasks)
    return frozenset(t for t in wanted if t == "cards" or (t == "chapters" and ai_config.chapters.youtube.enabled) or (t == "description" and ai_config.description_settings().enabled))


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
            "beats": story_beats(blueprint) if ai_config.chapters.youtube.enabled or ai_config.description_settings().enabled else [],
            "case_block": self._case_block(case),
        }

    @staticmethod
    def _case_block(case: Case) -> dict:
        """What the description writer may name: the case's own record, compact. Never the film's central question or thesis: they hold what the story withholds."""
        listed = YD.TD._list
        return {"name": case.canonical_title, "also_known_as": listed(case.aliases_json)[:3], "people": listed(case.people_json)[:4], "location": case.location,
                "country": case.country, "date": case.incident_date, "status": case.resolution_status or "UNKNOWN"}

    def _payload(self, ctx: dict, language: str, spoken: StoryVersion | None,
                 fix: list[dict] | None = None, tasks: frozenset[str] | None = None,
                 auditor: bool = False) -> dict:
        tasks = _enabled(tasks)
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
        if "cards" not in tasks:
            payload.pop("chapters"), payload.pop("events")
        beats = ctx.get("beats") or []
        if beats and ("chapters" in tasks or (auditor and "description" in tasks)):
            payload["story_beats"] = beats          # the writer needs all beats only for the chapters; the auditor needs them to see what a description would give away
        if beats and "chapters" in tasks:
            payload["story_chapters"] = {"count": youtube_count(len(beats))}
        if beats and "description" in tasks:
            cfg = ai_config.description_settings()
            payload["description"] = {"case": ctx["case_block"], "words": cfg.target_words, "beats": safe_beats(beats)}
            if not auditor:
                example = json.loads(prompt("documentary/chapters/description_examples")).get(language)
                if example:
                    payload["description"]["tone"], payload["description"]["example"] = example["tone"], example["text"]
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
        if isinstance(raw.get("description"), str):
            out["description"] = YD.clean_narrative(raw["description"])
        for item in raw.get("story_chapters") or []:
            if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[0], str):
                out[f"story_chapter:{item[0]}"] = _text(item[1])
        return out

    @staticmethod
    def precheck(key: str, text: str | None, language: str | None = None) -> str | None:
        limit = (ai_config.chapters.max_label_chars if key.startswith("event:")
                 else ai_config.chapters.max_title_chars)
        if not text:
            return "missing"
        if key == "description":
            cfg = ai_config.description_settings()
            words = len(text.split())
            if not cfg.min_words <= words <= cfg.max_words:
                return f"{words} words; write between {cfg.min_words} and {cfg.max_words}"
            return YD.reveal_issue(text, language) if language else None
        if len(text) > limit:
            return f"longer than {limit} characters"
        if key.startswith("event:") and re.search(r"[\"\u201c\u201d\u201e\u00ab\u00bb]", text):
            return "an event label has no quotation marks: say what was said or written, not the words"
        return None

    async def _write(self, db: Session, case: Case, ctx: dict, language: str,
                     spoken: StoryVersion | None, fix: list[dict] | None,
                     tasks: frozenset[str] | None = None) -> tuple[dict[str, str], dict | None]:
        tasks = _enabled(tasks)
        system = WRITER_SYSTEM.format(max_title=ai_config.chapters.max_title_chars,
                                      max_label=ai_config.chapters.max_label_chars)
        payload = self._payload(ctx, language, spoken, fix, tasks)
        with track_run(db, case.id, "Chapter Writer",
                       input_summary=f"{len(fix) if fix else 'all'} items") as run:
            raw, res = await run_agent("documentary.chapters.write", self.gen,
                                       json.dumps(payload, ensure_ascii=False), system=system)
            stamp_run(run, res, "chapter_writer")
        extra_in = payload if "cards" not in tasks else {k: payload[k] for k in ("story_beats", "story_chapters", "description") if k in payload}
        raw_dict = raw if isinstance(raw, dict) else {}
        extra_out = raw if "cards" not in tasks else {k: raw_dict[k] for k in ("story_chapters", "description") if k in raw_dict}
        return self._parse(raw, language), YT.extra_call("chapter writer", run, res, extra_in, extra_out)

    async def _audit(self, db: Session, case: Case, ctx: dict, language: str,
                     spoken: StoryVersion | None, texts: dict[str, str],
                     tasks: frozenset[str] | None = None) -> tuple[dict[str, dict], dict | None]:
        tasks = _enabled(tasks)
        payload = self._payload(ctx, language, spoken, tasks=tasks, auditor=True)
        payload["texts"] = texts
        with track_run(db, case.id, "Chapter Auditor",
                       input_summary=f"{len(texts)} items") as run:
            raw, res = await run_agent("documentary.chapters.audit", self.gen,
                                       json.dumps(payload, ensure_ascii=False), system=AUDITOR_SYSTEM)
            stamp_run(run, res, "chapter_auditor")
        verdicts = ({str(v["key"]): v for v in (raw.get("verdicts") or [])
                     if isinstance(v, dict) and v.get("key")} if isinstance(raw, dict) else {})
        youtube = {k: t for k, t in texts.items() if k.startswith("story_chapter:") or k == "description"}
        extra_in = payload if "cards" not in tasks else [{k: payload[k] for k in ("story_beats", "story_chapters", "description") if k in payload}, youtube]
        extra_out = list(verdicts.values()) if "cards" not in tasks else [v for k, v in verdicts.items() if k in youtube]
        return verdicts, YT.extra_call("chapter auditor", run, res, extra_in, extra_out)

    async def _language(self, db: Session, case: Case, ctx: dict, language: str,
                        version: StoryVersion | None, keys: list[str],
                        tasks: frozenset[str] | None = None) -> dict:
        """One language: write, audit and redo (at most max_redos times) until every text is approved or left out. The YouTube chapters
        and the description the writer adds join the items from the first round on."""
        redos = ai_config.documentary.max_redos
        approved: dict[str, str] = {}
        rounds: list[dict] = []
        calls: list[dict] = []
        pending = set(keys)
        drafts: dict[str, str] = {}
        reasons: dict[str, str] = {}
        error = None
        for attempt in range(redos + 1):
            fix = ([{"key": key, "previous": drafts.get(key), "language": language,
                      "auditor": reasons.get(key)} for key in sorted(pending)]
                   if attempt else None)
            try:
                written, call = await self._write(db, case, ctx, language, version, fix, tasks)
                drafts.update(written)
                calls.append(call)
                if attempt == 0:
                    pending |= {key for key in drafts if key.startswith("story_chapter:") or key == "description"}
                verdicts, call = await self._audit(
                    db, case, ctx, language, version,
                    {key: drafts.get(key) or "" for key in pending}, tasks)
                calls.append(call)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:300]
                break
            still: set[str] = set()
            for key in sorted(pending):
                verdict = verdicts.get(key)
                rejected = verdict is None or verdict.get("ok") is False
                reason = ("the auditor did not judge it" if verdict is None else
                          str(verdict.get("reason") or "rejected")[:300])
                pre = self.precheck(key, drafts.get(key), language)
                if pre or rejected:
                    still.add(key)
                    reasons[key] = f"{language}: {pre}" if pre else reason
                else:
                    approved[key] = drafts[key]
            rounds.append({"language": language, "round": attempt,
                           "approved": sorted(pending - still),
                           "rejected": sorted(still),
                           "reasons": {k: reasons[k] for k in sorted(reasons)}})
            pending = still
            if not pending:
                break
        left_out = ([{"key": key, "language": language, "reason": reasons.get(key) or error}
                     for key in sorted(pending or keys)] if pending or error else [])
        return {"approved": approved, "rounds": rounds, "left_out": left_out, "error": error,
                "calls": [c for c in calls if c]}

    @staticmethod
    def _story_chapters(ctx: dict, approved: dict[str, str]) -> list[list[str]]:
        """The approved YouTube chapters of a language in film order: [[beat id, title]]."""
        order = {b[0]: i for i, b in enumerate(ctx["beats"])}
        found = [[key.split(":", 1)[1], text] for key, text in approved.items() if key.startswith("story_chapter:")]
        return sorted((c for c in found if c[0] in order), key=lambda c: order[c[0]])

    async def create(self, db: Session, case: Case, bp_row: EditorialBlueprint,
                     master: StoryVersion | None, languages: list[str],
                     spoken: dict[str, StoryVersion] | None = None) -> ChapterPlan:
        ctx = self.context(db, case, bp_row, master, languages, spoken)
        langs = list(dict.fromkeys(languages))
        keys = self.keys(ctx)
        results = {language: await self._language(db, case, ctx, language, (spoken or {}).get(language), keys)
                   for language in langs}
        approved = {language: r["approved"] for language, r in results.items()}
        left_out = [x for r in results.values() for x in r["left_out"]]
        errors = [f"{language}: {r['error']}" for language, r in results.items() if r["error"]]
        rounds = [x for r in results.values() for x in r["rounds"]]

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
            "story_chapters": {lang: self._story_chapters(ctx, approved[lang]) for lang in langs},
            "descriptions": {lang: approved[lang].get("description") for lang in langs},
            "story_chapters_cost": {lang: results[lang]["calls"] for lang in langs},
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
                                   "max_redos": ai_config.documentary.max_redos}, ensure_ascii=False),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    async def add_youtube_parts(self, db: Session, case: Case, bp_row: EditorialBlueprint,
                                master: StoryVersion | None, language: str,
                                spoken: StoryVersion | None, earlier: ChapterPlan,
                                tasks: frozenset[str] | None = None) -> ChapterPlan:
        """The YouTube chapters and description of a plan made before they existed (or the description again when asked): the same writer and auditor, asked for these only (the cards
        are kept as they are). The result is a newer version of the plan; its cost is the whole of these two calls, labelled as such."""
        plan = _loads(earlier.plan_json, {})
        missing = {t for t, key in (("chapters", "story_chapters"), ("description", "descriptions")) if language not in (plan.get(key) or {})}
        tasks = _enabled(frozenset(tasks) if tasks is not None else missing) - {"cards"}
        if not tasks:
            return earlier
        ctx = self.context(db, case, bp_row, master, [language], {language: spoken} if spoken else {})
        result = await self._language(db, case, ctx, language, spoken, [], tasks=tasks)
        if "chapters" in tasks:
            plan["story_chapters"] = {**(plan.get("story_chapters") or {}), language: self._story_chapters(ctx, result["approved"])}
        if "description" in tasks:
            plan["descriptions"] = {**(plan.get("descriptions") or {}), language: result["approved"].get("description")}
        plan["story_chapters_cost"] = {**(plan.get("story_chapters_cost") or {}), language: result["calls"]}
        plan["story_chapters_backfilled"] = {**(plan.get("story_chapters_backfilled") or {}), language: True}
        audit = _loads(earlier.audit_json, {})
        audit["story_chapters_left_out"] = {**(audit.get("story_chapters_left_out") or {}), language: result["left_out"]}
        count = db.query(ChapterPlan).filter(ChapterPlan.blueprint_id == bp_row.id).count()
        row = ChapterPlan(case_id=case.id, blueprint_id=bp_row.id, version=count + 1, status=earlier.status,
                          languages_json=earlier.languages_json, plan_json=json.dumps(plan, ensure_ascii=False),
                          audit_json=json.dumps(audit, ensure_ascii=False))
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
