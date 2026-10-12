"""Stable IDs for members embedded in documentary JSON documents."""
from __future__ import annotations

import copy
import uuid
from typing import Any

_PREFIXES = {
    "beat": "beat", "shot": "shot", "chapter_card": "chapter_card",
    "timeline_card": "timeline_card", "caption": "caption",
}


def new_member_id(member_type: str) -> str:
    prefix = _PREFIXES.get(member_type, member_type)
    return f"{prefix}_{uuid.uuid4().hex}"


def legacy_member_id(parent_id: str, member_type: str, original_position: int) -> str:
    prefix = _PREFIXES.get(member_type, member_type)
    stable = uuid.uuid5(uuid.NAMESPACE_URL, f"{parent_id}:{member_type}:{original_position}").hex
    return f"{prefix}_{stable}"


def assign_new_ids(parent_id: str, member_type: str, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign UUID IDs to new members while preserving already persisted IDs."""
    out = copy.deepcopy(members)
    for member in out:
        if isinstance(member, dict) and not member.get("id"):
            member["id"] = new_member_id(member_type)
    return out


def load_legacy_ids(parent_id: str, member_type: str, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Provide deterministic, flagged IDs without rewriting the source record."""
    out = copy.deepcopy(members)
    for position, member in enumerate(out):
        if isinstance(member, dict) and not member.get("id"):
            member["id"] = legacy_member_id(parent_id, member_type, position)
            member["legacy_id"] = True
    return out


def ensure_document_member_ids(parent_id: str, document: dict[str, Any]) -> dict[str, Any]:
    """Apply IDs to the known member arrays in a new document."""
    out = copy.deepcopy(document)
    fields = (("beats", "beat"), ("shots", "shot"), ("chapter_cards", "chapter_card"),
              ("timeline_cards", "timeline_card"), ("captions", "caption"))
    for field, kind in fields:
        if isinstance(out.get(field), list):
            out[field] = assign_new_ids(parent_id, kind, out[field])
    return out
