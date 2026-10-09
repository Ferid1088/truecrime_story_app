"""Live validation of case naming (+ optional thumbnail) on one researched case.

    python -m scripts.case_naming_report <case_id> [--thumbnail LANG] [--languages en,de,fa,ar]

Uses the configured generation provider and the bge-m3 embedder, reads only
stored data, and counts search calls (must be 0). Prints the Part-56 report.
Run it from the repository root with the API keys in .env."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.core.ai_config import ai_config
from app.db.base import SessionLocal
from app.db.models import Case, CaseTitleCandidate
from app.identity import titles as T
from app.naming import agent as A
from app.naming.api import _embedder
from app.naming.corpus import get_corpus
from app.providers.generation import get_generation_provider
from app.research_engine import search as SE


def count_search_calls() -> list[int]:
    n = [0]
    for cls in (SE.SearchBackendRegistry,):
        orig = cls.search

        async def counted(self, *a, _orig=orig, **k):
            n[0] += 1
            return await _orig(self, *a, **k)
        cls.search = counted
    try:
        from app.research_engine.search_adapters.searxng import SearXNGSearchBackend

        orig2 = SearXNGSearchBackend.search

        async def counted2(self, *a, _orig=orig2, **k):
            n[0] += 1
            return await _orig(self, *a, **k)
        SearXNGSearchBackend.search = counted2
    except Exception:  # noqa: BLE001
        pass
    return n


async def main(case_id: int, languages: list[str], thumb_lang: str | None) -> int:
    searches = count_search_calls()
    db = SessionLocal()
    case = db.get(Case, case_id)
    if case is None:
        print(f"case {case_id} not found")
        return 2
    gen = get_generation_provider()
    corpus = get_corpus(db)
    prov = T.resolution_provenance(db, case)
    print(f"# CASE {case.case_uid} — {case.canonical_title}")
    print(f"status: {prov['case_resolution_status']} (public: {prov['public_status']}) "
          f"confidence={prov['resolution_confidence']} sources={prov['resolution_sources']}")
    print("\n## Title collision corpus (stored data only)")
    for k, v in sorted(corpus.counts().items()):
        print(f"- {k}: {v}")
    result = await A.generate_case_titles(db, case, gen, _embedder(), languages)
    print(f"\nfamily {result['title_family_id']} — concept: {result['editorial_concept']}")
    for lang in languages:
        rows = (db.query(CaseTitleCandidate).filter_by(case_id=case.id, language=lang)
                .order_by(CaseTitleCandidate.id).all())
        live = sorted((r for r in rows if r.status != "rejected"),
                      key=lambda r: json.loads(r.critic_json).get("rank") or 99)
        print(f"\n## {lang.upper()} — {len(live)} eligible / {result['languages'][lang]['rounds']} round(s)")
        for r in live:
            c = json.loads(r.critic_json)
            star = "*" if c.get("recommended") else " "
            print(f" {star}{c.get('rank')}. {r.title}  near={r.near_collision_score or 0:.0f} "
                  f"sem={r.semantic_collision_score if r.semantic_collision_score is not None else '-'} "
                  f"mem={r.memorability_score} cur={r.curiosity_score} spec={r.specificity_score} "
                  f"spoiler={r.spoiler_risk} epist={r.epistemic_risk}")
        for r in rows:
            if r.status == "rejected":
                print(f"   x {r.title} — {r.rejection_reason}")
    print("\n## YouTube titles (recommended candidates)")
    for lang in languages:
        best = next((r for r in db.query(CaseTitleCandidate).filter_by(case_id=case.id, language=lang)
                     if json.loads(r.critic_json).get("recommended") and r.status != "rejected"), None)
        if best:
            try:
                print(f"{lang}: {T.build_youtube_title(best.title, case.resolution_status, lang)}")
            except T.NoPublicStatus as e:
                print(f"{lang}: no public title — {e}")
    if thumb_lang:
        from app.thumbnails import service as SV

        t = await SV.create_thumbnail(db, case, thumb_lang, gen)
        print(f"\n## Thumbnail ({thumb_lang}) v{t.version}: {SV.file_of(t)}")
        print(json.dumps(json.loads(t.critic_json)["scorecard"], indent=1))
    print(f"\nSEARCH CALLS DURING NAMING: {searches[0]} (expected 0)")
    return 0 if searches[0] == 0 else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("case_id", type=int)
    ap.add_argument("--languages", default=",".join(ai_config.channels))
    ap.add_argument("--thumbnail", default=None, help="language to compose a thumbnail for "
                    "(needs an approved title for that language)")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.case_id, a.languages.split(","), a.thumbnail)))
