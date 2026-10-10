"""Report long-form RevealGraph/EpistemicContract rollout readiness per case."""

from __future__ import annotations

import argparse
import json

from app.db.base import SessionLocal
from app.longform.rollout import assess_all_cases
from app.providers.generation import get_generation_provider
from app.core.ai_config import ai_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    provider = get_generation_provider()
    with SessionLocal() as db:
        rows = [row.to_dict() for row in assess_all_cases(db)]

    operational = {
        "semantic_classifier_provider": provider.name,
        "semantic_classifier_configured": provider.is_configured(),
        "semantic_classifier_role": "consistency_checker",
        "semantic_classifier_temperature": ai_config.generation_for("consistency_checker").temperature,
        "semantic_classifier_expected_latency": "approximately 35 seconds per call",
        "ci_recommendation": "use explicit deterministic fallback for offline regression; reserve live API calls for integration verification",
    }
    payload = {"cases": rows, "operational_dependency": operational}
    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    print("case_id | status | blueprints | stories | visual_assets | media_segments | validated_graphs | contracts")
    for row in rows:
        print(
            f"{row['case_id']} | {row['status']} | {row['blueprint_count']} | "
            f"{row['story_version_count']} | {row['visual_asset_count']} | "
            f"{row['original_media_segment_count']} | {row['validated_graph_count']} | "
            f"{row['contract_set_count']}"
        )
        for reason in row["reasons"]:
            print(f"  - {reason}")
    print("\nOperational dependency:")
    for key, value in operational.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
