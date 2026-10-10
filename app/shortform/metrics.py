"""Persisted short-form metrics and concept-type comparisons."""

from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy.orm import Session

from app.db.models import ShortFormMetric


def _detail(row: ShortFormMetric) -> dict:
    try:
        value = json.loads(row.detail_json or "{}")
    except json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


def concept_type_comparison(db: Session, case_id: int) -> dict:
    groups: dict[str, list[ShortFormMetric]] = defaultdict(list)
    for row in db.query(ShortFormMetric).order_by(ShortFormMetric.id).all():
        detail = _detail(row)
        if detail.get("case_id") == case_id:
            groups[str(detail.get("concept_type") or "unknown")].append(row)

    comparisons = []
    for concept_type, rows in sorted(groups.items()):
        total_views = sum(row.views for row in rows)
        completion = [row.completion_rate for row in rows if row.completion_rate is not None]
        comparisons.append({
            "concept_type": concept_type,
            "metric_rows": len(rows),
            "total_views": total_views,
            "platforms": sorted({row.platform for row in rows}),
            "average_completion_rate": round(sum(completion) / len(completion), 4) if completion else None,
            "attribution_status": "unavailable",
        })
    return {
        "case_id": case_id,
        "comparison_basis": "concept_type",
        "metric_rows": sum(len(rows) for rows in groups.values()),
        "comparisons": comparisons,
        "attribution_status": "unavailable",
    }
