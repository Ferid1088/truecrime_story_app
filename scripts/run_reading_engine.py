"""Runs the Persian Reading Engine on the reading text of a film version and writes what it would change (nothing in the film, its cache, its
approvals or its audio is touched): python scripts/run_reading_engine.py CASE VERSION LANG OUT.json [--no-exceptions]
(--no-exceptions: the engine is not told the readings a person has confirmed, to show what it finds by itself)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.ai_config import ai_config  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.db.models import StoryVersion  # noqa: E402
from app.documentary import voice_prepare as VP  # noqa: E402
from app.documentary.performance import performance_for_version  # noqa: E402
from app.documentary.reading.audit import Auditor  # noqa: E402
from app.documentary.reading.block import block_inputs  # noqa: E402
from app.documentary.reading.engine import ReadingEngine  # noqa: E402
from app.documentary.reading.evidence import Lexicon  # noqa: E402
from app.documentary.reading.morphology import Morphology  # noqa: E402
from app.documentary.reading.selector import Selector  # noqa: E402


async def main() -> None:
    case_id, version_id, language, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
    folder = Path(ai_config.voice.work_dir) / str(case_id) / ai_config.reading.folder
    cache = json.loads((folder / f"{language}_v{version_id}.json").read_text(encoding="utf-8"))
    with SessionLocal() as db:
        version = db.get(StoryVersion, version_id)
        items = block_inputs(performance_for_version(db, version)["blocks"], VP.english_sources(db, version), ai_config.semantic_review.neighbour_chars)
        readings = {i.block_id: [r["latin"] for r in cache["blocks"][i.block_id]["readings"]] for i in items}
        engine = ReadingEngine(Auditor(Morphology(Lexicon.load(), exceptions={} if "--no-exceptions" in sys.argv else None)), Selector(db, case_id, folder / f"selection_{language}_v{version_id}.json"))
        result = await engine.run(items, readings)
    out.write_text(json.dumps({"summary": result.summary(), "outcomes": [o.__dict__ for o in result.outcomes]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result.summary(), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
