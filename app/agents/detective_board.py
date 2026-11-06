"""Detective board plan construction: deterministic card selection, then
one writer-agent call per language for the short titles."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import BoardPlan, Case, EditorialBlueprint, EntityNameForm, StoryVersion
from app.documentary import names as NS
from app.documentary.chapters import _text, told_events
from app.documentary.detective_board import select_cards
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

WRITER_SYSTEM = prompt("documentary/detective_board/writer_system")


SOURCE_LANGUAGE = "en"            # the language of the claims a title is written from


def _fallback_title(claim: str, max_words: int) -> str:
    words = " ".join(claim.split()).split(" ")
    return " ".join(w for w in words[:max_words] if w)


def problem_of(title: str | None, language: str, max_words: int) -> str | None:
    """Why a title cannot be pinned in a film of this language, or None: it is missing, longer than a title may be, or (in a
    language written in its own script) holds Latin words, which are names or phrases left untranslated."""
    if not title:
        return "no title"
    if len(title.split()) > max_words:
        return f"more than {max_words} words"
    if NS.uses_native_script(language) and (found := NS.LATIN_WORD.findall(title)):
        return f"Latin words {found}: write every name and word in the language's own script"
    return None


def native_names(db: Session, case_id: int, language: str) -> list[dict]:
    """How the film's narration writes the names of the case's entities ([{key, full, first, last}]), for a language written in
    its own script; the writer uses these spellings so that a name on the board is the name the narration says."""
    if not NS.uses_native_script(language):
        return []
    rows = db.query(EntityNameForm).filter_by(case_id=case_id, language=language).all()
    out = []
    for r in rows:
        forms = json.loads(r.forms_json or "{}")
        if any(forms.values()):
            out.append({"key": r.entity_key, **{part: (forms.get(part) or [None])[0] for part in NS.PARTS}})
    return out


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

    async def _write(self, db: Session, case: Case, ctx: dict, language: str,
                     repair: dict[str, str] | None = None) -> dict[str, str]:
        """Titles for the cards of `ctx` (only the ids of `repair` when given, each with what was wrong with its last title)."""
        max_words = ai_config.detective_board.title_max_words
        system = WRITER_SYSTEM.format(max_words=max_words)
        cards = [c for c in ctx["cards"] if repair is None or c["id"] in repair]
        payload = {"language": language, "names": native_names(db, case.id, language),
                   "cards": [{"id": c["id"], "kind": c["kind"], "claim": c["claim"],
                              **({"previous_problem": repair[c["id"]]} if repair else {})} for c in cards]}
        with track_run(db, case.id, "Detective Board Writer",
                       input_summary=f"{len(cards)} cards") as run:
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
            drafts: dict[str, str] = {}
            try:
                drafts = await self._write(db, case, ctx, language)
                for _ in range(ai_config.detective_board.title_repair_rounds):
                    wrong = {c["id"]: p for c in ctx["cards"]
                             if (p := problem_of(drafts.get(c["id"]), language, max_words))}
                    if not wrong:
                        break
                    drafts.update({k: v for k, v in (await self._write(db, case, ctx, language, wrong)).items()
                                   if k in wrong})
            except Exception as exc:
                errors.append(f"{language}: {type(exc).__name__}: {exc}"[:300])
            for c in ctx["cards"]:
                title = drafts.get(c["id"])
                if problem_of(title, language, max_words) and language == SOURCE_LANGUAGE:
                    title = _fallback_title(c["claim"], max_words)           # the claim is in this very language
                elif problem_of(title, language, max_words):
                    errors.append(f"{language}: card {c['id']} has no title in the language ({problem_of(title, language, max_words)})")
                    continue                                                 # no title: the card is left out of this film
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
