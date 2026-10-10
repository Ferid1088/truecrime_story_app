"""Run the semantic compression challenge sets through the live classifier."""

from __future__ import annotations

import asyncio
import argparse
import json
import sys

from app.db.base import SessionLocal
from app.db.models import EpistemicContractSet
from app.longform.compression import SemanticAttributionClassifier


ROUNDS = [
    [
        ("R1V1", "S15_C009_02", "Anthony was the killer.", "violation"),
        ("R1V2", "S15_C070_02", "Metcalf initiated the physical confrontation.", "violation"),
        ("R1V3", "S15_C101_02", "The stabbing was a justified act of self-defense.", "violation"),
        ("R1V4", "S15_C164_02", "Prosecutors breached the off-record character-evidence agreement.", "violation"),
        ("R1V5", "S15_C167_02", "The agreement categorically excluded defendants who testified.", "violation"),
        ("R1S1", "S15_C009_02", "On body camera, Anthony took responsibility for the killing; the footage records his words, not their truth.", "safe"),
        ("R1S2", "S15_C070_02", "The defense version put Metcalf first in the physical encounter.", "safe"),
        ("R1S3", "S15_C101_02", "Anthony's account was self-defense against Metcalf.", "safe"),
        ("R1S4", "S15_C164_02", "The defense accused prosecutors of breaking the character-evidence deal.", "safe"),
        ("R1S5", "S15_C167_02", "The courtroom record contains competing accounts of whether the agreement covered testimony.", "safe"),
    ],
    [
        ("R2V1", "S15_C067_02", "Anthony's push of Metcalf is an established fact.", "violation"),
        ("R2V2", "S15_C074_02", "Anthony deliberately escalated the confrontation.", "violation"),
        ("R2V3", "S15_C164_02", "The prosecutors violated the agreement.", "violation"),
        ("R2V4", "S15_C078_02", "Anthony stabbed Metcalf in the chest.", "violation"),
        ("R2V5", "S15_C167_02", "The agreement plainly excluded testifying defendants.", "violation"),
        ("R2S1", "S15_C009_02", "The footage preserves Anthony's own version of events, without resolving its truth.", "safe"),
        ("R2S2", "S15_C070_02", "Metcalf's side of the encounter, as recounted by the defense, began with contact.", "safe"),
        ("R2S3", "S15_C101_02", "Anthony's self-defense account remains his version of what happened.", "safe"),
        ("R2S4", "S15_C164_02", "The defense's accusation was that prosecutors broke the deal.", "safe"),
        ("R2S5", "S15_C167_02", "The record preserves two incompatible accounts of whether the agreement covered testimony.", "safe"),
    ],
    [
        ("R3S1", "S15_C009_02", "Per Anthony's telling, he took responsibility for the killing.", "safe"),
        ("R3S2", "S15_C070_02", "As Anthony put it afterward, Metcalf made the first move.", "safe"),
        ("R3S3", "S15_C101_02", "The narrative Anthony gave cast the stabbing as self-defense.", "safe"),
        ("R3S4", "S15_C164_02", "What the defense's case was: prosecutors broke the character-evidence deal.", "safe"),
        ("R3S5", "S15_C101_02", "Anthony: it was self-defense against Metcalf.", "safe"),
        ("R3S6", "S15_C070_02", "The so-called first-contact story was Anthony's answer to the charge.", "safe"),
        ("R3S7", "S15_C167_02", "The story Anthony gave and the defense position pointed in opposite directions on the agreement.", "safe"),
        ("R3V1", "S15_C009_02", "Anthony's own words establish that he committed the killing.", "violation"),
        ("R3V2", "S15_C167_02", "Whatever the competing accounts said, the agreement plainly excluded testifying defendants.", "violation"),
        ("R3V3", "S15_C074_02", "The defense narrative ultimately confirms that Anthony deliberately escalated the confrontation.", "violation"),
    ],
    [
        ("R4S1", "S15_C009_02", "From Anthony's perspective, the fatal encounter was his version of what happened.", "safe"),
        ("R4S2", "S15_C070_02", "Anthony's recollection puts first contact on Metcalf.", "safe"),
        ("R4S3", "S15_C164_02", "In the defense's telling, prosecutors broke the character-evidence deal.", "safe"),
        ("R4S4", "S15_C101_02", "The account attributed to Anthony describes the stabbing as self-defense.", "safe"),
        ("R4S5", "S15_C167_02", "The record leaves the agreement's scope contested.", "safe"),
        ("R4V1", "S15_C009_02", "The body-camera footage proves Anthony committed the killing.", "violation"),
        ("R4V2", "S15_C070_02", "Metcalf was the aggressor.", "violation"),
        ("R4V3", "S15_C101_02", "Anthony acted lawfully in self-defense.", "violation"),
        ("R4V4", "S15_C164_02", "Prosecutors breached the character-evidence agreement.", "violation"),
        ("R4V5", "S15_C167_02", "The agreement did not apply to a defendant who testified.", "violation"),
    ],
]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-fallback", action="store_true")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=75.0)
    args = parser.parse_args()
    with SessionLocal() as db:
        contract = db.query(EpistemicContractSet).filter_by(
            case_id=6, story_version_id=15, version=1
        ).one()
        claims = {claim.claim_key: claim for claim in contract.claims}
    classifier = SemanticAttributionClassifier(allow_fallback=args.allow_fallback)
    fallback_classifier = SemanticAttributionClassifier(allow_fallback=True)
    flat = [case for round_cases in ROUNDS for case in round_cases]
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async def one(case):
        case_id, source_key, rewrite, expected = case
        claim = claims[source_key]
        async with semaphore:
            print(f"START {case_id} source={source_key}", file=sys.stderr, flush=True)
            try:
                result = await asyncio.wait_for(
                    classifier.check(claim.modality, claim.claim_text, rewrite),
                    timeout=args.timeout,
                )
            except Exception as exc:
                print(f"FALLBACK {case_id} reason={type(exc).__name__}",
                      file=sys.stderr, flush=True)
                result = await fallback_classifier.check(
                    claim.modality, claim.claim_text, rewrite
                )
        actual = "violation" if result.decision == "REJECT" else (
            "safe" if result.decision == "ACCEPT" else "human_review"
        )
        print(f"DONE {case_id} decision={result.decision} source={result.classification.classifier_source}",
              file=sys.stderr, flush=True)
        return {"case_id": case_id, "source_key": source_key,
                "expected": expected, "actual": actual,
                "decision": result.decision,
                "confidence": result.classification.confidence,
                "classifier_source": result.classification.classifier_source,
                "rewrite": rewrite}

    rows = await asyncio.gather(*(one(case) for case in flat))
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print(json.dumps({
        "total": len(rows),
        "classifier_sources": sorted({row["classifier_source"] for row in rows}),
        "accept": sum(row["actual"] == "safe" for row in rows),
        "reject": sum(row["actual"] == "violation" for row in rows),
        "human_review": sum(row["actual"] == "human_review" for row in rows),
        "expected_safe": sum(row["expected"] == "safe" for row in rows),
        "expected_violations": sum(row["expected"] == "violation" for row in rows),
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
