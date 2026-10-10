"""Build and persist a RevealGraph from a persisted EditorialBlueprint."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from app.db.base import SessionLocal
from app.db.models import RevealGraph
from app.longform.service import build_reveal_graph_payload, create_reveal_graph


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id", type=int)
    parser.add_argument("blueprint_id", type=int)
    parser.add_argument("--review-dir", type=Path)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        existing = (db.query(RevealGraph)
                    .filter_by(case_id=args.case_id, blueprint_id=args.blueprint_id, version=1)
                    .first())
        if existing:
            raise SystemExit(f"RevealGraph already exists: id={existing.id}")
        payload = build_reveal_graph_payload(db, args.case_id, args.blueprint_id)
        graph = create_reveal_graph(db, args.case_id, args.blueprint_id, payload)
        print(f"created graph_id={graph.id} nodes={len(payload['nodes'])} "
              f"edges={len(payload['edges'])} exposures={len(payload['exposures'])}")
        if args.review_dir:
            args.review_dir.mkdir(parents=True, exist_ok=True)
            with (args.review_dir / "nodes.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "node_key", "category", "label", "first_revealed_beat_id",
                    "first_revealed_order", "description",
                ])
                writer.writeheader()
                writer.writerows({key: node.get(key, "") for key in writer.fieldnames}
                                 for node in payload["nodes"])
            with (args.review_dir / "edges.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["prerequisite", "dependent"])
                writer.writeheader()
                writer.writerows(payload["edges"])
            with (args.review_dir / "exposures.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "beat_id", "node_key", "exposure_type", "source_field",
                ])
                writer.writeheader()
                writer.writerows(payload["exposures"])
    finally:
        db.close()


if __name__ == "__main__":
    main()
