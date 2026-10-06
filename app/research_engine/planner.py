"""LLM intelligence inside the engine — APIMaster only (Part 4).

The LLM plans queries and analyzes retrieved content; it never
searches the web itself. All calls route through the generation
provider's role system — no model IDs here.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

QUERY_PURPOSES = {
    "case_identity", "official_record", "court_record", "police",
    "coroner", "archive", "local_news", "national_news", "interview",
    "family", "victim_background", "suspect_background", "timeline",
    "location", "forensic", "investigation", "documentary", "youtube",
    "transcript", "historical_context", "contradiction", "theory",
    "follow_up",
}

_PLANNER_SYSTEM = """You are a multilingual investigative-research query planner for true-crime cases.

You receive a case brief and ONE target language. Produce search queries a NATIVE-SPEAKING investigative journalist in that language would actually type — never translations of English queries. Use native name spellings, transliteration variants, local place names, local media vocabulary and case aliases.

Rules:
- Queries must be in the target language (except proper nouns where Latin script is the local convention).
- Vary purpose: official records, local news, archives, documentary, interviews, background.
- 4-8 queries, ordered by expected evidentiary value.
- For fa/ar use the script of the language (Persian/Arabic orthography), include transliterated name variants as separate queries.
- Return strict JSON only."""


@dataclass
class PlannedQuery:
    query: str
    language: str
    purpose: str = "follow_up"
    priority: int = 3
    expected_source_types: list[str] = field(default_factory=list)
    round: int = 0


class ResearchQueryPlanner:
    """research_intelligence role — native query generation per language."""

    def __init__(self, generation_provider, telemetry: dict | None = None):
        self.gen = generation_provider
        self.tel = telemetry

    async def plan(
        self,
        case_context: dict,
        target_language: str,
        research_gaps: list[str] | None = None,
        previous_queries: list[str] | None = None,
        round_index: int = 0,
        max_queries: int = 6,
    ) -> list[PlannedQuery]:
        user = json.dumps({
            "task": "plan_search_queries",
            "target_language": target_language,
            "case": case_context,
            "known_research_gaps": research_gaps or [],
            "already_executed_queries_do_not_repeat": (previous_queries or [])[-25:],
            "round": round_index,
            "max_queries": max_queries,
            "allowed_purposes": sorted(QUERY_PURPOSES),
            "output_schema": {
                "queries": [{
                    "query": "native-language search string",
                    "language": target_language,
                    "purpose": "one of allowed_purposes",
                    "priority": "1 high - 5 low",
                    "expected_source_types": ["official|news|archive|documentary|forum"],
                }]
            },
        }, ensure_ascii=False)
        try:
            data, res = await self.gen.generate_structured(
                "research_query_planner", _PLANNER_SYSTEM, user)
            _acc(self.tel, res)
        except Exception as e:  # noqa: BLE001
            log.warning("query planning failed for %s: %s", target_language, e)
            return self._fallback_queries(case_context, target_language, round_index)
        out = []
        for q in (data or {}).get("queries") or []:
            try:
                text = (q.get("query") or "").strip()
                if not text or len(text) > 400:
                    continue
                pr_raw = q.get("priority")
                m = re.match(r"\s*(\d)", str(pr_raw)) if pr_raw is not None else None
                priority = max(1, min(int(m.group(1)) if m else 3, 5))
                out.append(PlannedQuery(
                    query=text,
                    language=(q.get("language") or target_language)[:10],
                    purpose=q.get("purpose")
                    if q.get("purpose") in QUERY_PURPOSES else "follow_up",
                    priority=priority,
                    expected_source_types=list(q.get("expected_source_types") or []),
                    round=round_index,
                ))
            except (AttributeError, TypeError, ValueError) as e:
                log.warning("skipping malformed planned query: %s", e)
                continue
            if len(out) >= max_queries:
                break
        if not out:
            return self._fallback_queries(case_context, target_language, round_index)
        return out

    def _fallback_queries(self, case_context: dict, language: str,
                          round_index: int) -> list[PlannedQuery]:
        """Deterministic seed queries when the planner is unavailable —
        the engine still functions."""
        title = (case_context.get("title") or "").strip()
        people = [p for p in (case_context.get("key_people") or [])[:3]]
        seeds = [title] + people
        queries = []
        for i, s in enumerate(seeds):
            if not s:
                continue
            queries.append(PlannedQuery(
                query=s, language=language,
                purpose="case_identity" if i == 0 else "follow_up",
                priority=1 if i == 0 else 3, round=round_index))
        return queries[:6]


_RERANK_SYSTEM = """You evaluate search results for a true-crime research engine.
For each result score relevance to the case (0-1), source credibility (0-1),
potential for NEW information (0-1), and whether fetching the full page is
worth it. Be strict — a search hit is not automatically valuable. JSON only."""


def _acc(tel: dict | None, res) -> None:
    """Accumulate a GenerationResult's usage into engine telemetry."""
    if tel is None or res is None:
        return
    tel["calls"] = tel.get("calls", 0) + 1
    tel["input_tokens"] = tel.get("input_tokens", 0) + int(
        getattr(res, "input_tokens", 0) or 0)
    tel["output_tokens"] = tel.get("output_tokens", 0) + int(
        getattr(res, "output_tokens", 0) or 0)
    cost = getattr(res, "cost_usd", None)
    if cost:
        tel["aux_cost_usd"] = tel.get("aux_cost_usd", 0.0) + float(cost)
        tel["cost_usd"] = tel.get("cost_usd", 0.0) + float(cost)
    model = getattr(res, "model", None)
    if model:
        tel.setdefault("models_used", [])
        if model not in tel["models_used"]:
            tel["models_used"].append(model)


class ResultReranker:
    """LLM rerank for the ambiguous middle band only (Part 24)."""

    def __init__(self, generation_provider, telemetry: dict | None = None):
        self.gen = generation_provider
        self.tel = telemetry

    async def rerank(self, case_context: dict,
                     candidates: list[dict]) -> dict[int, dict]:
        """candidates: [{idx, url, title, snippet}] -> {idx: scores}."""
        if not candidates:
            return {}
        user = json.dumps({
            "case": {k: case_context.get(k) for k in
                     ("title", "summary", "location")},
            "results": candidates[:15],
            "output_schema": {"evaluations": [{
                "idx": 0, "relevance": 0.0, "credibility": 0.0,
                "novelty": 0.0, "fetch_recommendation": True,
                "reason": "short"}]},
        }, ensure_ascii=False)
        try:
            data, res = await self.gen.generate_structured(
                "result_reranker", _RERANK_SYSTEM, user)
            _acc(self.tel, res)
        except Exception as e:  # noqa: BLE001
            log.warning("rerank failed: %s", e)
            return {}
        out = {}
        for ev in (data or {}).get("evaluations") or []:
            try:
                out[int(ev.get("idx"))] = {
                    "relevance": float(ev.get("relevance") or 0),
                    "credibility": float(ev.get("credibility") or 0),
                    "novelty": float(ev.get("novelty") or 0),
                    "fetch": bool(ev.get("fetch_recommendation")),
                    "reason": (ev.get("reason") or "")[:200],
                }
            except (TypeError, ValueError):
                continue
        return out


_GAP_SYSTEM = """You analyze a true-crime case's accumulated evidence after a research round.
Identify what is still MISSING for a complete investigative account — be specific
(e.g. "no official police statement", "victim's background undocumented",
"timeline gap between disappearance and discovery"). Only gaps researchable
on the open web. Also flag contradictions visible across the new material. JSON only."""


class GapAnalyzer:
    """research_evaluator role — post-round gap + novelty analysis."""

    def __init__(self, generation_provider, telemetry: dict | None = None):
        self.gen = generation_provider
        self.tel = telemetry

    async def analyze(self, case_context: dict,
                      new_sources: list[dict],
                      existing_gaps: list[str]) -> dict:
        user = json.dumps({
            "case": {k: case_context.get(k) for k in
                     ("title", "summary", "location", "year")},
            "newly_acquired_sources": [{
                "title": s.get("title"),
                "language": s.get("language"),
                "source_type": s.get("source_type"),
                "depth": s.get("content_status"),
                "summary": (s.get("summary") or "")[:600],
            } for s in new_sources[:20]],
            "previously_known_gaps": existing_gaps[:20],
            "output_schema": {
                "remaining_gaps": ["..."],
                "resolved_gaps": ["..."],
                "new_contradictions": ["..."],
                "most_valuable_new_source": "title or null",
                "novel_information_detected": True,
            },
        }, ensure_ascii=False)
        try:
            data, res = await self.gen.generate_structured(
                "research_evaluator", _GAP_SYSTEM, user)
            _acc(self.tel, res)
            return data or {}
        except Exception as e:  # noqa: BLE001
            log.warning("gap analysis failed: %s", e)
            return {}


_SUMMARY_SYSTEM = """You write a factual research-status summary for an investigative case file.
Only facts grounded in the listed sources. Note coverage by language and
which evidence areas remain thin. No speculation. JSON only."""


class ResearchAssembler:
    """research_assembler role — final per-run case summary."""

    def __init__(self, generation_provider, telemetry: dict | None = None):
        self.gen = generation_provider
        self.tel = telemetry

    async def summarize(self, case_context: dict,
                        sources: list[dict]) -> dict:
        user = json.dumps({
            "case": {k: case_context.get(k) for k in ("title", "summary")},
            "sources": [{
                "title": s.get("title"), "language": s.get("language"),
                "depth": s.get("content_status"),
                "summary": (s.get("summary") or "")[:400],
            } for s in sources[:30]],
            "output_schema": {
                "summary": "2-4 sentence factual status",
                "coverage": {"en": 0, "de": 0, "fa": 0, "ar": 0},
                "thin_areas": ["..."],
            },
        }, ensure_ascii=False)
        try:
            data, res = await self.gen.generate_structured(
                "research_assembler", _SUMMARY_SYSTEM, user)
            _acc(self.tel, res)
            return data or {}
        except Exception as e:  # noqa: BLE001
            log.warning("assembler failed: %s", e)
            return {}
