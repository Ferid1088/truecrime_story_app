"""The deterministic gates a final text passes. A failure goes back to the final
writer (stage 4) as a short, concrete list; nothing earlier is run again."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case
from app.language_pipeline import figures
from app.language_pipeline.coverage import EvidenceCoverage, Telling
from app.language_pipeline.lengths import LengthRange
from app.language_pipeline.materials import SharedMaterial
from app.language_pipeline.originality import OriginalityGuard
from app.language_pipeline.stages import StageOutput
from app.lifecycle.followups import opens_with_earlier_video
from app.services.tracking import documentary_run_context
from app.utils import language_quality

GATE_STAGE = "language_gates"
# Every gate, in the order they run. A language's blocking_gates (config) picks from these.
ALL_GATES = ("markers", "length", "language_quality", "figures", "follow_up", "reveal_order", "originality")
LATIN_SCRIPT = {"en", "de"}
_URL = re.compile(r"https?://|www\.")
_MARKDOWN = re.compile(r"^\s*#{1,6}\s|^\s*[-*_]{3,}\s*$", re.M)


@dataclass
class GateResult:
    reports: dict = field(default_factory=dict)
    failures: list[dict] = field(default_factory=list)
    tellings: dict[str, Telling] = field(default_factory=dict)
    similarity: tuple[float | None, str] = (None, "not_evaluated")

    @property
    def passed(self) -> bool:
        return not self.failures

    def fail(self, gate: str, problem: str, passages: list | None = None) -> None:
        limit = ai_config.language_pipeline.gates.max_listed_passages
        self.failures.append({"gate": gate, "problem": problem, "passages": (passages or [])[:limit]})

    def as_dict(self) -> dict:
        return {"pass": self.passed, "failures": [f["gate"] for f in self.failures],
                "details": self.failures, "reports": self.reports}


class FinalGates:
    def __init__(self, embedder=None):
        self.coverage = EvidenceCoverage(embedder) if embedder is not None else None
        self.originality = OriginalityGuard(embedder)

    async def run(self, db: Session, case: Case, material: SharedMaterial, language: str,
                  output: StageOutput, span: LengthRange, gates: Iterable[str],
                  stage: str = GATE_STAGE) -> GateResult:
        """The gates named in `gates` (a language's blocking_gates), in their fixed order. `span` is
        the film range."""
        wanted = set(gates)
        unknown = wanted - set(ALL_GATES)
        if unknown:
            raise ValueError(f"unknown gates: {sorted(unknown)}")
        result = GateResult()
        text = output.text
        if "markers" in wanted:
            self._markers(result, material, output)
        if "length" in wanted:
            self._length(result, text, span)
        if "language_quality" in wanted:
            self._script_and_purity(result, text, language)
        if "figures" in wanted:
            self._figures(result, material, text)
        if "follow_up" in wanted:
            self._follow_up(result, material, text, language)
        with documentary_run_context(case_id=case.id, language=language,
                                     pipeline_stage=f"{stage}:{language}"):
            if "reveal_order" in wanted:
                await self._reveal_order(result, db, case, material, output)
        if "originality" in wanted:
            await self._originality(result, db, case, text)
        return result

    # -- deterministic gates ----------------------------------------------------
    @staticmethod
    def _markers(result: GateResult, material: SharedMaterial, output: StageOutput) -> None:
        found = [s["id"] for s in output.sections]
        ok = output.markers_ok and found == material.act_ids
        result.reports["markers"] = {"pass": ok, "expected": material.act_ids, "found": found}
        if not ok:
            result.fail("markers", "the text must keep every act marker [[ACT:id]] of the plan, "
                        f"once, in order: {material.act_ids}; found {found}")

    @staticmethod
    def _length(result: GateResult, text: str, span: LengthRange) -> None:
        words = len(text.split())
        ok = span.gate_words_min <= words <= span.gate_words_max
        result.reports["length"] = {"pass": ok, "words": words, "allowed": [span.gate_words_min, span.gate_words_max],
                                    "minutes": round(words / span.words_per_minute, 1)}
        if not ok:
            result.fail("length", f"the text has {words} words; it must have between {span.words_min} "
                        f"and {span.words_max} (tolerance {span.gate_words_min}-{span.gate_words_max})")

    @staticmethod
    def _script_and_purity(result: GateResult, text: str, language: str) -> None:
        quality = language_quality(text, language, ai_config.language_quality_for(language))
        pure = not _URL.search(text) and not _MARKDOWN.search(text)
        result.reports["language_quality"] = {"pass": quality["pass"] and pure, **quality, "pure": pure}
        if not quality["pass"]:
            result.fail("language_quality", "too much text outside the language's own script "
                        f"(script ratio {quality['script_ratio']}, foreign tokens {quality['foreign_token_ratio']})")
        if not pure:
            result.fail("output_purity", "the text contains a web address or markdown")

    @staticmethod
    def _figures(result: GateResult, material: SharedMaterial, text: str) -> None:
        allowed = figures.supported_figures(material.pack, material.plan, material.draft_pack)
        bad = figures.unsupported(text, allowed)
        limit = ai_config.language_pipeline.gates.max_unsupported_figures
        result.reports["figures"] = {"pass": len(bad) <= limit, "unsupported": bad, "allowed_unsupported": limit}
        if len(bad) > limit:
            result.fail("figures", "figures that no evidence item states (remove them or use the "
                        "evidence's own figure): " + ", ".join(b["figure"] for b in bad),
                        [f"{b['figure']}: ...{b['context']}..." for b in bad])

    @staticmethod
    def _follow_up(result: GateResult, material: SharedMaterial, text: str, language: str) -> None:
        """An update video opens by naming the earlier video and saying the case is solved
        (machine-checked for Latin-script texts)."""
        if not material.follow_up:
            return
        if language not in LATIN_SCRIPT:
            result.reports["follow_up"] = {"pass": True, "checked": False,
                                           "note": "not machine-checked in this script"}
            return
        ok = opens_with_earlier_video(text, material.follow_up)
        result.reports["follow_up"] = {"pass": ok, "checked": True}
        if not ok:
            result.fail("follow_up", "this update video must open by naming the earlier video "
                        f"\"{material.follow_up.get('original_title')}\" and saying the case is now solved")

    async def _originality(self, result: GateResult, db: Session, case: Case, text: str) -> None:
        if len(text.split()) < ai_config.language_pipeline.originality_min_words:
            return
        report = await self.originality.check(db, case.id, text)
        result.reports["originality"] = report
        result.similarity = (report["worst"]["ngram_share"], "evaluated")
        if not report["pass"]:
            copied = [f"{n['sentence']}  <-  {n['source_sentence']}" for n in report["near_verbatim"]]
            result.fail("originality",
                        "the text repeats a research transcript's own telling ("
                        + ", ".join(report["failures"]) + f"; longest shared run {report['worst']['longest_run']} words, "
                        f"{report['worst']['ngram_share']:.1%} of its word sequences). Retell these passages "
                        "in your own words and your own order", copied)

    async def _reveal_order(self, result: GateResult, db: Session, case: Case,
                            material: SharedMaterial, output: StageOutput) -> None:
        if self.coverage is None:
            result.reports["reveal_order"] = {"pass": True, "skipped": "no embedding client"}
            return
        result.tellings = await self.coverage.tellings(material, output.sections)
        early = self.coverage.told_before_their_act(material, result.tellings)
        limit = ai_config.language_pipeline.gates.coverage.max_reveal_violations
        result.reports["reveal_order"] = {"pass": len(early) <= limit, "told_too_early": early,
                                          "evidence_told": len(result.tellings)}
        if len(early) > limit:
            result.fail("reveal_order", "evidence the plan withholds from an act is told inside it",
                        [f"{e['act']}: {e['evidence_id']} ({e['score']}) {e['sentence']}" for e in early])
