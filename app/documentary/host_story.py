"""The story as the host sees it: acts in order, each with the narration the viewer hears
and the evidence it tells for the first time.

An appearance that stands after act N may use acts 1..N and nothing else (the knowledge
cutoff). Everything the host planner, writer and critic read about the story comes through
this module, so the cutoff lives in one place: `HostStory.visible`.

Two sources fill it: a story written to an approved story plan (acts are the plan's acts, the
narration is cut on its `[[ACT:id]]` markers, in any language) and a story with an editorial
blueprint (acts are the blueprint's beats)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, EditorialBlueprint, EntityGlossaryEntry, StoryPlan, StoryVersion


@dataclass(frozen=True)
class HostAct:
    id: str
    index: int  # 1-based position in the film
    title: str
    purpose: str
    text: str  # the narration of this act in the story's language
    reveals: tuple[str, ...]  # evidence ids told for the first time in this act
    open_loops: tuple[str, ...]  # questions this act raises
    resolved_loops: tuple[str, ...]  # questions this act answers
    words: int  # weight of the act in the film: the words of its narration
    hook: bool = False  # the act that opens the film
    payoff: bool = False  # the act where the story answers most: a host may not stand before it


@dataclass(frozen=True)
class HostEntity:
    """A person or place of the evidence, with the spellings the language accepts."""
    spellings: tuple[str, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class HostStory:
    case_title: str
    central_question: str
    language: str
    acts: tuple[HostAct, ...]
    evidence: dict[str, dict] = field(default_factory=dict)  # id -> item, every id of the case
    entities: tuple[HostEntity, ...] = ()  # names and places in this language's spellings

    @property
    def act_ids(self) -> list[str]:
        return [a.id for a in self.acts]

    def visible(self, cutoff: int) -> tuple[HostAct, ...]:
        """The acts a viewer has heard when `cutoff` acts have played."""
        return self.acts[:cutoff]

    def hidden(self, cutoff: int) -> tuple[HostAct, ...]:
        return self.acts[cutoff:]

    def visible_evidence_ids(self, cutoff: int) -> list[str]:
        seen: list[str] = []
        for act in self.visible(cutoff):
            seen += [e for e in act.reveals if e not in seen]
        return seen

    def total_words(self) -> int:
        return sum(a.words for a in self.acts)

    def act(self, act_id: str | None) -> HostAct | None:
        return next((a for a in self.acts if a.id == act_id), None)


def split_acts(text: str) -> dict[str, str]:
    """The narration cut on its `[[ACT:id]]` markers; empty when the text has none."""
    from app.agents.story import _parse_sections

    sections = _parse_sections(text or "")
    return {} if [s["id"] for s in sections] == ["full"] else {s["id"]: s["text"] for s in sections}


def _evidence_items(pack: dict) -> dict[str, dict]:
    from app.documentary.host import evidence_items

    return {i["id"]: i for i in evidence_items(pack)}


def _payoff_flags(resolved: list[int]) -> list[bool]:
    threshold = ai_config.host.payoff_min_resolved_loops
    return [n >= threshold for n in resolved]


def story_from_plan(case: Case, plan: dict, texts: dict[str, str], language: str,
                    pack: dict, entities: tuple[HostEntity, ...] = ()) -> HostStory:
    """A story written to a story plan: its acts, in the plan's order."""
    raw = plan.get("acts") or []
    missing = [str(a["id"]) for a in raw if str(a["id"]) not in texts]
    if missing:
        raise ValueError(f"the story text has no act marker for: {', '.join(missing)}")
    flags = _payoff_flags([len(a.get("resolved_loops") or []) for a in raw])
    acts = tuple(
        HostAct(id=str(a["id"]), index=n, title=str(a.get("title") or a["id"]),
                purpose=str(a.get("purpose") or ""), text=texts[str(a["id"])],
                reveals=tuple(str(e) for e in a.get("reveals") or []),
                open_loops=tuple(str(x) for x in a.get("open_loops") or []),
                resolved_loops=tuple(str(x) for x in a.get("resolved_loops") or []),
                words=len(texts[str(a["id"])].split()),
                hook=n == 1, payoff=flags[n - 1])
        for n, a in enumerate(raw, 1))
    return HostStory(case_title=case.canonical_title,
                     central_question=str(plan.get("central_question") or ""),
                     language=language, acts=acts, evidence=_evidence_items(pack),
                     entities=entities)


def story_from_blueprint(case: Case, blueprint: dict, texts: dict[str, str], language: str,
                         pack: dict, entities: tuple[HostEntity, ...] = ()) -> HostStory:
    """A story with an editorial blueprint: its beats are the acts."""
    beats = blueprint.get("beats") or []
    acts = tuple(
        HostAct(id=b["id"], index=n, title=str(b.get("summary") or b["id"])[:120],
                purpose=str(b.get("purpose") or ""), text=texts.get(b["id"], ""),
                reveals=tuple(str(e) for e in b.get("reveals") or []),
                open_loops=tuple(str(x) for x in b.get("opens") or []),
                resolved_loops=tuple(str(x) for x in b.get("answers") or []),
                words=len((texts.get(b["id"]) or "").split()),
                hook=b.get("purpose") == "hook", payoff=b.get("purpose") == "reveal")
        for n, b in enumerate(beats, 1))
    return HostStory(case_title=case.canonical_title,
                     central_question=str(blueprint.get("central_question") or ""),
                     language=language, acts=acts, evidence=_evidence_items(pack),
                     entities=entities)


def glossary_entities(db: Session, case_id: int, language: str) -> tuple[HostEntity, ...]:
    """The case's entity glossary in one language (proposed or approved spellings)."""
    rows = (db.query(EntityGlossaryEntry)
            .filter(EntityGlossaryEntry.case_id == case_id,
                    EntityGlossaryEntry.language == language).all())
    return tuple(HostEntity(tuple(json.loads(r.spellings or "[]")),
                            tuple(json.loads(r.evidence_ids or "[]"))) for r in rows)


def load_story(db: Session, case: Case, version: StoryVersion, pack: dict,
               bp_row: EditorialBlueprint | None = None) -> HostStory:
    """The story of one language's version, from its story plan when the text carries the
    plan's act markers, otherwise from the blueprint's beats."""
    from app.documentary.host import beat_texts

    language = version.language or "en"
    plan_row = db.get(StoryPlan, version.story_plan_id) if version.story_plan_id else None
    if plan_row is not None:
        plan = json.loads(plan_row.plan_json or "{}")
        texts = split_acts(version.story_text)
        if texts and all(str(a["id"]) in texts for a in plan.get("acts") or []):
            return story_from_plan(case, plan, texts, language, pack,
                                   glossary_entities(db, case.id, language))
    if bp_row is None:
        raise RuntimeError("The story has neither act markers of its plan nor a blueprint.")
    blueprint = json.loads(bp_row.blueprint_json or "{}")
    return story_from_blueprint(case, blueprint, beat_texts(version, blueprint), language, pack,
                                glossary_entities(db, case.id, language))


_WORD = re.compile(r"\w+", re.UNICODE)


def words_of(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]
