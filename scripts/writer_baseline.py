"""Baseline of a stored writer's output (Part 5b2): deterministic metrics of
chosen beats of a stored story version, no new model call and no spend.

    python scripts/writer_baseline.py --case 5 --version 6 --beats act1,act2,act3 \
        [--label baseline-v6] [--out docs/benchmarks/writer.md]

Writes numbers and ids only (never the narration text) as one table section
appended to the output file."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.db.models import StoryVersion  # noqa: E402
from app.services import writer_metrics as WM  # noqa: E402


def collect(version: StoryVersion, beat_ids: list[str]) -> dict:
    structure = json.loads(version.narrative_structure or "{}")
    targets = {str(a.get("id")): int(a.get("target_words") or 0) for a in structure.get("acts") or []}
    sections = {s["id"]: s["text"] for s in structure.get("sections") or []}
    rows = []
    for bid in beat_ids:
        if bid not in sections:
            raise SystemExit(f"version {version.id} has no beat {bid!r}; has {sorted(sections)}")
        rows.append({"beat": bid, **WM.beat_metrics(sections[bid], version.language, targets.get(bid))})
    quality = WM.stored_quality(json.loads(version.critic_notes or "{}"))
    return {"version": version, "rows": rows, "quality": quality}


def render(label: str, data: dict) -> str:
    v = data["version"]
    q = data["quality"]
    lines = [
        f"### {label} — case {v.case_id}, story version {v.id} (v{v.version}, {v.language}, "
        f"model {v.generation_model}, prompt {v.writer_prompt_name or 'not recorded'} "
        f"{(v.writer_prompt_sha256 or '')[:8]}) — {date.today().isoformat()}",
        "",
        "| beat | words | target | length error | sentences | mean len | len variance | short share "
        "| repeated trigrams | stock phrases |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in data["rows"]:
        s = r["sentence_length"]
        lines.append(
            f"| {r['beat']} | {r['words']} | {r.get('target_words', '')} | {r.get('length_error', '')} "
            f"| {s['sentences']} | {s['mean']} | {s['variance']} | {s['short_share']} "
            f"| {r['repeated_trigram_rate']} | {len(r['stock_phrases'])} |")
    lines += ["", f"Validators' own record for the whole version: grounding score {q['grounding_score']}, "
              f"unsupported claims {q['unsupported_claims']}, engagement score {q['engagement_score']}, "
              f"failed gates {q['gate_failures'] or 'none'}; critic section scores "
              f"{[(s.get('id') or s.get('section_id'), s.get('score')) for s in q['section_scores']] or 'none'}.", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", type=int, required=True)
    ap.add_argument("--version", type=int, required=True, help="story_versions.id")
    ap.add_argument("--beats", required=True)
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--out", default="docs/benchmarks/writer.md")
    args = ap.parse_args()
    with SessionLocal() as db:
        version = db.get(StoryVersion, args.version)
        if version is None or version.case_id != args.case:
            raise SystemExit("story version not found for this case")
        text = render(args.label, collect(version, args.beats.split(",")))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = out.read_text(encoding="utf-8") if out.exists() else (
        "# Writer benchmark\n\nNumbers and ids only. Deterministic metrics: scripts/writer_baseline.py.\n\n")
    out.write_text(existing + text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
