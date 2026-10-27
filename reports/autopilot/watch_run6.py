"""One-shot status snapshot for run 6 / case 15 — run via .venv/bin/python."""
import json
from sqlalchemy import text
from app.db.base import SessionLocal

with SessionLocal() as db:
    r = db.execute(text(
        "SELECT id, status, stage, steps_json, needs_you_json FROM autopilot_runs WHERE id=6"
    )).mappings().one()
    print(f"run6: {r['status']} stage={r['stage']}")
    for s in json.loads(r["steps_json"]):
        detail = f" — {s['detail']}" if s.get("detail") else ""
        print(f"  {s['name']}: {s['status']}{detail}")
    needs = json.loads(r["needs_you_json"] or "[]")
    for n in needs:
        print(f"  NEED[{n['kind']}]{n.get('language') or ''}: {n['message'][:140]}")
    for row in db.execute(text(
        "SELECT id, status, stage, progress, languages_json FROM documentary_jobs "
        "WHERE case_id=15 ORDER BY id")).mappings():
        print(f"  job{row['id']} {row['languages_json']}: {row['status']} "
              f"stage={row['stage']} progress={row['progress']:.0%}" if row['progress']
              else f"  job{row['id']} {row['languages_json']}: {row['status']} stage={row['stage']}")
    for row in db.execute(text(
        "SELECT id, language, status, stage, error FROM short_production_jobs "
        "WHERE case_id=15 ORDER BY id")).mappings():
        print(f"  short{row['id']} {row['language']}: {row['status']} {row['stage']} {row['error'] or ''}")
    stats = db.execute(text(
        "SELECT (SELECT count(*) FROM facts WHERE case_id=15) f,"
        " (SELECT count(*) FROM sources WHERE case_id=15) s,"
        " (SELECT count(*) FROM story_versions WHERE case_id=15) sv,"
        " (SELECT count(*) FROM videos WHERE case_id=15) v,"
        " (SELECT count(*) FROM rendered_shorts WHERE case_id=15) rs,"
        " (SELECT count(*) FROM research_jobs WHERE case_id=15) rj,"
        " (SELECT coalesce(sum(cost_eur),0)::numeric(10,4) FROM provider_calls WHERE case_id=15) eur"
    )).mappings().one()
    print(f"facts={stats['f']} sources={stats['s']} versions={stats['sv']} "
          f"videos={stats['v']} shorts={stats['rs']} rjobs={stats['rj']} eur={stats['eur']}")
