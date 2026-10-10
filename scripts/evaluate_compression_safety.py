"""Evaluate compression safety on real case 6 EpistemicContract claims."""

from __future__ import annotations

import argparse
import json
import re

from app.db.base import SessionLocal
from app.db.models import EpistemicContractSet
from app.longform.compression import evaluate_compression_cases


def _cases(claims):
    cases = []
    for claim in claims:
        if claim.assertion_role == "QUOTED_PROPOSITION":
            safe_text = f"{claim.speaker or 'The speaker'} asserted that {claim.claim_text}"
            strong_text = re.sub(r"^I['\u2019]m not alleged,?\s*(sir,?\s*)?",
                                 "", claim.claim_text, flags=re.I).strip("\u201c\u201d\"")
        elif claim.modality in {"ALLEGED", "DISPUTED"}:
            if claim.modality == "DISPUTED":
                safe_text = f"The disputed account remains contested: {claim.claim_text}"
            else:
                safe_text = f"According to the reported account, {claim.claim_text}"
            strong_text = f"It is established that {claim.claim_text}"
        elif claim.modality == "ABSENCE_OF_EVIDENCE":
            safe_text = f"The record notes an absence of evidence: {claim.claim_text}"
            strong_text = claim.claim_text
        else:
            safe_text = f"The record establishes that {claim.claim_text}"
            strong_text = claim.claim_text
        cases.extend([
            {"case_id": claim.claim_key, "kind": "safe_paraphrase",
             "source_modality": claim.modality, "rewritten_text": safe_text,
             "expected_violation": False},
            {"case_id": claim.claim_key, "kind": "adversarial_strengthening",
             "source_modality": claim.modality, "rewritten_text": strong_text,
             "expected_violation": claim.modality in {"ALLEGED", "DISPUTED"}},
        ])
    return cases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", type=int, default=6)
    parser.add_argument("--story-version-id", type=int, default=15)
    parser.add_argument("--version", type=int, default=1)
    args = parser.parse_args()
    with SessionLocal() as db:
        contract = db.query(EpistemicContractSet).filter_by(
            case_id=args.case_id, story_version_id=args.story_version_id,
            version=args.version,
        ).one()
        result = evaluate_compression_cases(_cases(contract.claims))
        print(json.dumps({key: value for key, value in result.items() if key != "details"}, indent=2))
        for detail in result["details"]:
            if detail["case_id"] in {"S15_C009_02", "S15_C080", "S15_C005_01"}:
                print(json.dumps(detail, ensure_ascii=False))


if __name__ == "__main__":
    main()
