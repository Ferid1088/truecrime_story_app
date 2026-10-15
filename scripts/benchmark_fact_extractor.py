"""Part 1 benchmark: fact_extractor ("cheap" role) candidates vs the Haiku baseline.

Real stored sources (read-only, from a real researched case) are fed to each
candidate model through the EXACT production call (app.agents.research's
fact-extraction prompt and schema), batched the same way production does.
Gold labels come from claude-sonnet-5-5. Scored: valid-JSON rate, agreement
with gold (fuzzy claim-text + source_id overlap), unsupported claims (is the
quoted supporting_text actually present in the source chunk it cites), mean
latency, and real EUR cost (from the ledger, attributed to a dedicated
benchmark case so it never touches the source case's own budget).

    python -m scripts.benchmark_fact_extractor <source_case_id> <benchmark_case_id> [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from contextlib import contextmanager
from difflib import SequenceMatcher

from app.agents.research import _FACT_SCHEMA, _source_bundle
from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.base import SessionLocal
from app.db.models import Case, Source
from app.providers.generation import get_generation_provider
from app.services.budget import BudgetService
from app.services.tracking import documentary_run_context, stamp_run, track_run

CANDIDATES = [
    "claude-haiku-4-5",  # baseline
    "gemini-3.8-flash",
    "qwen3.8-flash",
    "deepseek-v4-flash",
    "glm-5.3-flash",
]
GOLD_MODEL = "claude-sonnet-5-5"
ROLE_ALIAS = "cheap"  # config/models.json alias fact_extractor is routed to


@contextmanager
def _model_override(model_id: str):
    """Temporarily point the "cheap" alias at a specific concrete model, so
    run_agent("research.extract_facts", ...) — the real production call —
    runs unchanged against a different model."""
    section = ai_config.generation_provider()
    original = section.models[ROLE_ALIAS]
    section.models[ROLE_ALIAS] = model_id
    try:
        yield
    finally:
        section.models[ROLE_ALIAS] = original


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


async def _extract(db, bench_case: Case, bundle: list[dict], system: str, label: str) -> dict:
    gen = get_generation_provider()
    started = time.monotonic()
    with documentary_run_context(case_id=bench_case.id, pipeline_stage=f"benchmark:fact_extractor"):
        try:
            with track_run(db, bench_case.id, "Fact Extractor (benchmark)",
                           f"label={label}") as run:
                user = json.dumps(
                    {"case": bench_case.canonical_title, "sources": bundle,
                     "output_schema": _FACT_SCHEMA},
                    ensure_ascii=False,
                )
                data, res = await run_agent("research.extract_facts", gen, user, system=system)
                stamp_run(run, res, "fact_extractor")
                facts = data.get("facts", [])
                run.output_summary = f"facts={len(facts)}"
            return {"ok": True, "facts": facts, "elapsed_s": time.monotonic() - started}
        except Exception as e:
            return {"ok": False, "error": str(e)[:300], "elapsed_s": time.monotonic() - started}


def _unsupported_rate(facts: list[dict], bundle: list[dict]) -> float:
    """Share of facts whose supporting_text isn't a close match for any
    chunk/raw text of the source(s) it cites — a proxy for "not backed by
    the input text", the same shape the spec asks for."""
    chunk_text_by_source: dict[int, str] = {}
    for s in bundle:
        parts = [c["text"] for c in s.get("chunks", [])]
        if s.get("text"):
            parts.append(s["text"])
        chunk_text_by_source[s["id"]] = " ".join(parts)
    if not facts:
        return 0.0
    unsupported = 0
    for f in facts:
        supporting = f.get("supporting_text")
        if not supporting:
            continue  # nothing to check; not counted as unsupported
        source_text = " ".join(chunk_text_by_source.get(sid, "") for sid in f.get("source_ids", []))
        if not source_text:
            unsupported += 1
            continue
        # Closest substring-ish match: best sliding-window ratio against
        # same-length spans of the cited source text.
        best = 0.0
        words = source_text.split()
        span = max(len(supporting.split()), 5)
        for i in range(0, max(len(words) - span, 1), max(span // 2, 1)):
            window = " ".join(words[i:i + span * 2])
            best = max(best, _similar(supporting, window))
            if best > 0.6:
                break
        if best < 0.5:
            unsupported += 1
    return unsupported / len(facts)


def _agreement_with_gold(candidate: list[dict], gold: list[dict]) -> float:
    """F1-like agreement: a candidate fact matches a gold fact if their
    claim text is a close fuzzy match AND they share at least one
    source_id. Unmatched gold facts are misses; unmatched candidate facts
    are extras."""
    if not gold:
        return 1.0 if not candidate else 0.0
    matched_gold = set()
    matched_candidate = set()
    for ci, c in enumerate(candidate):
        best_j, best_score = None, 0.0
        for gi, g in enumerate(gold):
            if gi in matched_gold:
                continue
            if not (set(c.get("source_ids", [])) & set(g.get("source_ids", []))):
                continue
            score = _similar(c.get("claim", ""), g.get("claim", ""))
            if score > best_score:
                best_j, best_score = gi, score
        if best_j is not None and best_score >= 0.55:
            matched_gold.add(best_j)
            matched_candidate.add(ci)
    precision = len(matched_candidate) / len(candidate) if candidate else 0.0
    recall = len(matched_gold) / len(gold) if gold else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


async def main_async(source_case_id: int, bench_case_id: int, limit: int) -> None:
    db = SessionLocal()
    source_case = db.get(Case, source_case_id)
    bench_case = db.get(Case, bench_case_id)
    if source_case is None or bench_case is None:
        print("source or benchmark case not found", file=sys.stderr)
        sys.exit(1)

    sources = (db.query(Source)
               .filter(Source.case_id == source_case_id, Source.language == "en")
               .all())
    sources = [s for s in sources if s.chunks][:limit]
    print(f"Using {len(sources)} real English sources from case {source_case_id} "
          f"({source_case.canonical_title}); billing to case {bench_case_id} "
          f"({bench_case.canonical_title}).")

    system = prompt("agents/research/extract_facts")
    batch_size = ai_config.source_chunking.max_sources_per_extraction_call
    batches = [sources[i:i + batch_size] for i in range(0, len(sources), batch_size)]
    bundles = [_source_bundle(b) for b in batches]

    print(f"Running gold ({GOLD_MODEL}) over {len(bundles)} batches...")
    gold_results = []
    with _model_override(GOLD_MODEL):
        for i, bundle in enumerate(bundles):
            gold_results.append(await _extract(db, bench_case, bundle, system, f"gold-{i}"))
    gold_facts_by_batch = [r.get("facts", []) if r["ok"] else [] for r in gold_results]

    report: dict[str, dict] = {}
    for model_id in CANDIDATES:
        print(f"Running candidate {model_id} over {len(bundles)} batches...")
        results = []
        with _model_override(model_id):
            for i, bundle in enumerate(bundles):
                results.append(await _extract(db, bench_case, bundle, system, f"{model_id}-{i}"))
        valid = [r for r in results if r["ok"]]
        valid_json_rate = len(valid) / len(results) if results else 0.0
        agreements = [
            _agreement_with_gold(r["facts"], gold_facts_by_batch[i])
            for i, r in enumerate(results) if r["ok"]
        ]
        unsupported_rates = [
            _unsupported_rate(r["facts"], bundles[i])
            for i, r in enumerate(results) if r["ok"]
        ]
        latencies = [r["elapsed_s"] for r in results]
        report[model_id] = {
            "batches": len(results),
            "valid_json_rate": round(valid_json_rate, 3),
            "mean_agreement_with_gold": round(statistics.fmean(agreements), 3) if agreements else None,
            "mean_unsupported_rate": round(statistics.fmean(unsupported_rates), 3) if unsupported_rates else None,
            "mean_latency_s": round(statistics.fmean(latencies), 1),
            "total_facts_extracted": sum(len(r.get("facts", [])) for r in results),
        }

    snapshot = BudgetService(db).snapshot(bench_case_id)
    print("\n=== Report (see docs/benchmarks/models.md) ===")
    print(json.dumps(report, indent=2))
    print(f"\nTotal benchmark spend (case {bench_case_id}): EUR {snapshot.spent_eur:.6f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_case_id", type=int)
    parser.add_argument("benchmark_case_id", type=int)
    parser.add_argument("--limit", type=int, default=16)
    args = parser.parse_args()
    asyncio.run(main_async(args.source_case_id, args.benchmark_case_id, args.limit))


if __name__ == "__main__":
    main()
