"""C3 live proof: eight consecutive beats (B13-B20) of case 15's own approved narration, read-only, copied
into a NEW scratch case and run through the real Detective Director and Film Experience Reviewer. Case 15 is
over its EUR 300 cap, so nothing here is attributed to it: the script refuses to run against it and compares
its spend before and after. Standalone script (not pytest), same convention as Sections C/G/H/J/K."""
import asyncio
import json
import re
from uuid import uuid4

from sqlalchemy import text

from app.core.ai_config import ai_config
from app.db.base import SessionLocal
from app.db.models import Case, EditorialBlueprint, RevealGraph, StoryVersion
from app.documentary.detective import toolkit
from app.documentary.detective.review import persist_findings, review
from app.documentary.memory import add_narration_unit

REAL_CASE, REAL_BLUEPRINT = 15, 7
BEATS = [f"B{n}" for n in range(13, 21)]
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"])")


def spend(db, case_id):
    return db.execute(text("select coalesce(sum(cost_eur), 0) from provider_calls where case_id=:c"),
                      {"c": case_id}).scalar()


def real_excerpt(db):
    """(beat id, purpose, [sentence, ...]) per beat, read only from case 15's blueprint and story text."""
    row = db.execute(text("select story_version_id, blueprint_json from editorial_blueprints where id=:b"),
                     {"b": REAL_BLUEPRINT}).fetchone()
    blueprint = json.loads(row[1])
    story = db.execute(text("select story_text from story_versions where id=:i"), {"i": row[0]}).scalar()
    paragraphs = [p for p in re.split(r"\n\s*\n", story.strip()) if p.strip()]
    out = []
    for beat in blueprint["beats"]:
        if beat["id"] in BEATS:
            lo, hi = beat["paragraphs"]
            sentences = [s.strip() for p in paragraphs[lo - 1:hi] for s in SENTENCE_END.split(p) if s.strip()]
            out.append((beat["id"], beat.get("purpose") or "investigation", sentences))
    return out


async def main():
    db = SessionLocal()
    try:
        before = spend(db, REAL_CASE)
        excerpt = real_excerpt(db)
        case = Case(canonical_title="C3 live proof (real excerpt B13-B20, case 15 source)",
                    slug=f"c3-live-proof-{uuid4().hex[:10]}", language="en")
        db.add(case)
        db.flush()
        assert case.id != REAL_CASE, "never run against case 15"
        story = StoryVersion(case_id=case.id, version=1, kind="final", language="en", narrative_angle="true_crime",
                             story_text="\n\n".join(" ".join(s) for _, _, s in excerpt), engagement_score=0.0)
        db.add(story)
        db.flush()
        blueprint = EditorialBlueprint(case_id=case.id, story_version_id=story.id, version=1, status="valid",
                                       blueprint_json=json.dumps({"beats": [{"id": b, "purpose": p}
                                                                            for b, p, _ in excerpt]}))
        db.add(blueprint)
        db.flush()
        graph = RevealGraph(case_id=case.id, blueprint_id=blueprint.id, version=1, status="validated")
        db.add(graph)
        db.flush()
        order = 0
        for beat_id, _purpose, sentences in excerpt:
            for n, sentence in enumerate(sentences, 1):
                add_narration_unit(db, graph.id, unit_key=f"{beat_id}-{n}", beat_id=beat_id, story_order=order,
                                   language="en", text=sentence)
                order += 1
        db.commit()
        print("CASE_ID", case.id, "GRAPH_ID", graph.id, "UNITS", order, flush=True)

        ai_config.detective_toolkit.enabled = True
        try:
            print("TOOLKIT_RESULT", await toolkit.invoke(db, case, blueprint, graph), flush=True)
        finally:
            ai_config.detective_toolkit.enabled = False

        raw = await review(db, graph.id, mode="plan_audit", language="en", text_version=1)
        db.commit()
        print("REVIEW_OUTCOME", raw.get("outcome"), raw.get("inspection_modes"), flush=True)
        print("REVIEW_SUMMARY", raw.get("summary"), flush=True)
        findings = persist_findings(db, graph.id, raw)
        db.commit()
        for f in findings:
            print(" FINDING", f.severity, f.repair_owner, f.observed_problem[:140], flush=True)
        after = spend(db, REAL_CASE)
        print("CASE15_BEFORE", before, "AFTER", after, "UNCHANGED", before == after)
        print("SCRATCH_COST", spend(db, case.id))
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
