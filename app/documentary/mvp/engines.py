"""The eight cinematic engines as planner candidates: for each narration beat an agent decides whether one of them
makes the moment clearer and, if so, structures its data from the stored evidence only. Rules then check every
statement against the fact it names (words, figures, kind of statement, dates) - what fails is dropped, never
shown. The owner can disable an engine, force another one for a beat, edit its texts and change its length from the
planner's inspector; those choices are kept on the needs plan and applied to every rerun and language.

Engines are `Graphic`s (graphics/engines): they fill bare stretches through the same offers, repetition and cue
rules as every other alternative graphic."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import Case, VisualPlan
from app.documentary.graphics import shots as GS
from app.documentary.graphics import theme as T
from app.documentary.graphics.base import GraphicUnavailable
from app.documentary.graphics.engines import kit
from app.documentary.graphics.registry import graphic
from app.documentary.mvp import alternatives as AL
from app.documentary.mvp.copy import numbers, words
from app.providers.generation import get_generation_provider
from app.services.budget import BudgetService
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)

COURT_WORDS = re.compile(r"\b(jury|judge|court|sentenc\w*|convict\w*|verdict|guilty|ruled|ruling|trial)\b", re.I)
GAP_WORDS = re.compile(r"\b(no record|missing|not documented|undocumented|gap|unclear|never found|not found|"
                       r"chain of custody|unaccounted)\b", re.I)
SOFT_BASES = ("allegation", "witness", "disputed", "unknown")


@dataclass(frozen=True)
class EngineSpec:
    """What the planner and the inspector know about an engine before drawing anything."""

    kind: str
    name: str
    intents: tuple[str, ...]
    requires: str


SPECS: dict[str, EngineSpec] = {s.kind: s for s in (
    EngineSpec("courtroom", "Courtroom reasoning", ("legal_argument", "verdict", "conflicting_accounts"),
               "a prosecution argument, a defense argument and a documented court finding"),
    EngineSpec("custody", "Evidence chain of custody", ("evidence_journey",),
               "two or more documented stages of one evidence item's path"),
    EngineSpec("unknowns", "Map of unknowns", ("open_questions", "missing_time", "disputed_accounts"),
               "at least one documented known point and one open question"),
    EngineSpec("forensic", "Forensic examination", ("lab_result", "scientific_method"),
               "a documented examination of a sample with its reported result"),
    EngineSpec("interrogation", "Interview analysis", ("interview", "statement_comparison"),
               "documented questions or answers of an interview or police statement"),
    EngineSpec("sentencing", "Sentencing explanation", ("sentence", "charges"),
               "the documented charges and the sentence the court gave"),
    EngineSpec("investigation_clock", "Investigation clock", ("investigation_progress", "delays"),
               "dated events of the investigation (and of the crime) from the case timeline"),
    EngineSpec("evidence_check", "Evidence check", ("evidence_reliability",),
               "one evidence item with what it shows and what a source says it does not show"),
)}


@dataclass
class Fact:
    """Everything a statement may be checked against: the text of a stored fact, timeline entry, question or dispute."""

    id: str
    text: str
    disputed: bool = False
    category: str = ""
    date: str | None = None


def reference_texts(ev: AL.Evidence) -> dict[str, Fact]:
    refs = {fid: Fact(fid, " ".join(filter(None, [f["claim"], f.get("supporting_text") or ""])),
                      bool(f.get("disputed") or f.get("uncertain")), f.get("category") or "")
            for fid, f in ev.facts.items()}
    for t in ev.timeline:
        refs[t["id"]] = Fact(t["id"], t["claim"], False, "timeline", t.get("event_date"))
    for q in ev.questions:
        refs[q["id"]] = Fact(q["id"], q["question"], True, "question")
    for c in ev.contradictions:
        refs[c["id"]] = Fact(c["id"], f"{c['topic']} {c['description']}", True, "dispute")
    return refs


def _grounded(text: str, ref: Fact, verbatim: bool) -> str | None:
    """Why `text` is not supported by the stored text it cites, or None."""
    flat = " ".join(text.split())
    if verbatim:
        norm = lambda s: re.sub(r"\W+", " ", s.casefold()).strip()  # noqa: E731
        return None if norm(flat) in norm(ref.text) else "a verbatim quotation must stand word for word in the record"
    if not numbers(flat) <= numbers(ref.text):
        return "it contains a figure that the cited fact does not"
    mine = words(flat)
    share = len(mine & words(ref.text)) / len(mine) if mine else 0.0
    return None if share >= kit.settings()["min_grounded_share"] else "it is not supported by the cited fact"


def check_item(item: dict, refs: dict[str, Fact], path: str, verbatim: bool = False) -> str | None:
    """The rule check of one statement; returns the first reason it may not be shown."""
    if item.get("basis") == "illustration":
        text = item.get("text", "")
        return None if not re.search(r"\d", text) and not item.get("fact_id") else f"{path}: an illustration has no figures or facts"
    ref = refs.get(item.get("fact_id", ""))
    if ref is None:
        return f"{path}: fact {item.get('fact_id')!r} is not in the case"
    basis, text = item.get("basis"), item.get("text", "")
    if path.endswith("issue"):           # the question a shot asks is a framing: it may not add figures, nothing more
        return None if numbers(text) <= numbers(ref.text) else f"{path}: it contains a figure that the cited fact does not"
    if ref.disputed and basis not in SOFT_BASES:
        return f"{path}: {ref.id} is disputed or uncertain and cannot be shown as {basis}"
    if basis == "finding" and not (ref.category == "legal" or COURT_WORDS.search(ref.text)):
        return f"{path}: {ref.id} does not record a court finding"
    if basis == "unknown" and ref.category != "question" and not ref.disputed:
        return f"{path}: {ref.id} does not record an open question"
    problem = _grounded(text, ref, verbatim)
    return f"{path}: {problem}" if problem else None


def walk(data: dict) -> list[tuple[str, dict]]:
    """(path, statement) of every statement of an engine's data (a dict with text, basis and a fact id)."""
    out: list[tuple[str, dict]] = []

    def visit(node: object, path: str) -> None:
        if isinstance(node, dict):
            if "text" in node and "basis" in node:
                out.append((path, node))
            for k, v in node.items():
                if isinstance(v, dict | list):
                    visit(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                visit(v, f"{path}[{i}]")
    visit(data, "")
    return out


def normalize(kind: str, data: dict, refs: dict[str, Fact]) -> dict:
    """Facts the code knows better than the agent: clock dates come from the timeline entry, interview turns carry
    their basis, and a statement's id is trimmed."""
    data = json.loads(json.dumps(data))
    for event in data.get("events") or []:
        ref = refs.get(str(event.get("fact_id", "")))
        if ref and ref.date:
            parts = [int(p) for p in ref.date.split("-") if p.isdigit()]
            keys = ("year", "month", "day")
            event["date"] = dict(zip(keys, parts, strict=False))
    for turn in data.get("turns") or []:
        turn.setdefault("basis", "witness")
    for charge in data.get("charges") or []:
        charge.setdefault("text", charge.get("outcome", ""))
    return data


def validate(kind: str, data: dict, refs: dict[str, Fact]) -> tuple[dict | None, list[str]]:
    """(clean data, problems): data only when the engine accepts it and every statement is grounded."""
    if kind not in SPECS or not isinstance(data, dict):
        return None, [f"unknown engine {kind!r}"]
    data = normalize(kind, data, refs)
    problems = []
    for path, item in walk(data):
        verbatim = item.get("mode") == "verbatim"
        problem = check_item(item, refs, f"{kind}.{path}", verbatim)
        if problem:
            problems.append(problem)
    item = data.get("item")
    if item is not None and not (refs.get(str(item.get("fact_id", ""))) and words(item.get("name", ""))
                                 & words(refs[item["fact_id"]].text)):
        problems.append(f"{kind}: the evidence item {item.get('name')!r} is not in the fact it cites")
    for event in data.get("events") or []:
        ref = refs.get(str(event.get("fact_id", "")))
        if not ref or not ref.date:
            problems.append(f"{kind}: every clock event must stand on a dated timeline entry")
    for stage in data.get("stages") or []:
        gap = stage.get("gap_after")
        if gap and (not refs.get(gap.get("fact_id", "")) or not GAP_WORDS.search(refs[gap["fact_id"]].text)):
            problems.append(f"{kind}: a gap in the records must cite a fact that says so")
    for key in ("charges",):
        for charge in data.get(key) or []:
            ref = refs.get(charge.get("fact_id", ""))
            if ref and not (words(charge.get("charge", "")) & words(ref.text)):
                problems.append(f"{kind}: charge {charge.get('charge')!r} is not in {ref.id}")
    try:
        reason = graphic(kind).can_render(data)
    except (KeyError, TypeError, AttributeError) as e:
        reason = f"incomplete data ({type(e).__name__})"
    if reason:
        problems.append(f"{kind}: {reason}")
    return (data if not problems else None), problems


def provenance(data: dict, refs: dict[str, Fact], ev: AL.Evidence) -> list[dict]:
    """Where every case-specific statement comes from, for the inspector."""
    out = []
    for fid in kit.fact_ids(data):
        ref = refs[fid]
        fact = ev.facts.get(fid) or {}
        out.append({"fact_id": fid, "text": ref.text[:300], "source_ids": fact.get("source_ids") or [],
                    "disputed": ref.disputed})
    return out


@dataclass
class Decision:
    """The planner's choice for one beat (kept on the needs plan; the inspector shows it)."""

    beat: str
    engine: str | None
    why: str = ""
    eligible: list[str] = field(default_factory=list)
    missing: str = ""
    data: dict | None = None
    problems: list[str] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)

    def dump(self) -> dict:
        return {"beat": self.beat, "engine": self.engine, "why": self.why, "eligible": self.eligible,
                "missing": self.missing, "data": self.data, "problems": self.problems,
                "provenance": self.provenance}


def beat_view(ev: AL.Evidence, beat: dict, refs: dict[str, Fact]) -> dict | None:
    """What the agent may use for a beat: only what the story has told by then (the beat's own reveals first)."""
    told = list(dict.fromkeys((beat.get("reveals") or []) + (beat.get("viewer_knows") or [])))
    usable = [f for f in told if f in refs]
    if not usable:
        return None
    reveals = [f for f in beat.get("reveals") or [] if f in refs]
    # the beat's own facts first; the clock may also cite any told timeline entry
    stamps = [t["id"] for t in ev.timeline if t["id"] in set(told)]
    ids = (list(dict.fromkeys(reveals + [f for f in usable if f not in stamps]))[:20] + stamps)[:28]
    return {"beat": beat["id"], "summary": (beat.get("summary") or "")[:300],
            "said": ev.narration.get(beat["id"], "")[:900],
            "facts": [{"id": i, "text": refs[i].text[:340], "disputed": refs[i].disputed, "category": refs[i].category,
                       **({"date": refs[i].date} if refs[i].date else {})} for i in ids]}


class EnginePlanner:
    def __init__(self, db: Session, case: Case, needs_plan: VisualPlan, generation=None, planner=None):
        self.db, self.case, self.plan = db, case, needs_plan
        self.cfg = T.config().engines["selection"]
        self.generation = generation
        self._planner = planner
        self.calls = 0
        self.errors: list[str] = []

    def store(self) -> dict:
        return json.loads(self.plan.validation_json or "{}").get("mvp", {}).get("engines") or {}

    def save(self, store: dict, decisions: dict[str, Decision] | None = None) -> None:
        validation = json.loads(self.plan.validation_json or "{}")
        validation.setdefault("mvp", {})["engines"] = store
        if decisions is not None:           # what the planner's inspector lists without gathering the evidence again
            validation["mvp"]["engine_decisions"] = {**(validation["mvp"].get("engine_decisions") or {}),
                                                     **{b: d.dump() for b, d in decisions.items()}}
        self.plan.validation_json = json.dumps(validation, ensure_ascii=False)
        self.db.commit()

    def overrides(self) -> dict:
        return json.loads(self.plan.validation_json or "{}").get("mvp", {}).get("engine_overrides") or {}

    @staticmethod
    def key(view: dict, forced: str | None) -> str:
        body = json.dumps([view, forced, T.config().engines["version"]], sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(body.encode()).hexdigest()[:16]

    async def ask(self, payload: dict) -> dict | None:
        if self._planner is not None:
            return await self._planner(payload)
        gen = self.generation or get_generation_provider()
        body = json.dumps(payload, ensure_ascii=False)
        with track_run(self.db, self.case.id, "Cinematic engines: plan", input_summary=f"{len(payload['beats'])} beats") as run:
            data, res = await asyncio.wait_for(run_agent("mvp.engine_plan", gen, body),
                                               ai_config.mvp().call_timeout_s * 2)
            stamp_run(run, res, ai_config.agents["mvp.engine_plan"].role)
        return data

    async def decide(self, ev: AL.Evidence, force: dict[str, str] | None = None,
                     refresh: str | None = None) -> dict[str, Decision]:
        """{beat id: Decision} for every beat that has told facts; answers are reused until the beat's words or
        facts change. `force` names an engine the owner wants for a beat; `refresh` plans that beat again."""
        force = force or {}
        refs = reference_texts(ev)
        store = self.store()
        views = {b["id"]: v for b in ev.beats if (v := beat_view(ev, b, refs))}
        if refresh in views:
            store.pop(self.key(views[refresh], force.get(refresh)), None)
        todo = [v for bid, v in views.items() if self.key(v, force.get(bid)) not in store]
        for start in range(0, len(todo), self.cfg["beats_per_call"]):
            batch = todo[start:start + self.cfg["beats_per_call"]]
            snap = BudgetService(self.db).snapshot(self.case.id)
            if snap.spent_eur >= snap.cap_eur - 1e-9:
                break
            payload = {"beats": batch, "forced": {v["beat"]: force[v["beat"]] for v in batch if v["beat"] in force},
                       "engines": {k: {"name": s.name, "intents": list(s.intents), "requires": s.requires}
                                   for k, s in SPECS.items()}}
            try:
                self.calls += 1
                answer = await self.ask(payload)
            except Exception as e:  # noqa: BLE001 - a failed batch is tried again next time, never remembered
                self.errors.append(f"{type(e).__name__}: {e}"[:200])
                log.warning("engine plan failed: %s", e)
                continue
            by_beat = {r.get("beat"): r for r in (answer or {}).get("beats") or [] if isinstance(r, dict)}
            for view in batch:
                store[self.key(view, force.get(view["beat"]))] = self.check(by_beat.get(view["beat"]) or {}, view,
                                                                             refs, force.get(view["beat"]), ev)
        decisions = {bid: Decision(**store[k]) for bid, v in views.items() if (k := self.key(v, force.get(bid))) in store}
        self.save(store, decisions)
        return decisions

    def check(self, raw: dict, view: dict, refs: dict[str, Fact], forced: str | None, ev: AL.Evidence) -> dict:
        engine = raw.get("engine")
        eligible = [e for e in raw.get("eligible") or [] if e in SPECS]
        decision = Decision(view["beat"], None, str(raw.get("why") or "")[:300], eligible,
                            str(raw.get("missing") or "")[:300])
        if engine in (None, "none") or engine not in SPECS:
            return decision.dump()
        told = {f["id"] for f in view["facts"]}
        data, problems = validate(engine, raw.get("data") or {}, refs)
        if data is not None and not set(kit.fact_ids(data)) <= told:
            data, problems = None, [f"{engine}: it cites facts the story has not told by this beat"]
        decision.engine, decision.data, decision.problems = engine, data, problems
        if forced and engine != forced:
            decision.problems.append(f"the owner asked for {forced}; the agent built {engine}")
            data = None
        if data is None:
            decision.engine = None
        else:
            decision.provenance = provenance(data, refs, ev)
        return decision.dump()


def beat_only(ev: AL.Evidence, beat_id: str) -> AL.Evidence:
    """The evidence with only one beat to plan (the owner asked for that beat again)."""
    return dataclasses.replace(ev, beats=[b for b in ev.beats if b["id"] == beat_id])


def steps_of(kind: str, data: dict) -> list[str]:
    return graphic(kind).steps(data)


def engine_seconds(kind: str, steps: int) -> float:
    cfg = T.config().engines
    return min(max(cfg["base_seconds"] + cfg["seconds_per_step"] * steps, cfg["hold_min_seconds"]),
               cfg["hold_max_seconds"])


def apply_overrides(decisions: dict[str, Decision], overrides: dict, refs: dict[str, Fact]) -> dict[str, Decision]:
    """The owner's edits and switches on top of the agent's decisions."""
    disabled = set(overrides.get("disabled") or [])
    out: dict[str, Decision] = {}
    for bid, d in decisions.items():
        if d.engine is None or d.data is None or f"{d.engine}|{bid}" in disabled:
            continue
        patch = (overrides.get("edits") or {}).get(f"{d.engine}|{bid}")
        if patch:
            edited, problems = validate(d.engine, patch, refs)
            if edited is None:
                log.warning("engine edit of %s|%s rejected: %s", d.engine, bid, problems)
            else:
                d = Decision(**{**d.dump(), "data": edited})
        out[bid] = d
    return out


async def build_engine_offers(db: Session, case: Case, needs_plan: VisualPlan, ev: AL.Evidence,
                              planner: EnginePlanner | None = None) -> list[AL.Offer]:
    planner = planner or EnginePlanner(db, case, needs_plan)
    overrides = planner.overrides()
    refs = reference_texts(ev)
    decisions = apply_overrides(await planner.decide(ev, overrides.get("forced")), overrides, refs)
    timing = overrides.get("timing") or {}
    offers = []
    for bid, d in decisions.items():
        data, kind = d.data, d.engine
        best = ev.order[bid]
        steps = graphic(kind).required_cues(data)
        seconds = float(timing.get(f"{kind}|{bid}", {}).get("seconds") or engine_seconds(kind, steps))
        made = AL.offer(kind, f"{kind}|{bid}", data, best, items=(f"{kind}|{bid}",), count=steps, seconds=seconds)
        if made:
            made.meta = {"engine": kind, "version": graphic(kind).version, "beat": bid, "why": d.why,
                         "eligible": d.eligible, "steps": steps_of(kind, data), "provenance": d.provenance}
            offers.append(made)
        else:
            log.warning("engine %s for %s cannot be drawn at %.1fs", kind, bid, seconds)
    return offers


def drawn_check(kind: str, data: dict, seconds: float, language: str = "en") -> str | None:
    """Why the engine cannot be drawn with this data at this length, or None."""
    spec = {"type": kind, "data": data, "cue_fractions": GS.cue_fractions(kind, graphic(kind).required_cues(data))}
    try:
        GS.render_for(spec, seconds, language)
    except GraphicUnavailable as e:
        return str(e)
    return None
