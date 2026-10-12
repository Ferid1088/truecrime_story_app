"""Saved-output observation independent of the registry and its hash builders.

All rows in the explicit pipeline-table contract and all files are assigned an
owner. Derived views additionally expose individually registered artifact types.
Unknown nonempty tables/files fail closed instead of disappearing from the diff.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from sqlalchemy import inspect, text

from app.services.soundness import SNAPSHOT_PIPELINE_TABLES, _row_snapshot, _snapshot_value

LANGUAGES = ("en", "de", "fa", "ar")
SHARED = ("research_result", "master_story", "beats", "audio_blocks", "visual_needs",
          "visual_asset", "visual_shots", "visual_pieces", "visual_plan", "chapter_plan",
          "host_plan", "naming")
LOCAL = ("spoken_version", "voice_takes", "audio_timing", "chapter_cards", "timeline_cards",
         "intro", "music_cues", "captions", "production_script", "final_render",
         "host_segments", "film_length")
TABLE_OWNER = {
    "cases": "naming", "facts": "research_result", "sources": "research_result",
    "source_chunks": "research_result", "research_results": "research_result",
    "research_source_links": "research_result", "editorial_blueprints": "beats",
    "audio_plans": "audio_blocks", "visual_assets": "visual_asset",
    "visual_plans": "visual_plan", "visual_audits": "visual_pieces",
    "chapter_plans": "chapter_plan", "host_plans": "host_plan",
    "host_segments": "host_segments", "host_scenes": "host_segments",
    "voice_performances": "voice_takes", "music_usages": "music_cues",
    "production_scripts": "production_script", "videos": "final_render",
    "thumbnails": "final_render", "media_usages": "production_script",
    "reveal_asset_links": "visual_pieces", "reveal_edges": "visual_pieces",
    "reveal_exposures": "production_script",
}


def observe(db, root: Path) -> dict:
    buckets = {f"{t}:shared": {} for t in SHARED}
    buckets.update({f"{t}:{lang}": {} for t in LOCAL for lang in LANGUAGES})
    replacements = tuple((str(root / name), f"<{name}>") for name in ("cases", "audio", "lib"))
    present = set(inspect(db.get_bind()).get_table_names())
    rows = {table: [dict(row) for row in db.execute(text(f'SELECT * FROM "{table}" ORDER BY id')).mappings()]
            for table in SNAPSHOT_PIPELINE_TABLES if table in present}
    versions = {r["id"]: r for r in rows["story_versions"]}
    productions = {r["id"]: r for r in rows["production_scripts"]}
    track_langs = defaultdict(set)
    for row in rows["music_usages"]:
        track_langs[row["track_id"]].add(row["language"])

    def add(owner, language, key, value):
        bucket = f"{owner}:{language or 'shared'}"
        buckets.setdefault(bucket, {})[key] = _snapshot_value(value, replacements)

    for table, values in rows.items():
        for row in values:
            language = row.get("language")
            if not language and row.get("story_version_id") in versions:
                language = versions[row["story_version_id"]]["language"]
            if not language and row.get("production_script_id") in productions:
                language = productions[row["production_script_id"]]["language"]
            key = f"db:{table}:{row['id']}"
            if table == "story_versions":
                owner = "master_story" if row["kind"] == "master" else "spoken_version"
            elif table == "music_tracks":
                # An unconsumed library row is still observed, never dropped.
                for lang in track_langs[row["id"]] or {None}:
                    add("music_cues", lang, key, _row_snapshot(row, replacements))
                continue
            else:
                owner = TABLE_OWNER[table]
            add(owner, None if owner in SHARED else language, key, _row_snapshot(row, replacements))
            if table == "visual_plans":
                plan = json.loads(row["plan_json"] or "{}")
                add("visual_needs", None, key, row["requirements_json"])
                add("visual_shots", None, key, plan.get("shots", plan.get("beats")))
                add("visual_pieces", None, key, plan.get("pieces"))
            if table == "chapter_plans":
                plan = json.loads(row["plan_json"] or "{}")
                for lang in LANGUAGES:
                    add("chapter_cards", lang, key, {
                        "title": plan.get("film_title", {}).get(lang),
                        "chapters": [{**c, "title": c.get("title", {}).get(lang)}
                                     for c in plan.get("chapters", [])]})
                    add("timeline_cards", lang, key, [
                        {**e, "label": e.get("label", {}).get(lang)} for e in plan.get("events", [])])
            if table == "production_scripts":
                script = json.loads(row["script_json"])
                add("captions", language, key, script.get("subtitles"))
                # script.intro is placement on the film timeline, already
                # owned by production_script; it is NOT the reusable channel
                # intro media artifact. Absent intro media is not proof of it.

    for row in db.execute(text("SELECT result_json FROM documentary_jobs")).mappings():
        for language, minutes in json.loads(row["result_json"] or "{}").get("film_minutes", {}).items():
            add("film_length", language, "film_minutes", minutes)

    owned_files = 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        raw = path.read_bytes()
        value = {"bytes_sha256": hashlib.sha256(raw).hexdigest()}
        if path.suffix == ".json":
            value = json.loads(raw)
        language = next((p for p in rel.parts if p in LANGUAGES), None)
        if rel.parts[0] == "audio":
            owner = ("voice_takes" if "blocks" in rel.parts else
                     "music_cues" if path.name.startswith("documentary") else "audio_timing")
        elif "renders" in rel.parts:
            owner = "final_render"
        elif "production" in rel.parts:
            owner = "production_script"
        elif "visuals" in rel.parts:
            owner = "visual_asset"
        elif rel.parts[0] == "lib":
            langs = set()
            for track in rows["music_tracks"]:
                if Path(track["file_path"]).stem == path.stem:
                    langs.update(track_langs[track["id"]])
            for lang in langs or {None}:
                add("music_cues", lang, f"file:{rel}", value)
            owned_files += 1
            continue
        else:
            raise AssertionError(f"Unowned output file: {rel}")
        add(owner, None if owner in SHARED else language, f"file:{rel}", value)
        owned_files += 1
    encoded = {key: json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
               for key, value in sorted(buckets.items())}
    return {"hashes": {key: hashlib.sha256(value.encode()).hexdigest() for key, value in encoded.items()},
            "values": buckets, "rows": sum(map(len, rows.values())), "files": owned_files}
