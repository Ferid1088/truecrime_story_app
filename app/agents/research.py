import json
from sqlalchemy.orm import Session
from app.core.ai_config import ai_config
from app.db.models import Case, Source, Fact, Contradiction
from app.providers.generation import get_generation_provider
from app.services.tracking import track_run, stamp_run


def _source_bundle(sources: list[Source]) -> list[dict]:
    return [
        {
            "id": s.id,
            "title": s.title,
            "url": s.url,
            "type": s.source_type,
            "publisher": s.publisher,
            "reliability_score": s.reliability_score,
            "language": s.language,
            "content_status": s.content_status,
            "source_family": s.source_family,
            "text": (
                s.raw_text if s.is_authorized_text and not s.chunks else None
            ),
            "summary": s.summary,
            "summary_en": s.summary_en,
            "notes": s.notes,
            "chunks": [
                {"id": c.id, "text": c.text}
                for c in (s.chunks or [])[
                    : ai_config.source_chunking.max_chunks_per_source_for_extraction
                ]
            ],
        }
        for s in sources
    ]


class ResearchAgent:
    """Fact extraction -> timeline dating -> contradiction analysis.

    Each stage uses its own configured role. The provider's *possible* facts and
    contradictions are never promoted directly — they arrive only as hints
    the analyzer must corroborate against source material.
    """

    def __init__(self):
        self.gen = get_generation_provider()

    async def run(
        self,
        db: Session,
        case: Case,
        contradiction_hints: list | None = None,
    ) -> dict:
        sources = db.query(Source).filter(Source.case_id == case.id).all()

        previous_status = case.status
        case.status = "researching"
        db.commit()
        try:
            facts_data = await self._extract_facts(db, case, sources)
            fact_rows = self._store_facts(db, case, facts_data)

            await self._build_timeline(db, case, fact_rows, sources)

            contradictions = await self._analyze_contradictions(
                db, case, fact_rows, sources, contradiction_hints or []
            )
            n_contra = self._store_contradictions(db, case, contradictions)

            case.status = "researched"
            db.commit()
        except Exception:
            case.status = previous_status
            db.commit()
            raise
        return {"facts": len(fact_rows), "contradictions": n_contra}

    # ------------------------------------------------------------------
    # fact_extractor (cheap)
    # ------------------------------------------------------------------

    async def _extract_facts(self, db: Session, case: Case, sources: list[Source]) -> list[dict]:
        system = """
You are a forensic research analyst for a documentary newsroom.

Extract only claims supported by the supplied material.
Separate established facts from disputed claims (set "disputed": true for
claims that are uncertain, contested or attributed to a single weak source).
Never invent dialogue, motives, forensic results, dates, or causal claims.
For each fact set "event_date" to an ISO date (YYYY-MM-DD or YYYY-MM or YYYY)
only when the claim references a specific date stated in the material.
Otherwise set it to null. Never guess a date.

CANONICAL LANGUAGE: write every "claim" in English — it is the canonical
evidence representation used by all downstream reasoning, regardless of the
source language. For claims extracted from non-English sources, also set
"original_claim" to the claim in the source's original language and
"original_language" to that source's ISO code. For English sources leave
"original_claim" null. Preserve names, numbers, dates and legal nuance
exactly — normalization is factual fidelity, not paraphrase.
Set "narrative_value" to one of:
fact|timeline|human_detail|scene_detail|investigation_detail|
physical_evidence|legal|historical_context|quote|contradiction|
environment|procedure.
Use "human_detail" for documented personal context: family relations,
roles, occupations, duties, routines, documented behavior, correspondence,
known statements. Use "scene_detail"/"environment" only for documented
physical observations: layout, weather, objects, distances, measurements,
visible conditions — never inferred atmosphere.
For direct quotations use narrative_value "quote", set "quote_status" to
verified_direct|reported_quote|paraphrase|uncertain and "speaker" to the
attributed speaker. Never convert a paraphrase into a direct quote.
Set "evidence_strength" to one of:
primary|strong_secondary|secondary|tertiary|weak|disputed
(primary = official record/eyewitness document/transcript; strong_secondary
= reputable corroborated reporting; tertiary/weak = derivative or thin).
Set "supporting_text" to the brief verbatim passage (original language)
that grounds the claim. Set "chunk_ids" to the ids of chunks the claim
came from when chunk material was used.
Set "people" and "locations" to the names of people/places central to the
claim (short lists, [] if none).

Return JSON only.
"""
        user = json.dumps(
            {
                "case": case.canonical_title,
                "sources": _source_bundle(sources),
                "output_schema": {
                    "facts": [
                        {
                            "claim": "...",
                            "category": "timeline|person|evidence|investigation|legal|context",
                            "confidence": 0.0,
                            "source_ids": [1, 2],
                            "disputed": False,
                            "event_date": "YYYY-MM-DD or null",
                            "original_claim": "claim in the source language or null",
                            "original_language": "en",
                            "narrative_value": "human_detail|scene_detail|investigation_detail|...",
                            "evidence_strength": "primary|strong_secondary|secondary|tertiary|weak|disputed",
                            "supporting_text": "verbatim passage or null",
                            "chunk_ids": [1],
                            "quote_status": "verified_direct|reported_quote|paraphrase|uncertain|null",
                            "speaker": "name or null",
                            "people": ["..."],
                            "locations": ["..."],
                        }
                    ]
                },
            },
            ensure_ascii=False,
        )
        with track_run(
            db, case.id, "Fact Extractor",
            input_summary=f"case={case.id} sources={len(sources)}",
        ) as run:
            data, res = await self.gen.generate_structured("fact_extractor", system, user)
            stamp_run(run, res, "fact_extractor")
            facts = data.get("facts", [])
            run.output_summary = f"facts={len(facts)}"
        return facts

    def _store_facts(self, db: Session, case: Case, facts_data: list[dict]) -> list[Fact]:
        db.query(Fact).filter(Fact.case_id == case.id).delete()
        rows = []
        for f in facts_data:
            row = Fact(
                case_id=case.id,
                claim=f["claim"],
                original_claim=f.get("original_claim") or None,
                original_language=(f.get("original_language") or "en")[:20],
                category=f.get("category", "general"),
                confidence=float(f.get("confidence", 0.5)),
                source_ids_json=json.dumps(f.get("source_ids", [])),
                disputed=bool(f.get("disputed", False)),
                event_date=f.get("event_date") or None,
                narrative_value=(f.get("narrative_value") or None),
                evidence_strength=(f.get("evidence_strength") or None),
                supporting_text=(f.get("supporting_text") or None),
                chunk_ids_json=json.dumps(f.get("chunk_ids") or []),
                quote_status=(f.get("quote_status") or None),
                speaker=(f.get("speaker") or None),
                people_json=json.dumps(
                    [p for p in (f.get("people") or []) if p],
                    ensure_ascii=False,
                ),
                locations_json=json.dumps(
                    [l for l in (f.get("locations") or []) if l],
                    ensure_ascii=False,
                ),
            )
            db.add(row)
            rows.append(row)
        db.flush()
        return rows

    # ------------------------------------------------------------------
    # timeline_builder (cheap)
    # ------------------------------------------------------------------

    async def _build_timeline(
        self, db: Session, case: Case, fact_rows: list[Fact], sources: list[Source]
    ) -> None:
        """Refine event_date assignments — approximate dates allowed, never invented."""
        if not fact_rows:
            return
        system = """
You are building a documentary timeline from extracted claims.
For each claim, return the best-supported date from the source material.
Use YYYY-MM-DD, YYYY-MM or YYYY. If the timing is approximate, keep the
coarser form rather than inventing a day. If no source supports a date,
return null. Never guess.

Return JSON only.
"""
        user = json.dumps(
            {
                "case": case.canonical_title,
                "claims": [
                    {"id": f.id, "claim": f.claim, "current_event_date": f.event_date}
                    for f in fact_rows
                ],
                "sources": _source_bundle(sources),
                "output_schema": {
                    "timeline": [{"id": 1, "event_date": "YYYY-MM-DD or null"}]
                },
            },
            ensure_ascii=False,
        )
        with track_run(
            db, case.id, "Timeline Builder",
            input_summary=f"claims={len(fact_rows)}",
        ) as run:
            data, res = await self.gen.generate_structured("timeline_builder", system, user)
            stamp_run(run, res, "timeline_builder")
            dated = 0
            by_id = {f.id: f for f in fact_rows}
            for item in data.get("timeline", []):
                fact = by_id.get(item.get("id"))
                if fact and item.get("event_date"):
                    fact.event_date = item["event_date"]
                    dated += 1
            run.output_summary = f"dated={dated}"
        db.commit()

    # ------------------------------------------------------------------
    # contradiction_analyzer (cheap)
    # ------------------------------------------------------------------

    async def _analyze_contradictions(
        self,
        db: Session,
        case: Case,
        fact_rows: list[Fact],
        sources: list[Source],
        hints: list,
    ) -> list[dict]:
        system = """
You are a contradiction analyst for an investigative newsroom.
Identify claims where sources or extracted facts genuinely conflict —
different dates, different actors, mutually exclusive accounts.
"research_hints" are leads found during internet research: corroborate each
against the supplied facts/sources before including it; drop hints that the
material does not actually support. Do not promote a hint just because it
sounds interesting.

Return JSON only.
"""
        user = json.dumps(
            {
                "case": case.canonical_title,
                "facts": [{"id": f.id, "claim": f.claim, "disputed": f.disputed} for f in fact_rows],
                "sources": _source_bundle(sources),
                "research_hints": hints,
                "output_schema": {
                    "contradictions": [
                        {
                            "topic": "...",
                            "description": "version A vs version B, with who says what",
                            "source_ids": [1, 2],
                            "severity": "low|medium|high",
                        }
                    ]
                },
            },
            ensure_ascii=False,
        )
        with track_run(
            db, case.id, "Contradiction Analyzer",
            input_summary=f"facts={len(fact_rows)} hints={len(hints)}",
        ) as run:
            data, res = await self.gen.generate_structured(
                "contradiction_analyzer", system, user
            )
            stamp_run(run, res, "contradiction_analyzer")
            contradictions = data.get("contradictions", [])
            run.output_summary = f"contradictions={len(contradictions)}"
        return contradictions

    def _store_contradictions(
        self, db: Session, case: Case, contradictions: list[dict]
    ) -> int:
        db.query(Contradiction).filter(Contradiction.case_id == case.id).delete()
        for c in contradictions:
            db.add(
                Contradiction(
                    case_id=case.id,
                    topic=c["topic"],
                    description=c["description"],
                    source_ids_json=json.dumps(c.get("source_ids", [])),
                    severity=c.get("severity", "medium"),
                )
            )
        return len(contradictions)
