"""The material every language of a case shares: the story plan and the evidence.

It is built once per run. The text blocks sent to the models are serialised the same
way every time (sorted keys, no whitespace), so the leading bytes of a request are
identical across the four languages and the provider can serve them from its cache."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.story import build_evidence_pack, evidence_fingerprint
from app.core.ai_config import ai_config
from app.db.models import Case, Contradiction, Fact, Source, StoryPlan

_DRAFT_FACT_KEYS = ("id", "claim", "evidence_strength", "uncertain", "quote_status",
                    "supporting_text")
# What the English fact inspector and the reviser read about each fact (source_count: how many
# independent sources state it).
_MASTER_FACT_KEYS = _DRAFT_FACT_KEYS


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ordered_dump(value) -> str:
    """JSON that keeps the insertion order of its keys: a request whose stable keys come first
    and variable keys last, so the provider can cache the stable leading part."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _facts(pack: dict, keys: tuple[str, ...], with_sources: bool) -> list[dict]:
    out = []
    for fact in pack.get("facts") or []:
        item = {k: fact.get(k) for k in keys}
        if with_sources:
            ids = fact.get("source_ids") or []
            item["source_ids"] = ids
            item["supporting_sources"] = len(set(ids))
        out.append(item)
    return out


def _unique_claims(facts: list[dict]) -> list[dict]:
    """Facts with the same claim (case and spacing aside) once, the first one's id, the sources of all."""
    seen: dict[str, dict] = {}
    for fact in facts:
        key = " ".join(str(fact.get("claim") or "").lower().split())
        if key in seen:
            merged = list(dict.fromkeys([*(seen[key].get("source_ids") or []), *(fact.get("source_ids") or [])]))
            seen[key]["source_ids"], seen[key]["supporting_sources"] = merged, len(set(merged))
        else:
            seen[key] = dict(fact)
    return list(seen.values())


def _timeline(pack: dict) -> list[dict]:
    return [{"id": t["id"], "event_date": t.get("event_date"), "claim": t.get("claim")}
            for t in pack.get("timeline") or []]


def _contradictions(pack: dict) -> list[dict]:
    return [{k: c.get(k) for k in ("id", "topic", "description", "severity")}
            for c in pack.get("contradictions") or []]


def _context(pack: dict) -> list[dict]:
    return [{k: c.get(k) for k in ("id", "source", "language", "summary")}
            for c in pack.get("approved_context") or []]


@dataclass(frozen=True)
class SharedMaterial:
    case_id: int
    plan_id: int
    plan: dict
    pack: dict
    draft_pack: dict
    fingerprint: str
    # An update video: the earlier coverage and what changed (lifecycle.followups.context).
    follow_up: dict | None = None
    # The case's sources as {"id", "title", "type"}; the evidence audit names them next to the facts.
    sources: tuple[dict, ...] = ()

    @property
    def acts(self) -> list[dict]:
        return self.plan.get("acts") or []

    @property
    def act_ids(self) -> list[str]:
        return [str(a["id"]) for a in self.acts]

    def cache_key(self) -> str:
        """One key for every call of the case on this plan and evidence."""
        return f"case{self.case_id}:plan{self.plan_id}:{self.fingerprint}"

    def act_shares(self) -> dict[str, float]:
        """Each act's share of the story, from the plan's own word budgets."""
        words = {str(a["id"]): float(a.get("target_words") or 0) for a in self.acts}
        total = sum(words.values())
        if total <= 0:
            return {k: round(1 / len(words), 4) for k in words} if words else {}
        return {k: round(v / total, 4) for k, v in words.items()}

    def act_plan_words(self) -> dict[str, float]:
        """The plan's own word budget of each act, in order."""
        return {str(a["id"]): float(a.get("target_words") or 0) for a in self.acts}

    # -- the plan as the writers see it ---------------------------------------
    def plan_view(self) -> dict:
        keys = ("title", "central_question", "opening_strategy", "hook_design", "acts",
                "open_loops", "reveal_map", "ending_strategy", "human_threads", "case_status")
        view = {k: self.plan.get(k) for k in keys if self.plan.get(k) is not None}
        view["acts"] = [
            {k: a.get(k) for k in ("id", "title", "purpose", "key_beats", "open_loops",
                                   "resolved_loops", "do_not_reveal", "evidence_ids")
             if a.get(k) is not None}
            for a in self.acts]
        return view

    def film_plan_view(self) -> dict:
        """The plan as the English film writers see it: acts, purposes, key beats, reveal order,
        what is withheld and the ending; no evidence ids."""
        keys = ("title", "central_question", "reveal_map", "ending_strategy")
        view = {k: self.plan.get(k) for k in keys if self.plan.get(k) is not None}
        view["acts"] = [
            {k: a.get(k) for k in ("id", "title", "purpose", "key_beats", "open_loops",
                                   "resolved_loops", "do_not_reveal") if a.get(k) is not None}
            for a in self.acts]
        return view

    def correction_plan_view(self, act_ids: list[str]) -> dict:
        """What the bounded length correction reads of the plan: the listed acts in story order, what
        each withholds, and the ending when the last act is one of them. No evidence, no other act."""
        listed = [a for a in self.acts if str(a["id"]) in set(act_ids)]
        view = {"reveal_order": [str(a["id"]) for a in listed],
                "acts": [{"id": str(a["id"]), "do_not_reveal": a.get("do_not_reveal") or []} for a in listed]}
        if listed and listed[-1] is self.acts[-1] and self.plan.get("ending_strategy") is not None:
            view["ending_strategy"] = self.plan["ending_strategy"]
        return view

    def inspector_plan_view(self) -> dict:
        """What the English film inspector reads of the plan: acts, purposes, the ending."""
        view = {"acts": [{k: a.get(k) for k in ("id", "title", "purpose") if a.get(k) is not None}
                         for a in self.acts]}
        if self.plan.get("ending_strategy") is not None:
            view["ending_strategy"] = self.plan["ending_strategy"]
        return view

    def _follow_up_part(self) -> dict:
        return {"follow_up": self.follow_up} if self.follow_up else {}

    # -- text blocks ---------------------------------------------------------
    def draft_block(self) -> str:
        """Stage 1: every fact (beyond the 100-fact cap too), with its sources."""
        return "SHARED MATERIAL (JSON, the same for every language of this case)\n" + dump({
            "plan": self.plan_view(),
            "evidence": _facts(self.draft_pack, _DRAFT_FACT_KEYS, with_sources=True),
            "timeline": _timeline(self.draft_pack),
            "contradictions": _contradictions(self.draft_pack),
            "approved_context": _context(self.draft_pack),
            **self._follow_up_part()})

    def act_package(self, act: dict, extra_ids: list[str], related: dict[str, list[str]]) -> str:
        """Stage 1 for one act: the evidence of that act only, in the same shape as the whole block. The
        facts the plan assigned to the act and the extra facts nearest to it; the timeline entries of those
        facts and the ones assigned; the contradictions assigned to the act together with the facts that
        stand against them (`related`: contradiction id -> fact ids), so a dispute arrives complete. A fact
        that says the same as another (same claim) is listed once with the sources of both."""
        wanted = {str(i) for i in (act.get("evidence_ids") or [])} | set(extra_ids)
        contradictions = [c for c in self.draft_pack.get("contradictions") or [] if c["id"] in wanted]
        for contradiction in contradictions:
            wanted |= set(related.get(contradiction["id"], []))
        facts = _unique_claims([f for f in _facts(self.draft_pack, _DRAFT_FACT_KEYS, with_sources=True)
                                if f["id"] in wanted])
        claims = {f["claim"] for f in facts}
        timeline = [t for t in _timeline(self.draft_pack) if t["id"] in wanted or t["claim"] in claims]
        return f"ACT PACKAGE {act['id']} (JSON: the evidence of this act only)\n" + dump({
            "plan": self.plan_view(), "evidence": facts, "timeline": timeline,
            "contradictions": [{k: c.get(k) for k in ("id", "topic", "description", "severity")}
                               for c in contradictions],
            **self._follow_up_part()})

    def audit_evidence(self) -> dict:
        """What the evidence audit reads: everything the draft stage used. Every promoted fact (same ids
        and excerpts) with the sources that state it, those sources, and the timeline, the contradictions
        and the approved context the writer also received; a claim the writer took from one of those is
        supported. A fact's text is its claim, its excerpt its supporting text."""
        facts = [{"id": f["id"], "text": f.get("claim"), "source_ids": f.get("source_ids") or [],
                  "excerpt": f.get("supporting_text"), "uncertain": f.get("uncertain"),
                  "quote_status": f.get("quote_status")}
                 for f in self.draft_pack.get("facts") or []]
        used = {i for f in facts for i in f["source_ids"]}
        return {"facts": facts, "sources": [s for s in self.sources if s["id"] in used],
                "timeline": _timeline(self.draft_pack), "contradictions": _contradictions(self.draft_pack),
                "approved_context": _context(self.draft_pack)}

    def key_fact_ids(self, count: int) -> list[str]:
        """The strongest facts of the evidence pack (the pack is ordered by strength)."""
        return [f["id"] for f in (self.pack.get("facts") or [])[:count]]


def load_material(db: Session, case: Case, plan: StoryPlan | None,
                  follow_up: dict | None = None) -> SharedMaterial:
    """The plan row and the evidence of the case as it is now. Without a plan (an estimate
    before the plan exists) the plan part is empty."""
    facts = db.query(Fact).filter(Fact.case_id == case.id).all()
    contradictions = db.query(Contradiction).filter(Contradiction.case_id == case.id).all()
    sources = db.query(Source).filter(Source.case_id == case.id).all()
    pack = build_evidence_pack(facts, contradictions, sources)
    limit = ai_config.story_quality.max_evidence_for_long_draft
    extra = None if limit is None else max(limit - len(pack["facts"]), 0)
    draft_pack = build_evidence_pack(facts, contradictions, sources, extra_facts=extra)
    return SharedMaterial(
        case_id=case.id, plan_id=plan.id if plan else 0,
        plan=json.loads(plan.plan_json) if plan else {}, pack=pack,
        draft_pack=draft_pack, fingerprint=evidence_fingerprint(pack), follow_up=follow_up,
        sources=tuple({"id": s.id, "title": s.title, "type": s.source_type} for s in sources))
