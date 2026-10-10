"""Persist clearly labeled mock metrics for the case-6 performance UI."""

from __future__ import annotations

import json

from app.db.base import SessionLocal
from app.db.models import ShortFormMetric


ROWS = [
    ("weather-cold-open", "youtube_short", 1200, 0.42),
    ("weather-cold-open", "instagram_reel", 840, 0.38),
    ("weather-cold-open", "tiktok_video", 610, 0.35),
    ("dispatch-audio-question", "youtube_short", 980, 0.51),
    ("dispatch-audio-question", "instagram_reel", 730, 0.47),
    ("dispatch-audio-question", "tiktok_video", 1550, 0.56),
    ("document-drop", "youtube_short", 640, 0.33),
    ("document-drop", "instagram_reel", 910, 0.44),
    ("document-drop", "tiktok_video", 1120, 0.49),
    ("verdict-countdown", "youtube_short", 1800, 0.62),
    ("verdict-countdown", "instagram_reel", 1310, 0.58),
    ("verdict-countdown", "tiktok_video", 2100, 0.65),
    ("appeal-turn", "youtube_short", 520, 0.29),
    ("appeal-turn", "instagram_reel", 460, 0.31),
    ("appeal-turn", "tiktok_video", 780, 0.37),
]


def main() -> None:
    with SessionLocal() as db:
        existing = db.query(ShortFormMetric).filter_by(model="phase13_mock").all()
        for row in existing:
            db.delete(row)
        db.flush()
        for concept_type, platform, views, completion in ROWS:
            db.add(ShortFormMetric(
                variant_id=None,
                platform=platform,
                metric_date="mock-2026-10-10",
                views=views,
                unique_viewers=None,
                average_watch_time=None,
                completion_rate=completion,
                likes=0,
                comments=0,
                shares=0,
                saves=0,
                estimated_conversion_rate=None,
                attribution_status="unavailable",
                raw_json=json.dumps({"mock": True, "source": "phase13"}),
                detail_json=json.dumps({
                    "case_id": 6,
                    "concept_id": f"mock-{concept_type}",
                    "concept_type": concept_type,
                }),
                model="phase13_mock",
            ))
        db.commit()
        print(json.dumps({
            "inserted": len(ROWS),
            "model": "phase13_mock",
            "case_id": 6,
            "attribution_status": "unavailable",
            "estimated_conversion_rate_values": [],
        }, indent=2))


if __name__ == "__main__":
    main()
