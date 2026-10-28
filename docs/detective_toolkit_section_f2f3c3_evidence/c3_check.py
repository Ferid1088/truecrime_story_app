"""Run the deterministic C3 / F3 checkers on the real Director output of a graph (read-only):
    PYTHONPATH=. python3 docs/detective_toolkit_section_f2f3c3_evidence/c3_check.py GRAPH_ID"""
import json
import sys

from sqlalchemy import text

from app.db.base import SessionLocal
from app.documentary.detective import pacing as P
from app.documentary.detective import text_revision as TR
from app.documentary.detective.dispatch import resolve_dispatchable_actions


def main(graph_id):
    db = SessionLocal()
    try:
        counts = {t: db.execute(text(f"select count(*) from {t} where graph_id=:g"), {"g": graph_id}).scalar()
                  for t in ("narration_units", "detective_claims", "detective_questions", "detective_relations",
                            "detective_knowledge_events", "detective_scenes")}
        counts["tool_actions"] = db.execute(text(
            "select count(*) from detective_tool_actions a join detective_scenes s on a.scene_id=s.id "
            "where s.graph_id=:g"), {"g": graph_id}).scalar()
        print("COUNTS", json.dumps(counts))
        for row in db.execute(text("select s.scene_key, a.tool_id, a.reuse_reason from detective_tool_actions a "
                                   "join detective_scenes s on a.scene_id=s.id where s.graph_id=:g "
                                   "order by s.scene_order, a.order_index"), {"g": graph_id}):
            print("ACTION", row[0], row[1], "| reuse:", (row[2] or "")[:90])
        report = P.c3_report(db, graph_id)
        for p in report["question_payoffs"]:
            print("QUESTION", p["ending"], p["status"], p["kind"], "|", p["text"][:90])
        print("RETURNS_WITHOUT_REASON", json.dumps(report["returns_without_reason"]))
        print("DENSE_RUNS", json.dumps(report["dense_runs"]))
        audio = TR.audio_only_findings(db, graph_id)
        print("AUDIO_ONLY_FINDINGS", len(audio))
        for f in audio:
            print("  -", f["problem"][:200])
        skips = [(a["action_key"], a.get("skip", "dispatchable")) for a in resolve_dispatchable_actions(db, graph_id)]
        print("DISPATCH", skips)
    finally:
        db.close()


main(int(sys.argv[1]))
