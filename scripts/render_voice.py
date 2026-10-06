"""Render narration for one story version from the command line.

    python -m scripts.render_voice --case 2 --version 9 --max-seconds 180

Prints a short QA summary; audio + manifest go to
data/cases/<case>/audio/<lang>/v<version>/. Unchanged blocks come from
cache, so re-running only pays for blocks whose text or voice changed.
"""

import argparse
import asyncio
import json

from app.db.base import SessionLocal
from app.db.models import StoryVersion
from app.documentary.voice_blocks import plan_for_version
from app.documentary.voice_render import VoiceRenderer


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", type=int, required=True)
    ap.add_argument("--version", type=int, required=True, help="StoryVersion id")
    ap.add_argument("--max-seconds", type=float, default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--force", nargs="*", default=[], help="block ids to re-take")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        story = (
            db.query(StoryVersion)
            .filter(StoryVersion.id == args.version, StoryVersion.case_id == args.case)
            .first()
        )
        if not story:
            raise SystemExit(f"Story version {args.version} not found in case {args.case}")
        plan = plan_for_version(story)
    finally:
        db.close()

    m = asyncio.run(VoiceRenderer().render(
        plan, case_id=args.case, story_version_id=args.version,
        max_seconds=args.max_seconds, style=args.style,
        force_block_ids=args.force,
    ))
    print(json.dumps({
        k: m[k] for k in ("duration_seconds", "blocks_rendered", "characters_paid",
                          "loudness", "flags", "asr", "asr_error", "files")
    }, indent=2, ensure_ascii=False))
    for b in m["blocks"]:
        asr = b["asr"] or {}
        print(f"{b['block_id']}: {b['actual_seconds']:.1f}s "
              f"{b['words_per_minute']} wpm, {b['loudness_lufs']} LUFS, "
              f"ASR {asr.get('word_error_rate')} "
              f"{'OK' if asr.get('passed', True) else 'FAILED'} {b['flags']}")
        for d in asr.get("diffs") or []:
            print(f"    {d['type']}: script='{d['script']}' heard='{d['heard']}'")


if __name__ == "__main__":
    main()
