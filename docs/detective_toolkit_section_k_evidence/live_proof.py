"""Section K live proof: a real excerpt from case 15's own narration, run through the detective
toolkit in a dedicated scratch case (never case 15 itself — see the budget constraint in the
directive). Run as a standalone script (not pytest), same convention as Sections C/G/H/J."""
import asyncio
import json
from uuid import uuid4

from app.db.base import SessionLocal
from app.db.models import Case, EditorialBlueprint, RevealGraph, StoryVersion
from app.core.ai_config import ai_config
from app.documentary.detective import toolkit
from app.documentary.memory import add_narration_unit

EXCERPT = [
    ("B01", "Caleb Flynn called 911 from his home on Cunningham Court. He told the dispatcher "
            "somebody broke into his home and shot his wife. He said the garage door was "
            "standing wide open, and that someone had come in and shot Ashley while she was in bed."),
    ("B02", "Officers found Ashley Flynn under the covers, shot twice in the head. Near the foot "
            "of the bed lay two spent cartridge casings. Nothing suggested she had gotten out of "
            "bed or struggled with whoever came up to her."),
    ("B03", "Early reporting did not agree on the older daughter's age: The Guardian said the "
            "sisters were nine and eleven, while NBC News said nine and twelve."),
]


async def main():
    db = SessionLocal()
    try:
        slug = f"section-k-live-proof-{uuid4().hex[:10]}"
        case = Case(canonical_title="Section K live proof (real excerpt, case 15 source)",
                    slug=slug, language="en")
        db.add(case)
        db.flush()
        story = StoryVersion(case_id=case.id, version=1, kind="final", language="en",
                             narrative_angle="true_crime", story_text="\n\n".join(t for _, t in EXCERPT),
                             engagement_score=0.0)
        db.add(story)
        db.flush()
        blueprint = EditorialBlueprint(
            case_id=case.id, story_version_id=story.id, version=1, status="valid",
            blueprint_json=json.dumps({"beats": [{"id": b, "purpose": "investigation"} for b, _ in EXCERPT]}),
        )
        db.add(blueprint)
        db.flush()
        graph = RevealGraph(case_id=case.id, blueprint_id=blueprint.id, version=1, status="validated")
        db.add(graph)
        db.flush()
        for i, (beat_id, text) in enumerate(EXCERPT):
            add_narration_unit(db, graph.id, unit_key=f"{beat_id}-1", beat_id=beat_id,
                               story_order=i, language="en", text=text)
        db.commit()
        print("CASE_ID", case.id, "GRAPH_ID", graph.id, "BLUEPRINT_ID", blueprint.id)

        ai_config.detective_toolkit.enabled = True
        try:
            result = await toolkit.invoke(db, case, blueprint, graph)
        finally:
            ai_config.detective_toolkit.enabled = False
        print("TOOLKIT_RESULT", result)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
