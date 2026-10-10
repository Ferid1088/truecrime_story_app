"""The ChapterWriter agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.chapters."""

from __future__ import annotations

from app.agents.runner import run_agent
import json
from sqlalchemy.orm import Session
from app.agents.story import build_evidence_pack
from app.core.ai_config import ai_config
from app.db.models import Case, ChapterPlan, Contradiction, EditorialBlueprint, Fact, Source, StoryVersion
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.documentary.chapters import (
    AUDITOR_SYSTEM,
    WRITER_SYSTEM,
    _loads,
    _text,
    acts_of,
    cold_open,
    log,
    told_events,
)


class ChapterWriter:
    """Writes and audits the card texts of one blueprint (all languages)."""

    def __init__(self, gen=None):
        self.gen = gen or get_generation_provider()

    # -- context ---------------------------------------------------------------
    def context(self, db: Session, case: Case, bp_row: EditorialBlueprint,
                master: StoryVersion | None, languages: list[str],
                spoken: dict[str, StoryVersion] | None = None) -> dict:
        blueprint = _loads(bp_row.blueprint_json, {})
        structure = _loads(master.narrative_structure, {}) if master else {}
        pack = build_evidence_pack(
            db.query(Fact).filter(Fact.case_id == case.id).all(),
            db.query(Contradiction).filter(Contradiction.case_id == case.id).all(),
            db.query(Source).filter(Source.case_id == case.id).all())
        excerpts = {}
        for lang, v in (spoken or {}).items():
            if v is not None and v.story_text:
                excerpts[lang] = v.story_text[:1500]
        return {
            "blueprint": blueprint, "structure": structure,
            "acts": acts_of(blueprint, structure),
            "events": told_events(blueprint, pack.get("timeline") or []),
            "cold_open": cold_open(blueprint),
            "languages": list(dict.fromkeys(["en", *languages])),
            "case": case.canonical_title,
            "case_status": getattr(case, "resolution_status", None) or "UNKNOWN",
            "film_title": structure.get("title"),
            "central_question": structure.get("central_question")
            or blueprint.get("central_question"),
            "excerpts": excerpts,
        }

    def _payload(self, ctx: dict) -> dict:
        return {
            "case": ctx["case"], "case_status": ctx["case_status"],
            "languages": ctx["languages"], "central_question": ctx["central_question"],
            "film_title_draft": ctx["film_title"],
            "chapters": [{"act_id": a["act_id"], "number": a["number"],
                          "planned_title": a["plan_title"], "about": a["purpose"],
                          "reveals_in_this_chapter": a["reveals"],
                          "revealed_later": [r for b in ctx["acts"][a["number"]:]
                                             for r in b["reveals"]][:12]}
                         for a in ctx["acts"]],
            "events": [{"id": e["id"], "date": e["date"], "claim": e["claim"]}
                       for e in ctx["events"]],
            "narration_excerpts": ctx["excerpts"],
            "limits": {"title": ai_config.chapters.max_title_chars,
                       "label": ai_config.chapters.max_label_chars},
        }

    def keys(self, ctx: dict) -> list[str]:
        # the film's title card is shown only after a cold open
        out = ["film_title"] if ctx["cold_open"] and ai_config.chapters.title_card else []
        out += [f"chapter:{a['act_id']}" for a in ctx["acts"]]
        out += [f"event:{e['id']}" for e in ctx["events"]]
        return out

    @staticmethod
    def _parse(raw: dict) -> dict[str, dict[str, str]]:
        raw = raw if isinstance(raw, dict) else {}
        out: dict[str, dict[str, str]] = {}
        if isinstance(raw.get("film_title"), dict):
            out["film_title"] = {k: _text(v) for k, v in raw["film_title"].items()}
        for c in raw.get("chapters") or []:
            if isinstance(c, dict) and c.get("act_id") and isinstance(c.get("title"), dict):
                out[f"chapter:{c['act_id']}"] = {k: _text(v) for k, v in c["title"].items()}
        for e in raw.get("events") or []:
            if isinstance(e, dict) and e.get("id") and isinstance(e.get("label"), dict):
                out[f"event:{e['id']}"] = {k: _text(v) for k, v in e["label"].items()}
        return out

    @staticmethod
    def precheck(key: str, lang: str, text: str | None) -> str | None:
        cfg = ai_config.chapters
        limit = cfg.max_label_chars if key.startswith("event:") else cfg.max_title_chars
        if not text:
            return "missing"
        if len(text) > limit:
            return f"longer than {limit} characters"
        return None

    async def _write(self, db: Session, case: Case, ctx: dict, fix: list[dict] | None):
        cfg = ai_config.chapters
        system = WRITER_SYSTEM.format(max_title=cfg.max_title_chars, max_label=cfg.max_label_chars)
        payload = self._payload(ctx)
        if fix:
            payload["fix"] = fix
        with track_run(db, case.id, "Chapter Writer",
                       input_summary=f"{len(fix) if fix else 'all'} items") as run:
            raw, res = await run_agent("documentary.chapters.write", self.gen, json.dumps(payload, ensure_ascii=False), system=system)
            stamp_run(run, res, "chapter_writer")
        return self._parse(raw)

    async def _audit(self, db: Session, case: Case, ctx: dict,
                     texts: dict[str, dict[str, str]]) -> dict[str, dict]:
        payload = self._payload(ctx)
        payload["texts"] = texts
        with track_run(db, case.id, "Chapter Auditor",
                       input_summary=f"{len(texts)} items") as run:
            raw, res = await run_agent("documentary.chapters.audit", self.gen, json.dumps(payload, ensure_ascii=False), system=AUDITOR_SYSTEM)
            stamp_run(run, res, "chapter_auditor")
        out = {}
        verdicts = raw.get("verdicts") if isinstance(raw, dict) else None
        for v in verdicts or []:
            if isinstance(v, dict) and v.get("key"):
                out[str(v["key"])] = v
        return out

    # -- the loop ---------------------------------------------------------------
    async def create(self, db: Session, case: Case, bp_row: EditorialBlueprint,
                     master: StoryVersion | None, languages: list[str],
                     spoken: dict[str, StoryVersion] | None = None) -> ChapterPlan:
        ctx = self.context(db, case, bp_row, master, languages, spoken)
        langs = ctx["languages"]
        keys = self.keys(ctx)
        approved: dict[str, dict[str, str]] = {}
        pending: dict[str, set[str]] = {k: set(langs) for k in keys}
        reasons: dict[str, str] = {}
        drafts: dict[str, dict[str, str]] = {}
        rounds: list[dict] = []
        error = None
        redos = ai_config.documentary.max_redos
        try:
            for r in range(redos + 1):
                if not pending:
                    break
                fix = None if r == 0 else [
                    {"key": k, "previous": drafts.get(k), "languages": sorted(ls),
                     "auditor": reasons.get(k)} for k, ls in pending.items()]
                got = await self._write(db, case, ctx, fix)
                for k in pending:
                    if k in got:
                        drafts[k] = {**drafts.get(k, {}), **got[k]}
                check = {k: {lg: drafts.get(k, {}).get(lg) for lg in ls}
                         for k, ls in pending.items()}
                verdicts = await self._audit(db, case, ctx, check)
                round_log = []
                for k, ls in list(pending.items()):
                    v = verdicts.get(k)
                    bad_model = set()
                    if v is None:
                        bad_model = set(ls)  # not judged = not approved
                        why = "the auditor did not judge it"
                    elif v.get("ok") is False:
                        named = {str(x) for x in v.get("languages") or []}
                        bad_model = (named & ls) or set(ls)
                        why = str(v.get("reason") or "rejected")[:300]
                    else:
                        why = ""
                    still = set()
                    for lg in ls:
                        pre = self.precheck(k, lg, drafts.get(k, {}).get(lg))
                        if pre or lg in bad_model:
                            still.add(lg)
                            reasons[k] = (pre and f"{lg}: {pre}") or why
                        else:
                            approved.setdefault(k, {})[lg] = drafts[k][lg]
                    round_log.append({"key": k, "approved": sorted(ls - still),
                                      "rejected": sorted(still),
                                      "reason": reasons.get(k) if still else None})
                    if still:
                        pending[k] = still
                    else:
                        pending.pop(k)
                rounds.append({"round": r, "items": round_log})
        except Exception as e:  # noqa: BLE001 — cards without texts, reported
            log.warning("chapter texts for case %s failed: %s", case.id, e)
            error = f"{type(e).__name__}: {e}"[:300]
        left_out = [{"key": k, "languages": sorted(ls), "reason": reasons.get(k) or error}
                    for k, ls in pending.items()]
        plan = {
            "languages": langs, "cold_open": ctx["cold_open"],
            "film_title": approved.get("film_title") or {},
            "chapters": [{"act_id": a["act_id"], "number": a["number"],
                          "first_beat": a["first_beat"], "last_beat": a["last_beat"],
                          "beats": a["beats"],
                          "title": approved.get(f"chapter:{a['act_id']}") or {}}
                         for a in ctx["acts"]],
            "events": [{**e, "label": approved.get(f"event:{e['id']}") or {}}
                       for e in ctx["events"]],
        }
        status = ("no_texts" if not approved and keys else
                  "partial" if left_out else "approved")
        count = db.query(ChapterPlan).filter(ChapterPlan.blueprint_id == bp_row.id).count()
        row = ChapterPlan(case_id=case.id, blueprint_id=bp_row.id, version=count + 1,
                          status=status, languages_json=json.dumps(langs),
                          plan_json=json.dumps(plan, ensure_ascii=False),
                          audit_json=json.dumps({"rounds": rounds, "left_out": left_out,
                                                 "error": error, "max_redos": redos},
                                                ensure_ascii=False))
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
