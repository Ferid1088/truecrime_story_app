"""Row counts and max ids of everything case 15 owns, so a planning pass can be diffed afterwards.
Usage: python3 -I snapshot.py out.json   (read-only)"""
import json
import sys

sys.path.insert(0, ".")
from sqlalchemy import text  # noqa: E402

from app.db.base import engine  # noqa: E402

CASE = 15


def main(out: str) -> None:
    snap: dict[str, dict] = {}
    with engine.connect() as c:
        tables = [r[0] for r in c.execute(text(
            "select table_name from information_schema.columns "
            "where table_schema='public' and column_name='case_id' order by table_name"))]
        for t in tables:
            n, mx = c.execute(text(f'select count(*), max(id) from "{t}" where case_id=:c'), {"c": CASE}).one()
            snap[t] = {"count": n, "max_id": mx}
        indirect = {
            "mvp_requirements": "visual_plan_id in (select id from visual_plans where case_id=15)",
            "mvp_search_rounds": "requirement_id in (select id from mvp_requirements where visual_plan_id in "
                                 "(select id from visual_plans where case_id=15))",
            "mvp_candidates": "requirement_id in (select id from mvp_requirements where visual_plan_id in "
                              "(select id from visual_plans where case_id=15))",
            "reveal_nodes": "graph_id in (select id from reveal_graphs where case_id=15)",
            "narration_units": "graph_id in (select id from reveal_graphs where case_id=15)",
            "release_anchors": "graph_id in (select id from reveal_graphs where case_id=15)",
            "detective_claims": "graph_id in (select id from reveal_graphs where case_id=15)",
            "detective_questions": "graph_id in (select id from reveal_graphs where case_id=15)",
            "detective_relations": "graph_id in (select id from reveal_graphs where case_id=15)",
            "detective_knowledge_events": "graph_id in (select id from reveal_graphs where case_id=15)",
            "detective_scenes": "graph_id in (select id from reveal_graphs where case_id=15)",
            "detective_findings": "graph_id in (select id from reveal_graphs where case_id=15)",
            "detective_tool_actions": "scene_id in (select id from detective_scenes where graph_id in "
                                      "(select id from reveal_graphs where case_id=15))",
        }
        for t, where in indirect.items():
            try:
                n, mx = c.execute(text(f'select count(*), max(id) from "{t}" where {where}')).one()
                snap[t] = {"count": n, "max_id": mx}
            except Exception as e:  # a table without that shape is reported, not hidden
                snap[t] = {"error": str(e)[:120]}
        case = c.execute(text("select status, budget_spend_baseline_eur, total_cost_cap_eur, location, language "
                              "from cases where id=15")).one()
        snap["_case"] = {"status": case[0], "baseline": case[1], "cap_field": case[2], "location": case[3],
                         "language": case[4]}
        snap["_film_reviews"] = [dict(r._mapping) for r in c.execute(text(
            "select id, language, production_script_id, render_sha256, state from mvp_film_reviews "
            "where case_id=15 order by id"))]
    json.dump(snap, open(out, "w"), indent=1, default=str)
    print(f"wrote {out}: {len(snap)} entries")


main(sys.argv[1])
