"""Detective board plan construction: deterministic card selection, then
one writer-agent call per language for the short titles."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import BoardPlan, Case, EditorialBlueprint, StoryVersion
from app.documentary.chapters import _text, told_events
from app.documentary.detective_board import select_cards
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

WRITER_SYSTEM = prompt("documentary/detective_board/writer_system")


def _fallback_title(claim: str, max_words: int) -> str:
    words = " ".join(claim.split()).split(" ")
    return " ".join(w for w in words[:max_words] if w)


class BoardWriter:
    """Build the language-neutral selection once, then write native-
    language titles for every card in one call per language."""

    def __init__(self, gen=None):
        self.gen = gen or get_generation_provider()

    def context(self, db: Session, case: Case, bp_row: EditorialBlueprint) -> dict:
        from app.agents.story import build_evidence_pack
        from app.db.models import Contradiction, Fact, Source

        blueprint = json.loads(bp_row.blueprint_json or "{}")
        pack = build_evidence_pack(
            db.query(Fact).filter(Fact.case_id == case.id).all(),
            db.query(Contradiction).filter(Contradiction.case_id == case.id).all(),
            db.query(Source).filter(Source.case_id == case.id).all())
        events = told_events(blueprint, pack.get("timeline") or [])
        beats = {b["id"]: b for b in blueprint.get("beats") or []}
        card_overrides = {}
        from app.db.models import RevealGraph

        graph = db.query(RevealGraph).filter(RevealGraph.blueprint_id == bp_row.id).first()
        if graph is not None:
            from app.documentary.detective.dispatch import board_card_overrides, has_detective_plan

            if has_detective_plan(db, graph.id):
                card_overrides = board_card_overrides(db, graph.id)
        cards = select_cards(blueprint, events, card_overrides)
        claims = {e["id"]: e["claim"] for e in events}
        for c in cards:
            if c["kind"] != "time":
                c["claim"] = str(beats.get(c["beat_id"], {}).get("summary") or "")[:200]
            else:
                c["claim"] = claims.get(c["source_fact"], "")
        return {"cards": cards}

    async def _write(self, db: Session, case: Case, ctx: dict, language: str) -> dict[str, str]:
        max_words = ai_config.detective_board.title_max_words
        system = WRITER_SYSTEM.format(max_words=max_words)
        payload = {"language": language,
                   "cards": [{"id": c["id"], "kind": c["kind"], "claim": c["claim"]}
                            for c in ctx["cards"]]}
        with track_run(db, case.id, "Detective Board Writer",
                       input_summary=f"{len(ctx['cards'])} cards") as run:
            raw, res = await run_agent("documentary.detective_board.write", self.gen,
                                       json.dumps(payload, ensure_ascii=False), system=system)
            stamp_run(run, res, "chapter_writer")
        out: dict[str, str] = {}
        titles = raw.get("titles") if isinstance(raw, dict) else None
        for item in titles or []:
            if isinstance(item, dict) and item.get("id"):
                out[str(item["id"])] = _text(item.get("title") or "")
        return out

    async def create(self, db: Session, case: Case, bp_row: EditorialBlueprint,
                     master: StoryVersion | None, languages: list[str],
                     spoken: dict[str, StoryVersion] | None = None) -> BoardPlan:
        ctx = self.context(db, case, bp_row)
        max_words = ai_config.detective_board.title_max_words
        langs = list(dict.fromkeys(languages))
        titles: dict[str, dict[str, str]] = {c["id"]: {} for c in ctx["cards"]}
        errors: list[str] = []
        for language in langs:
            try:
                drafts = await self._write(db, case, ctx, language)
            except Exception as exc:
                errors.append(f"{language}: {type(exc).__name__}: {exc}"[:300])
                drafts = {}
            for c in ctx["cards"]:
                title = drafts.get(c["id"])
                if not title or len(title.split()) > max_words:
                    title = _fallback_title(c["claim"], max_words)
                if title:
                    titles[c["id"]][language] = title
        plan: dict = {"cards": [{"id": c["id"], "kind": c["kind"], "source_fact": c["source_fact"],
                          "beat_id": c["beat_id"], "status": c["status"], "links": c["links"],
                          "title": titles[c["id"]]}
                         for c in ctx["cards"]]}
        from app.documentary.detective_board import covers_end

        plan["covers_end"] = covers_end(plan["cards"], [b["id"] for b in json.loads(bp_row.blueprint_json or "{}").get("beats") or []])
        status = "approved" if not errors else ("partial" if any(titles.values()) else "no_texts")
        row = BoardPlan(case_id=case.id, blueprint_id=bp_row.id, status=status,
                        languages_json=json.dumps(langs), plan_json=json.dumps(plan))
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
