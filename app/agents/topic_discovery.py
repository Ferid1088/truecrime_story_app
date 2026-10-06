import json
from rapidfuzz import fuzz
from sqlalchemy.orm import Session
from app.core.ai_config import ai_config
from app.db.models import Case, DiscoveryCandidate
from app.providers.generation import get_generation_provider
from app.services.search import get_search_provider
from app.services.youtube import YouTubeSearchService
from app.services.tracking import track_run, stamp_run
from app.utils import fingerprint


class TopicDiscoveryAgent:
    def __init__(self):
        self.gen = get_generation_provider()
        self.search = get_search_provider()
        self.youtube = YouTubeSearchService()

    async def discover(
        self,
        db: Session,
        count: int,
        languages: list[str],
        theme: str,
        prefer_undercovered: bool,
        search_web: bool = True,
        search_youtube: bool = True,
        require_multiple_sources: bool = False,
        avoid_existing: bool = True,
    ) -> list[dict]:
        existing = db.query(Case).all()
        existing_titles = [c.canonical_title for c in existing]
        seen_fingerprints = {
            row.fingerprint for row in db.query(DiscoveryCandidate.fingerprint).all()
        }

        evidence = []
        seed_queries = [
            f"{theme} unsolved case unusual investigation",
            f"{theme} court case disappearance homicide",
            f"{theme} documentary case investigation",
        ]

        for q in seed_queries:
            if search_web:
                evidence.extend(await self.search.search(q, max_results=6))
            if search_youtube:
                evidence.extend(await self.youtube.search(q, max_results=6))

        compact = [
            {
                "title": x.get("title", ""),
                "url": x.get("url", ""),
                "snippet": x.get("content") or x.get("description", ""),
            }
            for x in evidence[:30]
        ]

        system = """
You are a senior true-crime research producer.
Your job is to propose DISTINCT real-world cases that could support a deeply researched,
original long-form narrative.

Rules:
- Do not invent cases.
- Prefer cases with enough public reporting to support verification.
- Avoid cases already covered in the provided existing-title list.
- Do not optimize for gore; optimize for mystery, human stakes, investigation, contradictions,
  unusual evidence, legal twists, or surprising chronology.
- Return strict JSON only.
"""

        user = json.dumps(
            {
                "requested_count": count,
                "languages": languages,
                "prefer_undercovered": prefer_undercovered,
                "require_multiple_sources": require_multiple_sources,
                "existing_titles": existing_titles,
                "search_evidence": compact,
                "output_schema": {
                    "candidates": [
                        {
                            "title": "canonical case title",
                            "rationale": "why this has narrative potential",
                            "narrative_potential": "short assessment of dramatic/narrative strength",
                            "languages_available": ["en", "de"],
                            "source_richness": "low|medium|high",
                            "angles": ["possible narrative angle", "another angle"],
                            "suggested_queries": ["query 1", "query 2", "query 3"],
                        }
                    ]
                },
            },
            ensure_ascii=False,
        )

        with track_run(
            db,
            case_id=None,
            agent_name="Discovery Agent",
            input_summary=f"count={count} langs={','.join(languages)} theme={theme}",
        ) as run:
            data, res = await self.gen.generate_structured("discovery", system, user)
            stamp_run(run, res, "discovery")
            candidates = []

            for item in data.get("candidates", []):
                title = item["title"].strip()

                similar_title = next(
                    (
                        old
                        for old in existing_titles
                        if fuzz.token_set_ratio(title, old)
                        >= ai_config.research.dedupe_similarity_threshold
                    ),
                    None,
                )
                fp = fingerprint(title)
                already_seen = fp in seen_fingerprints
                already_covered = bool(similar_title) or already_seen

                if avoid_existing and already_covered:
                    continue
                if len(candidates) >= count:
                    break

                row = DiscoveryCandidate(
                    title=title,
                    query=" | ".join(item.get("suggested_queries", [])),
                    rationale=item.get("rationale", ""),
                    fingerprint=fp,
                )
                if not already_seen:
                    db.add(row)
                    db.flush()
                    seen_fingerprints.add(fp)
                else:
                    row = db.query(DiscoveryCandidate).filter_by(fingerprint=fp).first()

                candidates.append(
                    {
                        "candidate_id": row.id,
                        "title": title,
                        "rationale": item.get("rationale", ""),
                        "narrative_potential": item.get("narrative_potential", ""),
                        "languages_available": item.get("languages_available", []),
                        "source_richness": item.get("source_richness", "unknown"),
                        "angles": item.get("angles", []),
                        "suggested_queries": item.get("suggested_queries", []),
                        "already_covered": already_covered,
                        "matched_existing_title": similar_title,
                    }
                )

            run.output_summary = f"candidates={len(candidates)}"
        db.commit()
        return candidates
