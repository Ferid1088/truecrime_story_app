"""What the checks of a delivered text decide: which of their items go into the one revision round,
which keep the text open, and what an approval record says. Pure functions; the model calls live in
stages.py and the order of the steps in orchestrator.py."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.core.ai_config import ai_config

OPEN_ISSUES = "open_issues"
# A revision round happens at most once per language version (a hard rule, not a setting).
REVISION_ROUNDS = 1


def items_of(severities: Sequence[str], items: list[dict]) -> list[dict]:
    return [i for i in items if i.get("severity") in severities]


def revision_items(review_items: list[dict], check_items: list[dict]) -> list[dict]:
    """The items the revision round works on: the review's and the check's items of the configured
    severities, high first."""
    wanted = ai_config.language_pipeline.review.revision_severities
    rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(items_of(wanted, review_items) + items_of(wanted, check_items),
                  key=lambda i: rank.get(i.get("severity") or "", 3))


def blocking_items(review_items: list[dict], check_items: list[dict]) -> list[dict]:
    """The items that keep a text open: unresolved ones of the configured blocking severities."""
    wanted = ai_config.language_pipeline.review.blocking_severities
    return items_of(wanted, review_items) + items_of(wanted, check_items)


def critique_from(review_items: list[dict], check_items: list[dict]) -> dict:
    """The critique the final writer receives for the revision round: one list, in the shape of the
    inspector's critique. A fidelity or audit item becomes an instruction to return to the English
    text (or to the evidence); it never invites a new claim."""
    items = []
    for number, item in enumerate(revision_items(review_items, check_items), 1):
        items.append({
            "id": number, "severity": item["severity"], "kind": item.get("kind", ""),
            "act": item.get("act", ""), "quote": item.get("quote", ""),
            "problem": item.get("problem", ""),
            "instruction": item.get("instruction") or item.get("correction") or item.get("problem", "")})
    return {"scores": {}, "summary": "Revision round: the review of the delivered text and the check against "
            "the approved English text.", "items": items}


def open_issues(review_items: list[dict], check_items: list[dict]) -> list[dict]:
    """Every blocking item that is still there after the last check, with its source."""
    return ([{**i, "source": "review"} for i in items_of(ai_config.language_pipeline.review.blocking_severities, review_items)]
            + [{**i, "source": "check"} for i in items_of(ai_config.language_pipeline.review.blocking_severities, check_items)])


def counts(items: Sequence[Any]) -> dict:
    out: dict[str, int] = {}
    for item in items:
        out[item.get("severity", "?")] = out.get(item.get("severity", "?"), 0) + 1
    return out
