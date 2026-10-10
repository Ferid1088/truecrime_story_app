"""Compression-safety checks for persisted EpistemicContract claims."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Iterable

from app.agents.runner import run_agent
from app.providers.generation.base import GenerationError

log = logging.getLogger(__name__)


MODALITY_STRENGTH = {
    "DISPUTED": 0,
    "ABSENCE_OF_EVIDENCE": 1,
    "ALLEGED": 2,
    "BELIEVED_BY_INVESTIGATORS": 3,
    "ESTABLISHED": 4,
}


@dataclass(frozen=True)
class CompressionCheck:
    passed: bool
    source_modality: str
    rewritten_modality: str
    violation: str | None = None


@dataclass(frozen=True)
class SemanticClassification:
    direct_assertion: bool
    attribution_present: bool
    confidence: float
    rationale: str
    classifier_source: str


@dataclass(frozen=True)
class SemanticCompressionCheck:
    decision: str
    source_modality: str
    classification: SemanticClassification
    reason: str


class SemanticAttributionClassifier:
    """LLM-backed attribution classifier with explicit offline fallback.

    Production callers must leave ``allow_fallback`` false. Tests and
    offline evaluation may opt in, and every fallback result is labeled and
    logged rather than being indistinguishable from a model result.
    """

    role = "consistency_checker"

    def __init__(self, provider=None, *, allow_fallback: bool = False,
                 confidence_threshold: float = 0.75):
        self.provider = provider
        self.allow_fallback = allow_fallback
        self.confidence_threshold = confidence_threshold

    def _fallback(self, rewrite: str) -> SemanticClassification:
        inferred = infer_modality(rewrite)
        attributed = inferred in {"ALLEGED", "DISPUTED", "BELIEVED_BY_INVESTIGATORS"}
        log.warning("compression attribution using deterministic fallback")
        return SemanticClassification(
            direct_assertion=not attributed,
            attribution_present=attributed,
            confidence=0.5,
            rationale="deterministic offline fallback; human review required",
            classifier_source="deterministic_fallback",
        )

    async def classify(self, source_claim: str, rewritten_text: str) -> SemanticClassification:
        if self.allow_fallback:
            return self._fallback(rewritten_text)
        provider = self.provider
        if provider is None:
            from app.providers.generation import get_generation_provider
            provider = get_generation_provider()
        if not provider.is_configured():
            raise GenerationError("missing_key", "Semantic compression classifier requires the configured generation provider")
        user = f"SOURCE CLAIM:\n{source_claim}\n\nREWRITE:\n{rewritten_text}"
        try:
            data, result = await run_agent("story.compression_semantics", provider, user)
            direct = data.get("direct_assertion")
            attributed = data.get("attribution_present")
            confidence = float(data.get("confidence"))
            if not isinstance(direct, bool) or not isinstance(attributed, bool) or not 0 <= confidence <= 1:
                raise ValueError("invalid semantic classifier payload")
            return SemanticClassification(
                direct_assertion=direct,
                attribution_present=attributed,
                confidence=confidence,
                rationale=str(data.get("rationale") or ""),
                classifier_source=f"llm:{result.provider}/{result.model}",
            )
        except (GenerationError, ValueError, TypeError, KeyError):
            raise

    async def check(self, source_modality: str, source_claim: str,
                    rewritten_text: str) -> SemanticCompressionCheck:
        classification = await self.classify(source_claim, rewritten_text)
        if classification.classifier_source == "deterministic_fallback":
            return SemanticCompressionCheck(
                decision="HUMAN_REVIEW", source_modality=source_modality,
                classification=classification,
                reason="fallback classification cannot authorize a production gate",
            )
        if classification.confidence < self.confidence_threshold or (
            not classification.direct_assertion and not classification.attribution_present
        ):
            return SemanticCompressionCheck(
                decision="HUMAN_REVIEW", source_modality=source_modality,
                classification=classification,
                reason="semantic attribution classification is uncertain",
            )
        if classification.direct_assertion and MODALITY_STRENGTH.get(source_modality, -1) < MODALITY_STRENGTH["ESTABLISHED"]:
            return SemanticCompressionCheck(
                decision="REJECT", source_modality=source_modality,
                classification=classification,
                reason=f"rewrite directly asserts a {source_modality} proposition as established",
            )
        return SemanticCompressionCheck(
            decision="ACCEPT", source_modality=source_modality,
            classification=classification,
            reason="attribution is semantically preserved or source is established",
        )


def infer_modality(text: str) -> str:
    """Infer only the modality signal needed for a compression gate.

    This intentionally errs toward a reviewable violation when attribution
    language is removed. It is not a replacement for editorial adjudication.
    """
    lower = text.lower()
    if re.match(r"\s*(the record|the evidence|it)\s+(?:is\s+)?(?:clearly\s+)?establish(?:es|ed)\s+that\b", lower):
        return "ESTABLISHED"
    if any(term in lower for term in (
        "denied", "disputed", "dispute", "conflicting", "controversy",
        "controversial", "whether", "on the other hand",
    )):
        return "DISPUTED"
    if any(term in lower for term in (
        "investigators believed", "investigators concluded", "police believed",
    )):
        return "BELIEVED_BY_INVESTIGATORS"
    if any(term in lower for term in (
        "not their truth", "not the truth of", "records his words",
        "records her words", "words, not", "account", "version", "testimony",
        "recounted by", "reported by", "accusation",
        "the defense position", "the prosecution position",
    )):
        return "ALLEGED"
    if re.search(r"\b(alleged|allegedly|claimed|argued|maintained|asserted|"
                 r"contended|according to|testified|insisted|accused)\b", lower):
        return "ALLEGED"
    if re.search(r"\b(?:the\s+)?(?:defense|prosecution|state|Anthony|Metcalf|"
                 r"Cortez|Wirskye)['’]?s\s+(?:account|version|claim|position|"
                 r"argument|allegation|testimony)\b", text, flags=re.I):
        return "ALLEGED"
    if any(term in lower for term in (
        "no evidence", "without evidence", "absence of evidence",
        "not a", "never", "did not", "no prior",
    )):
        return "ABSENCE_OF_EVIDENCE"
    if re.search(r"[\u201c\"].+[\u201d\"]", text, flags=re.S):
        return "ALLEGED"
    return "ESTABLISHED"


def check_compression_safety(source_modality: str, rewritten_text: str) -> CompressionCheck:
    """Reject a rewrite whose inferred modality is stronger than its source."""
    source = str(source_modality)
    rewritten = infer_modality(rewritten_text)
    if source not in MODALITY_STRENGTH:
        raise ValueError(f"Unknown source modality: {source}")
    if MODALITY_STRENGTH[rewritten] > MODALITY_STRENGTH[source]:
        return CompressionCheck(
            passed=False,
            source_modality=source,
            rewritten_modality=rewritten,
            violation=f"rewrite strengthens {source} to {rewritten}",
        )
    return CompressionCheck(
        passed=True,
        source_modality=source,
        rewritten_modality=rewritten,
    )


def evaluate_compression_cases(cases: Iterable[dict]) -> dict:
    """Measure gate decisions against labeled real-claim rewrite cases."""
    counts = {"true_positive": 0, "true_negative": 0,
              "false_positive": 0, "false_negative": 0}
    details = []
    for case in cases:
        result = check_compression_safety(case["source_modality"], case["rewritten_text"])
        expected_violation = bool(case["expected_violation"])
        actual_violation = not result.passed
        if actual_violation and expected_violation:
            bucket = "true_positive"
        elif not actual_violation and not expected_violation:
            bucket = "true_negative"
        elif actual_violation:
            bucket = "false_positive"
        else:
            bucket = "false_negative"
        counts[bucket] += 1
        details.append({**case, "actual_violation": actual_violation,
                        "inferred_modality": result.rewritten_modality,
                        "bucket": bucket})
    total = len(details)
    positives = sum(1 for case in details if case["expected_violation"])
    negatives = total - positives
    return {
        "total": total,
        "expected_violations": positives,
        "expected_safe": negatives,
        **counts,
        "false_positive_rate": round(counts["false_positive"] / negatives * 100, 2) if negatives else 0,
        "false_negative_rate": round(counts["false_negative"] / positives * 100, 2) if positives else 0,
        "details": details,
    }
