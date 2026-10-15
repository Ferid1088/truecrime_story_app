"""Seed the isolated stack for the manual-approval + pilot browser test.

A master story its own critics rejected (status "needs_revision", version 5)
sits on a case with nothing else — no research, no sources. Approving it
"as is" from Story Studio and then starting a pilot must not run the master
critics again (no provider calls for that stage): the pilot reuses this
exact version, never writes a new one from zero.
"""

from __future__ import annotations

import json

from sqlalchemy import inspect, text

from app.core.test_database_guard import assert_safe_test_database
from app.db.base import SessionLocal
from app.db.models import Case, StoryVersion
from app.utils import text_hash

DB_NAME = "truecrime_approve_pilot_e2e"


def main() -> None:
    with SessionLocal() as db:
        bind = db.get_bind()
        assert_safe_test_database(bind.url.render_as_string(hide_password=False))
        if bind.dialect.name != "postgresql" or bind.url.database != DB_NAME:
            raise RuntimeError(f"approve+pilot E2E seed is restricted to PostgreSQL database {DB_NAME}")
        tables = [name for name in inspect(bind).get_table_names() if name != "alembic_version"]
        if tables:
            quoted = ", ".join(f'"{name.replace(chr(34), chr(34) * 2)}"' for name in tables)
            db.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
            db.commit()

        case = Case(canonical_title="Approve and Pilot E2E Case", slug="approve-pilot-e2e",
                    language="en", status="story_ready", resolution_status="UNSOLVED",
                    summary="A master with open critic problems, for the manual-approval flow.")
        db.add(case)
        db.flush()

        master_text = (
            "A lighthouse keeper logged one final signal before the tower went dark. "
            "Investigators never recovered the transmitter, and the case stayed open for decades."
        )
        master = StoryVersion(
            case_id=case.id, version=5, kind="master", language="en",
            narrative_angle=json.dumps({"title": "The Final Signal",
                                        "central_question": "What happened after the signal?"}),
            story_text=master_text, status="needs_revision", is_best=True,
            text_hash=text_hash(master_text), engagement_score=56.0,
            critic_notes=json.dumps({
                "score": 56,
                "problems": [
                    "The transmitter's disappearance is asserted without a source.",
                    "No scene or sensory detail anchors the keeper as a person.",
                ],
                "quality_gates": {
                    "pass": False,
                    "failures": ["grounding", "engagement_below_threshold"],
                    "words": len(master_text.split()),
                    "target_words": 50,
                    "grounding_score": 0.6,
                },
            }),
        )
        db.add(master)
        db.commit()
        print(json.dumps({"case_id": case.id, "master_version_id": master.id,
                          "database": bind.url.database}))


if __name__ == "__main__":
    main()
