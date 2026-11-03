"""Audits the reading text of a film version against the lexical evidence (no model call, no voice): python scripts/audit_readings.py CASE VERSION [LANG]."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.ai_config import ai_config  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.db.models import StoryVersion  # noqa: E402
from app.documentary import voice_prepare as VP  # noqa: E402
from app.documentary.performance import performance_for_version  # noqa: E402
from app.documentary.reading.audit import Auditor, summary  # noqa: E402
from app.documentary.reading.block import block_inputs  # noqa: E402
from app.documentary.reading.evidence import Lexicon  # noqa: E402
from app.documentary.reading.morphology import Morphology  # noqa: E402


def main() -> None:
    case_id, version_id = int(sys.argv[1]), int(sys.argv[2])
    language = sys.argv[3] if len(sys.argv) > 3 else "fa"
    cache = json.loads((Path(ai_config.voice.work_dir) / str(case_id) / ai_config.reading.folder / f"{language}_v{version_id}.json").read_text(encoding="utf-8"))
    with SessionLocal() as db:
        version = db.get(StoryVersion, version_id)
        items = block_inputs(performance_for_version(db, version)["blocks"], VP.english_sources(db, version), ai_config.semantic_review.neighbour_chars)
    auditor = Auditor(Morphology(Lexicon.load()))
    findings = []
    for item in items:
        readings = [r["latin"] for r in cache["blocks"][item.block_id]["readings"]]
        if len(readings) != len(item.words):
            raise SystemExit(f"{item.block_id}: {len(readings)} readings for {len(item.words)} words")
        findings += auditor.block(item, readings)
    out = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("reading_audit.json")
    out.write_text(json.dumps({"summary": summary(findings), "findings": [f.__dict__ for f in findings]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary(findings), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
