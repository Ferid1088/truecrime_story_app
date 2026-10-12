"""Stable IDs for members embedded in documentary JSON documents."""
from __future__ import annotations

import copy
import uuid
from typing import Any

_PREFIXES = {
    "beat": "beat", "shot": "shot", "chapter_card": "chapter_card",
    "timeline_card": "timeline_card", "caption": "caption",
    "audio_block": "audio_block", "audio_cue": "audio_cue",
    "visual_need": "visual_need", "visual_piece": "visual_piece",
    "overlay": "overlay", "voice_block": "voice_block",
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
    """Apply IDs to known member arrays in a new document.

    Existing semantic IDs are preserved so legacy readers keep seeing the
    same keys.  New members without an ``id`` receive a UUID-based ID.
    """
    out = copy.deepcopy(document)
    fields = (("beats", "beat"), ("shots", "shot"), ("chapter_cards", "chapter_card"),
              ("timeline_cards", "timeline_card"), ("captions", "caption"))
    for field, kind in fields:
        if isinstance(out.get(field), list):
            out[field] = assign_new_ids(parent_id, kind, out[field])
    return out


def ensure_pipeline_member_ids(parent_id: str, document: dict[str, Any]) -> dict[str, Any]:
    """Add IDs to nested members used by the documentary JSON producers.

    The helper only inserts missing fields.  It never changes existing
    semantic IDs such as ``beat_id``, ``act_id`` or ``index``.
    """
    out = ensure_document_member_ids(parent_id, document)
    for field, kind in (("beats", "audio_block"), ("requirements", "visual_need"),
                        ("chapters", "chapter_card"), ("events", "timeline_card"),
                        ("subtitles", "caption"), ("voice", "voice_block"),
                        ("overlays", "overlay"), ("music", "audio_cue"),
                        ("silences", "audio_cue")):
        if isinstance(out.get(field), list):
            out[field] = assign_new_ids(parent_id, kind, out[field])
    for beat in out.get("beats") or []:
        if not isinstance(beat, dict):
            continue
        if isinstance(beat.get("requirements"), list):
            beat["requirements"] = assign_new_ids(parent_id, "visual_need",
                                                   beat["requirements"])
        if isinstance(beat.get("shots"), list):
            beat["shots"] = assign_new_ids(parent_id, "shot", beat["shots"])
            for shot in beat["shots"]:
                if (isinstance(shot, dict) and shot.get("asset_id")
                        and str(shot.get("command") or "").upper() == "SHOW_CLIP"
                        and not shot.get("piece_id")):
                    shot["piece_id"] = new_member_id("visual_piece")
        if isinstance(beat.get("after"), dict) and not beat["after"].get("id"):
            beat["after"]["id"] = new_member_id("audio_cue")
        for block in beat.get("blocks") or []:
            if isinstance(block, dict) and not block.get("id"):
                block["id"] = new_member_id("audio_block")
    for chapter in out.get("chapters") or []:
        if isinstance(chapter, dict) and not chapter.get("card_id"):
            chapter["card_id"] = new_member_id("chapter_card")
    for event in out.get("events") or []:
        if isinstance(event, dict) and not event.get("card_id"):
            event["card_id"] = new_member_id("timeline_card")
    return out
