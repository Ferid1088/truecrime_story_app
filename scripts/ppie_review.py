"""Reviews a Persian narration (ppie_review): the alignment with the approved English story, the local acoustic review of every block's original
take, and the repair proposals. Free unless --agents is given (the verifier, reading and corrector agents are billable, up to --max-eur). A folder
of another narration of the same approved version (a preview, an older narration) can be reviewed with --folder: its takes are compared with the
approved text and every difference is reported. --expect gives readings a person decided ({"block": {"word": "reading"}}), used where no agent
reading exists. Nothing is voiced here."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.db.models import StoryVersion  # noqa: E402
from app.documentary.ppie_review import expectation as EX  # noqa: E402
from app.documentary.ppie_review.run import Authorization, ReviewRun  # noqa: E402
from app.documentary.reading.words import key_of, tokenize  # noqa: E402
from app.documentary.voice_render import VoiceRenderer  # noqa: E402
from app.utils import utc_now  # noqa: E402


def owner_expectations(path: str, plan_blocks: list[dict]) -> dict[str, dict[int, EX.Expectation]]:
    given = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[str, dict[int, EX.Expectation]] = {}
    for b in plan_blocks:
        for w in tokenize(b["text"]):
            reading = (given.get(b["block_id"]) or {}).get(w.surface) or (given.get(b["block_id"]) or {}).get(key_of(w.surface))
            if reading:
                out.setdefault(b["block_id"], {})[w.n] = EX.Expectation(w.n, w.surface, reading, reason="decided by a person")
    return out


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--folder")
    p.add_argument("--agents", action="store_true")
    p.add_argument("--max-eur", type=float, default=0.0)
    p.add_argument("--by", default="")
    p.add_argument("--run-id", help="continue this run of the narration: its paid readings are used, not asked again")
    p.add_argument("--expect")
    p.add_argument("--blocks", nargs="*", help="review these blocks only")
    args = p.parse_args()
    with SessionLocal() as db:
        version = db.get(StoryVersion, args.version)
        folder = Path(args.folder) if args.folder else VoiceRenderer.out_dir_for(version.case_id, "fa", version.id)
        run = ReviewRun(db, version, folder, args.run_id)
        run.authorize(Authorization(agents=args.agents, tts=False, max_eur=args.max_eur, by=args.by, at=utc_now().isoformat()))
        given = None
        if args.expect:
            from app.documentary.performance import performance_for_version

            given = owner_expectations(args.expect, performance_for_version(db, version)["blocks"])
        summary = await run.review(given=given, only=set(args.blocks) if args.blocks else None)
        print(json.dumps({k: v for k, v in summary.items() if k != "voice_text_differs_from_approved"}, ensure_ascii=False, indent=1, default=str))
        print("blocks whose voice text differs from the approved text:", len(summary["voice_text_differs_from_approved"]))
        print("records:", run.folder)


if __name__ == "__main__":
    asyncio.run(main())
