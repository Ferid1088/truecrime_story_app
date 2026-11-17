"""The offline Reels preflight: every clip of a case and language is probed (read-only) and judged against Instagram Reels and Facebook Page Reels.
Run: python -m app.meta_publishing.preflight [--json]. It makes no network call and writes nothing."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.meta_publishing.config import MetaPublishingConfig, meta_config
from app.meta_publishing.copies import prepared_copy_of
from app.meta_publishing.probe import MediaFacts, ProbeError, Prober, Runner, run_ffprobe
from app.meta_publishing.validator import PlatformReport, PlatformValidator, Verdict


@dataclass
class ClipReport:
    clip_key: str
    path: Path | None
    facts: MediaFacts | None
    error: str | None
    platforms: dict[str, PlatformReport]

    def verdict(self, platform: str) -> Verdict:
        return self.platforms[platform].verdict if platform in self.platforms else Verdict.FAIL

    def actions(self) -> list[str]:
        if self.error:
            return [self.error]
        return list(dict.fromkeys(a for r in self.platforms.values() for a in r.actions()))


class ReelsPreflight:
    def __init__(self, config: MetaPublishingConfig | None = None, runner: Runner = run_ffprobe):
        self.config = config or meta_config()
        self.prober = Prober(self.config.preflight.ffprobe_timeout_s, runner)

    def check_file(self, clip_key: str, path: Path | None) -> ClipReport:
        if path is None:
            return ClipReport(clip_key, None, None, "the clip has no video file recorded", {})
        try:
            facts = self.prober.probe(path)
        except ProbeError as exc:
            return ClipReport(clip_key, path, None, str(exc), {})
        reports = {name: PlatformValidator(name, spec, self.config.preflight.gop_tolerance_s).validate(facts) for name, spec in self.config.platforms.items()}
        return ClipReport(clip_key, path, facts, None, reports)

    def check_case(self, db: Session) -> list[ClipReport]:
        """The newest valid version of every clip of the configured case and language (a read of the database, nothing is written)."""
        from app.documentary import storage
        from app.shortform import clips as C

        p = self.config.preflight
        out = []
        for row in C.clips_of(db, p.case_id, p.language):
            video = (json.loads(row.media_json or "{}").get("video") or {}).get("path")
            original = storage.resolve(video) if video else None
            out.append(self.check_file(row.clip_key, (prepared_copy_of(original) or original) if original else None))      # a prepared copy, when one exists, is what Instagram and Facebook get
        return out


def _summary(reports: list[ClipReport]) -> str:
    lines = ["Clip | Duration | Instagram | Facebook | Warnings | Required Action"]
    for r in reports:
        dur = f"{r.facts.duration:.1f} s" if r.facts else "-"
        warnings = sum(len(p.by(Verdict.WARNING)) for p in r.platforms.values())
        lines.append(f"{r.clip_key} | {dur} | {r.verdict('instagram_reel').value} | {r.verdict('facebook_reel').value} | {warnings} | {'; '.join(r.actions()) or 'none'}")
    return "\n".join(lines)


def _detail(reports: list[ClipReport]) -> list[dict]:
    return [{"clip": r.clip_key, "path": str(r.path) if r.path else None, "error": r.error,
             "platforms": {n: {"verdict": p.verdict.value, "checks": [{"name": c.name, "evidence": c.level, "verdict": c.verdict.value, "actual": c.actual, "expected": c.expected, "action": c.action}
                                                                      for c in p.checks if c.verdict is not Verdict.PASS]} for n, p in r.platforms.items()}} for r in reports]


def main(argv: list[str]) -> int:
    from app.db.base import SessionLocal

    with SessionLocal() as db:
        reports = ReelsPreflight().check_case(db)
    if not reports:
        print("no clips found for the configured case and language", file=sys.stderr)
        return 1
    print(json.dumps(_detail(reports), ensure_ascii=False, indent=2) if "--json" in argv else _summary(reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
