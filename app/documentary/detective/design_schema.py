"""Validation for the Alternative Visual Design Director's structured output (Section G OUTPUT
envelope). Same discipline as app.documentary.detective.schema (Section C): a provider can return
schema-valid JSON that still violates a semantic invariant this module catches before anything is
applied to a renderer payload."""

from __future__ import annotations

_VALID_OUTCOMES = {"proposed", "pass", "revise", "needs_attention"}
_REQUIRED_ENVELOPE = ("schema_version", "run_id", "film_id", "scene_id", "visual_id",
                     "language", "text_version", "mode", "outcome")


class DesignOutputError(ValueError):
    """The Visual Design Director's structured output violates a Section G invariant."""


def validate_design_output(raw: dict, *, exact_texts: dict[str, str]) -> list[str]:
    """`exact_texts`: element_id -> the exact quotation/claim text that element is bound to (from
    the Detective Director's content bindings). A `label_edits` entry touching one of these IDs
    must keep that exact text — shortening is allowed only for elements NOT in this map."""
    violations: list[str] = []
    for key in _REQUIRED_ENVELOPE:
        if key not in raw:
            raise DesignOutputError(f"missing required envelope field {key!r}")
    if raw["outcome"] not in _VALID_OUTCOMES:
        raise DesignOutputError(f"outcome {raw['outcome']!r} not in {_VALID_OUTCOMES}")
    if raw["mode"] not in {"design", "review", "repair"}:
        raise DesignOutputError(f"unknown mode {raw['mode']!r}")

    if raw["outcome"] in ("pass", "revise") and not raw.get("reviewed_preview_ids"):
        violations.append("outcome requires at least one actually reviewed preview, "
                          "found none in reviewed_preview_ids")

    for edit in raw.get("label_edits") or []:
        element_id = edit.get("element_id")
        proposed = edit.get("proposed_text")
        exact = exact_texts.get(element_id)
        if exact is not None and proposed is not None:
            if _normalize(proposed) != _normalize(exact):
                violations.append(
                    f"label_edits[{element_id!r}] rewrites an exact-quotation/claim-bound element "
                    f"({proposed!r} != {exact!r}) — meaning must survive design"
                )

    return violations


def _normalize(text: str) -> str:
    return " ".join(str(text).split()).strip().rstrip(".")
