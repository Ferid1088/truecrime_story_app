"""Validation for the Detective Director's structured output (Section C OUTPUT envelope).

Deliberately independent of any one provider's JSON-schema enforcement: the model can return
schema-valid JSON that still violates a *semantic* invariant (a premature release, a duplicate
first-reveal, an unknown tool_id). This module catches those before anything is persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class DirectorOutputError(ValueError):
    """The Detective Director's structured output violates a Section C invariant."""


@dataclass
class NarrationIndex:
    """story_order per narration unit key and per release-anchor key, built from the same
    `narration` list the agent was given, so release-order checks use the actual input, not the
    agent's own (untrusted) claims about position."""

    order_by_unit: dict[str, int] = field(default_factory=dict)
    order_by_anchor: dict[str, int] = field(default_factory=dict)
    text_by_unit: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls, narration: list[dict], anchors: list[dict]) -> "NarrationIndex":
        order_by_unit = {u["unit_key"]: u["story_order"] for u in narration}
        order_by_anchor = {}
        for a in anchors:
            unit_key = a.get("unit_key")
            if unit_key in order_by_unit:
                order_by_anchor[a["anchor_key"]] = order_by_unit[unit_key]
        text_by_unit = {u["unit_key"]: u["text"] for u in narration if isinstance(u.get("text"), str)}
        return cls(order_by_unit=order_by_unit, order_by_anchor=order_by_anchor, text_by_unit=text_by_unit)

    def order_of(self, anchor_key: str | None) -> int | None:
        if anchor_key is None:
            return None
        return self.order_by_anchor.get(anchor_key)


CLAIM_BASIS_VALUES = {"established_in_text", "attributed", "disputed", "inference", "explicit_unknown"}
QUESTION_KIND_VALUES = {"not_yet_explained", "explicit_unresolved", "interpretive_question"}
KNOWLEDGE_SUBJECT_TYPES = {"entity", "claim", "question", "relation"}


def validate_director_output(payload: dict, narration_index: NarrationIndex,
                              known_tool_ids: set[str]) -> list[str]:
    """Return a list of violation messages (empty = clean). Never raises on a content problem —
    callers decide whether any violation blocks persistence; a missing/malformed required field
    does raise `DirectorOutputError`, since there is nothing safe to check further."""
    for key in ("schema_version", "run_id", "film_id", "language", "text_version", "mode"):
        if key not in payload:
            raise DirectorOutputError(f"missing required envelope field {key!r}")

    violations: list[str] = []

    for c in payload.get("claims", []):
        missing = [f for f in ("id", "entity_ids", "attribute", "text", "basis") if f not in c]
        if missing:
            violations.append(f"claim missing required field(s) {missing}: {c.get('id', c)!r}")
            continue
        if c["basis"] not in CLAIM_BASIS_VALUES:
            violations.append(f"claim {c['id']!r}: basis {c['basis']!r} is not one of {sorted(CLAIM_BASIS_VALUES)}")
    for q in payload.get("questions", []):
        missing = [f for f in ("id", "entity_ids", "question_text", "kind") if f not in q]
        if missing:
            violations.append(f"question missing required field(s) {missing}: {q.get('id', q)!r}")
            continue
        if q["kind"] not in QUESTION_KIND_VALUES:
            violations.append(f"question {q['id']!r}: kind {q['kind']!r} is not one of {sorted(QUESTION_KIND_VALUES)}")
    for r in payload.get("relations", []):
        missing = [f for f in ("id", "endpoint_ids", "relation_type", "label") if f not in r]
        if missing:
            violations.append(f"relation missing required field(s) {missing}: {r.get('id', r)!r}")
    for ev in payload.get("knowledge_events", []):
        missing = [f for f in ("subject_type", "subject_id", "operation") if f not in ev]
        if missing:
            violations.append(f"knowledge_event missing required field(s) {missing}: {ev.get('id', ev)!r}")
            continue
        if ev["subject_type"] not in KNOWLEDGE_SUBJECT_TYPES:
            violations.append(
                f"knowledge_event {ev.get('id')!r}: subject_type {ev['subject_type']!r} is not one "
                f"of {sorted(KNOWLEDGE_SUBJECT_TYPES)}"
            )
    for req in payload.get("requests", []):
        if req.get("type") == "text_revision":
            violations.extend(_text_revision_violations(req, narration_index))
    if violations:
        return violations

    claim_anchor = {c["id"]: c.get("release_anchor") for c in payload.get("claims", [])}
    question_anchor = {q["id"]: q.get("opening_anchor") for q in payload.get("questions", [])}
    relation_anchor = {r["id"]: r.get("release_anchor") for r in payload.get("relations", [])}
    subject_anchor = {**claim_anchor, **question_anchor, **relation_anchor}

    first_bound_by: dict[str, str] = {}
    for scene in payload.get("scenes", []):
        scene_order = _scene_order(scene, narration_index)
        for action in scene.get("actions", []):
            returned = [b for b in (_binding_id(r) for r in action.get("content_bindings", []))
                        if b in first_bound_by]
            if returned and not (action.get("reuse_reason") or "").strip():
                violations.append(
                    f"scene {scene.get('id')} action {action.get('id')}: shows {sorted(set(returned))} again "
                    f"(first shown by action {first_bound_by[returned[0]]}) without a reuse_reason; a purposeful "
                    "return says what the new appearance adds")
            for b in (_binding_id(r) for r in action.get("content_bindings", [])):
                if b is not None:
                    first_bound_by.setdefault(b, action.get("id"))
            tool_id = action.get("tool_id")
            if tool_id not in known_tool_ids:
                violations.append(
                    f"scene {scene.get('id')} action {action.get('id')}: "
                    f"unknown tool_id {tool_id!r} (not in tool_catalog)"
                )
            for raw_binding in action.get("content_bindings", []):
                subject_id = _binding_id(raw_binding)
                if subject_id is None:
                    violations.append(
                        f"scene {scene.get('id')} action {action.get('id')}: content binding "
                        f"{raw_binding!r} is not a recognizable stable ID string"
                    )
                    continue
                anchor_key = subject_anchor.get(subject_id)
                subject_order = narration_index.order_of(anchor_key)
                if scene_order is not None and subject_order is not None and subject_order > scene_order:
                    violations.append(
                        f"scene {scene.get('id')} action {action.get('id')}: content binding "
                        f"{subject_id!r} is anchored at story_order {subject_order}, after this "
                        f"scene's own position {scene_order} — premature release"
                    )
    return violations


TEXT_REVISION_FIELDS = ("id", "unit_key", "passage", "problem", "proposed_repair")


def _text_revision_violations(req: dict, narration_index: NarrationIndex) -> list[str]:
    """A text-revision request must name the exact passage it is about: the narration unit, and words
    that really occur in it - the agent points at the text, it never rewrites it (Section F, F3)."""
    missing = [f for f in TEXT_REVISION_FIELDS if not req.get(f)]
    if missing:
        return [f"text_revision request {req.get('id')!r} missing field(s) {missing}"]
    unit_key = req["unit_key"]
    if unit_key not in narration_index.order_by_unit:
        return [f"text_revision request {req['id']!r}: unknown narration unit {unit_key!r}"]
    text = narration_index.text_by_unit.get(unit_key)
    if text is not None and req["passage"] not in text:
        return [f"text_revision request {req['id']!r}: the passage is not the exact words of {unit_key!r}"]
    return []


def _binding_id(raw_binding) -> str | None:
    """A content binding should be a bare stable-ID string; tolerate a model wrapping it as
    `{"id": "..."}` or similar, but refuse anything with no single clear ID to extract."""
    if isinstance(raw_binding, str):
        return raw_binding
    if isinstance(raw_binding, dict):
        for key in ("id", "claim_id", "question_id", "relation_id", "entity_id"):
            if isinstance(raw_binding.get(key), str):
                return raw_binding[key]
    return None


def _scene_order(scene: dict, narration_index: NarrationIndex) -> int | None:
    orders = [
        narration_index.order_by_unit[u] for u in scene.get("covered_span_ids", [])
        if u in narration_index.order_by_unit
    ]
    return min(orders) if orders else None
