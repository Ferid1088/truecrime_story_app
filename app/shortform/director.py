from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import itertools
import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import EditorialBlueprint, ShortFormConcept, VisualAsset
from app.shortform.assets import UNSELECTABLE_VERIFICATION_STATUSES


CONCEPT_TYPES = [
    "MYSTERY_HOOK",
    "CONTRADICTION",
    "REAL_EVIDENCE",
    "DOCUMENT_MOMENT",
    "HUMAN_MOMENT",
    "LOCATION_OR_MAP",
    "ORIGINAL_AUDIO",
    "UNEXPECTED_DETAIL",
    "HOST_DIRECT_TO_CAMERA",
    "QUESTION_DRIVEN",
    "TIMELINE_MOMENT",
    "INVESTIGATIVE_TURN",
    "FULL_STORY_TEASER",
]


@dataclass(frozen=True)
class BeatMaterial:
    id: str
    text: str
    evidence_ids: list[str] = field(default_factory=list)
    asset_ids: list[str] = field(default_factory=list)
    allowed_reveals: list[str] = field(default_factory=list)
    forbidden_reveals: list[str] = field(default_factory=list)
    visual_priority: str = ""


class ShortFormDirectorAgent:
    """Deterministic director for Phase 2.

    It does not invent scenes. It extracts candidate hooks from blueprint
    beats and attaches only evidence/assets already present in the given
    blueprint or case visual library.
    """

    def __init__(self, candidate_count: int | None = None):
        self.candidate_count = candidate_count or ai_config.short_form.candidate_count()

    def generate_from_blueprint(
        self,
        blueprint: dict,
        *,
        assets: list[VisualAsset] | list[dict] | None = None,
    ) -> list[dict]:
        beats = extract_beats(blueprint)
        if not beats:
            raise ValueError("blueprint has no usable beats")
        asset_map = _asset_map(assets or [])
        out: list[dict] = []
        for idx in range(self.candidate_count):
            beat = beats[idx % len(beats)]
            concept_type = choose_concept_type(beat, idx)
            asset_ids = [
                asset_id for asset_id in beat.asset_ids
                if asset_id not in asset_map
                or asset_map[asset_id].get("verification_status")
                not in UNSELECTABLE_VERIFICATION_STATUSES
            ] or _best_asset_ids(asset_map, beat, limit=2)
            hook = make_hook(beat, concept_type)
            out.append({
                "id": stable_candidate_id(beat.id, concept_type, idx),
                "concept_type": concept_type,
                "hook": hook,
                "source_beat_ids": [beat.id],
                "evidence_ids": beat.evidence_ids,
                "asset_ids": asset_ids,
                "allowed_reveals": beat.allowed_reveals,
                "forbidden_reveals": beat.forbidden_reveals,
                "claims": [
                    {"source_claim_id": eid, "modality": "ESTABLISHED"}
                    for eid in beat.evidence_ids
                ],
                "gate_results": {},
                "soft_scores": {},
                "status": "generated",
            })
        return out

    def generate_for_blueprint_row(
        self,
        db: Session,
        row: EditorialBlueprint,
        *,
        persist: bool = False,
    ) -> list[dict]:
        blueprint = json.loads(row.blueprint_json or "{}")
        assets = db.query(VisualAsset).filter(VisualAsset.case_id == row.case_id).all()
        candidates = self.generate_from_blueprint(blueprint, assets=assets)
        if persist:
            for c in candidates:
                db.add(ShortFormConcept(
                    case_id=row.case_id,
                    episode_identity_id=row.id,
                    concept_type=c["concept_type"],
                    source_beat_ids_json=json.dumps(c["source_beat_ids"]),
                    evidence_ids_json=json.dumps(c["evidence_ids"]),
                    asset_ids_json=json.dumps(c["asset_ids"]),
                    allowed_reveals_json=json.dumps(c["allowed_reveals"]),
                    forbidden_reveals_json=json.dumps(c["forbidden_reveals"]),
                    claims_json=json.dumps(c["claims"]),
                    gate_results_json=json.dumps(c["gate_results"]),
                    soft_scores_json=json.dumps(c["soft_scores"]),
                    status=c["status"],
                ))
            db.commit()
        return candidates


def candidate_table(candidates: list[dict]) -> list[dict]:
    return [
        {
            "type": c["concept_type"],
            "hook": c["hook"],
            "beats": c["source_beat_ids"],
            "assets": c["asset_ids"],
            "forbidden_reveals": c["forbidden_reveals"],
        }
        for c in candidates
    ]


def extract_beats(blueprint: dict) -> list[BeatMaterial]:
    raw = (
        blueprint.get("beats")
        or blueprint.get("story_beats")
        or list(itertools.chain.from_iterable(
            act.get("beats", []) for act in blueprint.get("acts", [])
        ))
    )
    beats: list[BeatMaterial] = []
    for index, beat in enumerate(raw, start=1):
        if not isinstance(beat, dict):
            continue
        beat_id = str(beat.get("id") or beat.get("beat_id") or f"B{index:03d}")
        text = str(
            beat.get("short_text")
            or beat.get("summary")
            or beat.get("text")
            or beat.get("purpose")
            or ""
        ).strip()
        if not text:
            continue
        beats.append(BeatMaterial(
            id=beat_id,
            text=text,
            evidence_ids=[str(x) for x in beat.get("evidence_ids", [])],
            asset_ids=[str(x) for x in beat.get("asset_ids", [])],
            allowed_reveals=[str(x) for x in beat.get("allowed_reveals", beat.get("reveals_so_far", []))],
            forbidden_reveals=[str(x) for x in beat.get("forbidden_reveals", [])],
            visual_priority=str(beat.get("visual_priority") or beat.get("visual") or ""),
        ))
    return beats


def choose_concept_type(beat: BeatMaterial, index: int) -> str:
    text = f"{beat.text} {beat.visual_priority}".lower()
    if "document" in text or "letter" in text or "record" in text:
        return "DOCUMENT_MOMENT"
    if "audio" in text or "call" in text or "recording" in text:
        return "ORIGINAL_AUDIO"
    if "map" in text or "location" in text or "road" in text:
        return "LOCATION_OR_MAP"
    if "contradict" in text or "but " in text:
        return "CONTRADICTION"
    return CONCEPT_TYPES[index % len(CONCEPT_TYPES)]


def make_hook(beat: BeatMaterial, concept_type: str) -> str:
    stem = beat.text.strip().split(".")[0]
    if len(stem) > 96:
        stem = stem[:93].rstrip() + "..."
    if concept_type == "QUESTION_DRIVEN":
        return stem if stem.endswith("?") else f"What does this detail change: {stem}?"
    if concept_type == "CONTRADICTION":
        return f"The detail that does not line up: {stem}"
    if concept_type == "DOCUMENT_MOMENT":
        return f"One document changes the timeline: {stem}"
    if concept_type == "LOCATION_OR_MAP":
        return f"Look closely at the location: {stem}"
    return stem


def stable_candidate_id(beat_id: str, concept_type: str, index: int) -> str:
    h = hashlib.sha1(f"{beat_id}:{concept_type}:{index}".encode()).hexdigest()[:10]
    return f"SFC_{h}"


def _asset_map(assets: list[VisualAsset] | list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for asset in assets:
        if isinstance(asset, dict):
            key = str(asset.get("asset_code") or asset.get("id") or asset.get("asset_id"))
            out[key] = asset
        else:
            out[asset.asset_code] = {
                "asset_code": asset.asset_code,
                "asset_role": asset.asset_role,
                "asset_type": asset.asset_type,
                "verification_status": asset.verification_status,
                "reveals": json.loads(asset.reveals_json or "[]"),
            }
    return out


def _best_asset_ids(asset_map: dict[str, dict], beat: BeatMaterial, *, limit: int) -> list[str]:
    if not asset_map:
        return []
    scored = []
    for asset_id, asset in asset_map.items():
        if asset.get("verification_status") in UNSELECTABLE_VERIFICATION_STATUSES:
            continue
        role = asset.get("asset_role", "")
        asset_type = asset.get("asset_type", "")
        score = 0
        if role == "evidence":
            score += 4
        if asset_type in {"photo", "document", "map", "video"}:
            score += 2
        if set(asset.get("reveals") or []) <= set(beat.allowed_reveals):
            score += 1
        scored.append((score, asset_id))
    scored.sort(reverse=True)
    return [asset_id for _, asset_id in scored[:limit]]
