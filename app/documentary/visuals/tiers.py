"""Relevance tiers of the media library (Master task §8.2): how directly
an asset belongs to THIS case. The Visual Director prefers lower tiers.

  1 exact_case   — real case evidence or footage (case-source photos of
                   the scene/event, documents, our quoted document cards)
  2 exact_entity — the exact person, object or vehicle involved
  3 exact_place  — the exact city, building, street or forest area
  4 contextual   — contextually accurate licensed imagery (same region,
                   same kind of place, the right period)
  5 generic      — atmosphere, last resort only

Two moments decide a tier, both deterministic and explainable:
  * provisional_tier — when an asset is FOUND: what the search claimed
    (provider, role, entity type, exact vs context query);
  * verified_tier    — after the vision check: what the image SHOWS.
Each function has a `*_why` twin that returns the rule that fired, so the
audit can say why an asset sits in its tier.
"""

from __future__ import annotations

TIERS: dict[int, str] = {
    1: "exact_case",
    2: "exact_entity",
    3: "exact_place",
    4: "contextual",
    5: "generic",
}

# Entity types (planner vocabulary) and vision subject types, grouped by
# what an exact match of them proves.
_ENTITY_KINDS = {"person", "object", "vehicle", "organization"}
_PLACE_KINDS = {"place", "building", "landscape", "map", "geography"}
_CASE_KINDS = {"event", "document"}

# Material from the case's own sources: an exact match is case evidence.
CASE_SOURCE_PROVIDERS = {"source_page", "upload"}


def tier_label(tier: int | None) -> str | None:
    """case_relevance label of a tier (None for an unknown tier)."""
    return TIERS.get(tier) if tier is not None else None


def entity_kind(kind: str | None) -> str | None:
    """'entity' | 'place' | 'case' | None for a planner entity type or a
    verifier subject type."""
    k = (kind or "").strip().lower()
    if k in _ENTITY_KINDS:
        return "entity"
    if k in _PLACE_KINDS:
        return "place"
    if k in _CASE_KINDS:
        return "case"
    return None


def provisional_why(provider: str | None, asset_role: str | None = None,
                    entity_type: str | None = None,
                    query_kind: str | None = None) -> tuple[int, str]:
    """(tier, rule) for an asset at the moment it is found."""
    provider = provider or ""
    kind = entity_kind(entity_type)
    if provider == "generated":
        # our own cards and maps: a document card quotes a case source, a
        # map shows the exact place
        if asset_role == "evidence" or entity_type == "document":
            return 1, "generated card quoting a case source"
        return 3, "generated map of the exact place"
    if asset_role == "illustration":
        return 5, "illustration (atmosphere only)"
    if query_kind == "context":
        return 4, "found by a context query (same kind of place/period)"
    if provider in CASE_SOURCE_PROVIDERS:
        if asset_role == "context":
            return (2, "case source, exact entity") if kind == "entity" else \
                (3, "case source, context picture")
        if kind == "entity":
            return 2, f"case source picture of the exact {entity_type}"
        return 1, "case source material (scene, event, document)"
    # rights-known libraries and web image search, found by an exact query
    if kind in ("entity", "case"):
        return 2, f"exact query for the {entity_type}"
    return 3, f"exact query for the place ({entity_type or 'unspecified'})"


def provisional_tier(provider: str | None, asset_role: str | None = None,
                     entity_type: str | None = None, query_kind: str | None = None) -> int:
    return provisional_why(provider, asset_role, entity_type, query_kind)[0]


def verified_why(asset, verification: dict | None,
                 provisional: int | None = None) -> tuple[int, str]:
    """(tier, rule) after the vision check. `asset` is a VisualAsset (or
    anything with provider/asset_role/entity_type/relevance_tier);
    `verification` the verifier's judgement (matches_claim, role,
    subject_type, period_ok); `provisional` the tier given when it was
    found (default: the asset's current tier) — so a re-check starts from
    what the search claimed, not from an earlier verdict.

    Verification can confirm or demote what the search claimed, and
    promote an exact find to case evidence — but matching a CONTEXT claim
    ("a pine forest in the region") never makes it the exact place."""
    v = verification if isinstance(verification, dict) else {}
    prov = provisional or getattr(asset, "relevance_tier", None) or provisional_tier(
        getattr(asset, "provider", None), getattr(asset, "asset_role", None),
        getattr(asset, "entity_type", None))
    role = v.get("role") if v.get("role") in ("evidence", "context", "illustration") \
        else getattr(asset, "asset_role", None)
    matches = v.get("matches_claim")
    if matches == "stand_in":
        if role == "context":
            return 4, "verifier: stand-in of the same kind from the case's region/period"
        return 5, "verifier: stand-in illustration (labelled on screen)"
    if role == "illustration":
        return 5, "verifier: illustration (atmosphere only)"
    if matches == "no":
        if v.get("period_ok") == "no":
            return 5, "verifier: not the claimed subject, wrong period"
        return 4, "verifier: not the claimed subject (context at best)"
    if prov >= 4:
        return prov, "found as context: a contextual match stays contextual"
    if matches != "yes":
        return prov, "verifier unsure: provisional tier kept (human review)"
    kind = entity_kind(v.get("subject_type")) or entity_kind(getattr(asset, "entity_type", None))
    if role == "evidence":
        if kind == "entity":
            return 2, "verified: the exact person/object of the case"
        return 1, "verified: genuine case material"
    if kind == "entity":
        return 2, "verified: the exact person/object (not case material)"
    return 3, "verified: the exact place/building/area"


def verified_tier(asset, verification: dict | None, provisional: int | None = None) -> int:
    return verified_why(asset, verification, provisional)[0]
