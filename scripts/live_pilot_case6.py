"""Generate, gate, approve, persist, and export the real case-6 pilot.

This command is deliberately explicit about the human approval boundary. It
uses the persisted blueprint, RevealGraph, EpistemicContracts, and VisualAsset
rows. Unknown-rights assets are never selected; editorial-review assets are
only accepted after a separate ``--asset-signoff`` file names the approved
asset ids. ``--approve`` records concept approval only. Nothing publishes or
schedules content.
"""

from __future__ import annotations

import argparse
import itertools
import json
import shutil
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.base import SessionLocal
from app.db.models import (
    EditorialBlueprint,
    EpistemicClaim,
    EpistemicContractSet,
    RevealGraph,
    ShortFormConcept,
    VisualAsset,
)
from app.longform.service import spoiler_horizon
from app.shortform.director import (
    ShortFormDirectorAgent,
    extract_beats,
    make_hook,
    stable_candidate_id,
)
from app.shortform.export import build_export_package
from app.shortform.operations import repetition_check
from app.shortform.selection import evaluate_candidate, mmr_select
from app.shortform.assets import require_verified_case_assets, UNSELECTABLE_VERIFICATION_STATUSES


CASE_ID = 6
PILOT_TAG = "live_pilot_case6_v1"
PLATFORM_COUNTS = {
    "youtube_short": 3,
    "instagram_reel": 3,
    "tiktok_video": 3,
    "facebook_reel": 2,
}


def _json(value, default):
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _beat_map(blueprint: EditorialBlueprint) -> dict[str, dict]:
    return {str(beat["id"]): beat for beat in _json(blueprint.blueprint_json, {}).get("beats", [])}


def _claims_by_beat(claims: list[EpistemicClaim]) -> dict[str, list[EpistemicClaim]]:
    grouped: dict[str, list[EpistemicClaim]] = {}
    for claim in claims:
        grouped.setdefault(str(claim.beat_id), []).append(claim)
    return grouped


def _candidate_asset_pool(assets: list[VisualAsset]) -> list[VisualAsset]:
    # Unknown rights and rejected/unverified verification states are hard
    # stops. Use distinct real assets so the live
    # batch can be checked for visual repetition instead of reusing one image.
    return [
        asset for asset in assets
        if asset.rights_status != "unknown"
        and asset.verification_status not in UNSELECTABLE_VERIFICATION_STATUSES
    ]


def _prepare_candidates(
    db: Session,
    blueprint: EditorialBlueprint,
    graph: RevealGraph,
    contract: EpistemicContractSet,
) -> list[dict]:
    beats = {beat.id: beat for beat in extract_beats(_json(blueprint.blueprint_json, {}))}
    claims = _claims_by_beat(list(contract.claims))
    assets = db.query(VisualAsset).filter_by(case_id=CASE_ID).order_by(VisualAsset.id).all()
    pool = _candidate_asset_pool(assets)
    if len(pool) < 14:
        raise RuntimeError(f"case 6 has only {len(pool)} non-unknown assets; 14 are required")
    raw = ShortFormDirectorAgent().generate_from_blueprint(
        _json(blueprint.blueprint_json, {}), assets=assets
    )
    prepared = []
    for index, candidate in enumerate(raw):
        beat_id = candidate["source_beat_ids"][0]
        horizon = spoiler_horizon(db, graph.id, beat_id=beat_id)
        beat_claims = claims.get(beat_id, [])
        used_assets = {item["asset_ids"][0] for item in prepared}
        asset = next(
            (
                candidate_asset for candidate_asset in pool
                if candidate_asset.asset_code not in used_assets
                and all(
                    link.node.node_key in set(horizon["allowed"])
                    for link in graph.asset_links
                    if link.visual_asset_id == candidate_asset.id
                )
            ),
            None,
        )
        if asset is None:
            raise RuntimeError(f"no rights-safe, spoiler-safe unused asset for beat {beat_id}")
        linked_nodes = [
            link.node.node_key for link in graph.asset_links
            if link.visual_asset_id == asset.id
        ]
        candidate = {
            **candidate,
            "asset_ids": [asset.asset_code],
            "asset_details": [{
                "asset_id": asset.asset_code,
                "rights_status": asset.rights_status,
                "platform_claim_risk": "low",
                "reveals": linked_nodes,
                "human_signoff": False,
            }],
            "allowed_reveals": horizon["allowed"],
            "forbidden_reveals": horizon["forbidden"],
            "claims": [{
                "source_claim_id": claim.claim_key,
                "source_modality": claim.modality,
                "short_modality": claim.modality,
                "text": claim.claim_text,
            } for claim in beat_claims],
            "synthetic_voice": True,
            "disclosure_flags": {platform: True for platform in PLATFORM_COUNTS},
            "opening_visual": asset.asset_code,
            "host_pose": [
                "HOST_MEDIUM", "HOST_CLOSE", "HOST_WIDE", "HOST_PROFILE",
                "HOST_OVER_SHOULDER", "HOST_DESK", "HOST_WALKING", "HOST_SILHOUETTE",
                "HOST_MEDIUM_LEFT", "HOST_CLOSE_LEFT", "HOST_WIDE_LEFT", "HOST_PROFILE_LEFT",
                "HOST_DESK_RIGHT", "HOST_WALKING_RIGHT",
            ][index],
            "hook_structure": candidate["concept_type"],
            "cta": f"Watch the evidence-led case in full, chapter {index + 1}.",
            "pilot_tag": PILOT_TAG,
            "episode_identity_id": blueprint.id,
        }
        prepared.append(candidate)
    return prepared


def _load_persisted_candidates(
    db: Session,
    rows: list[ShortFormConcept],
    blueprint: EditorialBlueprint,
) -> list[dict]:
    """Rebuild gate inputs from persisted concepts without generating new ones."""
    beats = {beat.id: beat for beat in extract_beats(_json(blueprint.blueprint_json, {}))}
    assets = {
        asset.asset_code: asset
        for asset in db.query(VisualAsset).filter_by(case_id=CASE_ID).all()
    }
    candidates = []
    for index, row in enumerate(rows):
        source_beats = _json(row.source_beat_ids_json, [])
        beat = beats.get(str(source_beats[0])) if source_beats else {}
        asset_codes = _json(row.asset_ids_json, [])
        details = []
        for code in asset_codes:
            asset = assets.get(str(code))
            if asset is None:
                raise RuntimeError(f"persisted concept {row.id} references missing asset {code}")
            details.append({
                "asset_id": asset.asset_code,
                "rights_status": asset.rights_status,
                "verification_status": asset.verification_status,
                "platform_claim_risk": "low",
                "reveals": _json(asset.reveals_json, []),
                "human_signoff": False,
            })
        candidates.append({
            "id": stable_candidate_id(str(source_beats[0]), row.concept_type, index),
            "concept_type": row.concept_type,
            "hook": make_hook(beat, row.concept_type),
            "source_beat_ids": source_beats,
            "evidence_ids": _json(row.evidence_ids_json, []),
            "asset_ids": asset_codes,
            "asset_details": details,
            "allowed_reveals": _json(row.allowed_reveals_json, []),
            "forbidden_reveals": _json(row.forbidden_reveals_json, []),
            "claims": _json(row.claims_json, []),
            "synthetic_voice": True,
            "disclosure_flags": {platform: True for platform in PLATFORM_COUNTS},
            "cta": f"Watch the evidence-led case in full, chapter {index + 1}.",
            "pilot_tag": PILOT_TAG,
            "episode_identity_id": blueprint.id,
            "_row_id": row.id,
        })
    return candidates


def _gate_all_platforms(candidate: dict, *, asset_signoffs: set[str]) -> dict:
    details = json.loads(json.dumps(candidate["asset_details"]))
    for asset in details:
        asset["human_signoff"] = asset["asset_id"] in asset_signoffs
    candidate["asset_details"] = details
    platform_results = {
        platform: evaluate_candidate(candidate, platform=platform)
        for platform in PLATFORM_COUNTS
    }
    blocked_assets = [
        asset["asset_id"] for asset in details
        if asset.get("verification_status") in UNSELECTABLE_VERIFICATION_STATUSES
    ]
    if blocked_assets:
        for result in platform_results.values():
            result["gate_results"]["VisualVerificationGate"] = {
                "passed": False,
                "reasons": [
                    f"{asset_id} is not selectable (verification_status=rejected or unverified)"
                    for asset_id in blocked_assets
                ],
                "offending_ids": blocked_assets,
                "metadata": {"blocked_asset_ids": blocked_assets},
            }
            result["passed_hard_gates"] = False
    return {
        "platforms": {
            platform: result["gate_results"]
            for platform, result in platform_results.items()
        },
        "all_platforms_passed": all(
            result["passed_hard_gates"] for result in platform_results.values()
        ),
        "soft_scores": platform_results["youtube_short"]["soft_scores"],
    }


def _persist(
    db: Session,
    candidates: list[dict],
    selected: list[dict],
    *,
    existing_rows: list[ShortFormConcept] | None = None,
) -> list[ShortFormConcept]:
    if existing_rows:
        rows_by_id = {row.id: row for row in existing_rows}
        selected_ids = {candidate["id"] for candidate in selected}
        for candidate in candidates:
            row = rows_by_id[candidate["_row_id"]]
            row.gate_results_json = json.dumps({
                **candidate["_pilot_gate"],
                "human_approval": {
                    "status": "approved" if candidate["id"] in selected_ids else "not_selected",
                    "approval_record": PILOT_TAG,
                    "asset_signoff_record": "reports/shortform/case6_asset_signoff.json",
                },
            }, ensure_ascii=False)
            row.soft_scores_json = json.dumps(candidate["_pilot_gate"]["soft_scores"])
            row.status = "approved" if candidate["id"] in selected_ids else "rejected"
        db.commit()
        return existing_rows
    selected_ids = {candidate["id"] for candidate in selected}
    rows = []
    for candidate in candidates:
        gate = candidate["_pilot_gate"]
        selected_row = candidate["id"] in selected_ids
        row = ShortFormConcept(
            case_id=CASE_ID,
            episode_identity_id=candidate["episode_identity_id"],
            concept_type=candidate["concept_type"],
            source_beat_ids_json=json.dumps(candidate["source_beat_ids"]),
            evidence_ids_json=json.dumps(candidate["evidence_ids"]),
            asset_ids_json=json.dumps(candidate["asset_ids"]),
            allowed_reveals_json=json.dumps(candidate["allowed_reveals"]),
            forbidden_reveals_json=json.dumps(candidate["forbidden_reveals"]),
            claims_json=json.dumps(candidate["claims"]),
            gate_results_json=json.dumps({**gate, "human_approval": {
                "status": "approved" if selected_row else "not_selected",
                "approval_record": PILOT_TAG,
            }}, ensure_ascii=False),
            soft_scores_json=json.dumps(gate["soft_scores"]),
            status="approved" if selected_row else "rejected",
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return rows


def _select_pilot_candidates(candidates: list[dict]) -> tuple[list[dict], list[str]]:
    gated = [candidate for candidate in candidates if candidate["_pilot_gate"]["all_platforms_passed"]]
    if len(gated) < 6:
        return [], [f"only {len(gated)} candidates passed all platform gates"]
    best: tuple[float, list[dict], list[str]] | None = None
    for size in range(8, 5, -1):
        for subset in itertools.combinations(gated, size):
            selected = list(subset)
            passed, reasons = repetition_check(selected, max_repeats=1)
            if not passed:
                continue
            if len({item["concept_type"] for item in selected}) < 2:
                continue
            score = sum(item["_pilot_gate"]["soft_scores"]["total"] for item in selected)
            if best is None or score > best[0]:
                best = (score, selected, reasons)
        if best is not None:
            return best[1], best[2]
    return [], ["no 6-8 candidate subset passed the live repetition and diversity gates"]


def _export(
    selected: list[dict],
    rows: list[ShortFormConcept],
    output_dir: Path,
    *,
    pilot_run_id: str = "",
    pilot_generated_at: str = "",
) -> list[dict]:
    if not selected:
        return []
    packages = []
    for platform_index, (platform, count) in enumerate(PLATFORM_COUNTS.items()):
        ranked = sorted(selected, key=lambda item: item["_pilot_gate"]["soft_scores"]["total"], reverse=True)
        # Spread the approved concepts across variants. Reuse is allowed, but
        # no platform should consume only the first three concepts by default.
        offset = (platform_index * 2) % len(ranked)
        ordered = ranked[offset:] + ranked[:offset]
        for index, candidate in enumerate(ordered[:count]):
            cfg = ai_config.short_form.distribution[platform]
            folder = output_dir / platform / f"{index + 1:02d}_{candidate['id']}"
            asset = candidate["asset_details"][0]
            manifest = build_export_package(
                folder,
                title=candidate["hook"],
                narration=[candidate["hook"],
                           "The case record keeps the claim's modality.",
                           candidate["cta"]],
                duration_seconds=float(cfg.target_seconds),
                rights_status=asset["rights_status"],
                rights_source=f"visual_asset:{asset['asset_id']}",
                rights_human_signoff=bool(asset.get("human_signoff")),
            )
            manifest.update({
                "case_id": CASE_ID,
                "concept_id": candidate["id"],
                "platform": platform,
                "campaign_id": f"{candidate['id']}-{platform}-en",
                "rights_review": candidate["asset_details"],
                "source_beat_ids": candidate["source_beat_ids"],
                "gate_status": "passed",
                "publication_status": "exported_only",
                "pilot_run_id": pilot_run_id,
                "pilot_generated_at": pilot_generated_at,
            })
            (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            packages.append({"platform": platform, "concept_id": candidate["id"], "path": str(folder)})
    return packages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve", action="store_true", help="persist selected concepts as approved")
    parser.add_argument(
        "--asset-signoff", type=Path,
        help="JSON file containing asset codes with explicit human rights sign-off",
    )
    parser.add_argument("--output", type=Path, default=Path("reports/shortform/live_pilot_case6"))
    args = parser.parse_args()
    if not args.approve:
        raise SystemExit("Refusing to persist or export: rerun with --approve after human review of the candidate report")
    asset_signoffs = set()
    if args.asset_signoff:
        payload = json.loads(args.asset_signoff.read_text(encoding="utf-8"))
        asset_signoffs = {str(asset_id) for asset_id in payload.get("approved_asset_ids", [])}
    # A rerun must not leave prior blocked or superseded manifests beside the
    # current package set where they can be mistaken for current output.
    if args.output.exists():
        shutil.rmtree(args.output)
    pilot_generated_at = datetime.now(timezone.utc).isoformat()
    pilot_run_id = f"{PILOT_TAG}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    with SessionLocal() as db:
        blueprint = db.query(EditorialBlueprint).filter_by(case_id=CASE_ID, status="valid").one()
        graph = db.query(RevealGraph).filter_by(case_id=CASE_ID, status="validated").one()
        contract = db.query(EpistemicContractSet).filter_by(case_id=CASE_ID, status="approved").one()
        require_verified_case_assets(db, CASE_ID)
        existing_rows = (db.query(ShortFormConcept).filter_by(case_id=CASE_ID)
                         .order_by(ShortFormConcept.id).all())
        if existing_rows:
            candidates = _load_persisted_candidates(db, existing_rows, blueprint)
            persistence_mode = "updated_existing_rows"
        else:
            candidates = _prepare_candidates(db, blueprint, graph, contract)
            persistence_mode = "created_rows"
        gated = []
        for candidate in candidates:
            result = _gate_all_platforms(candidate, asset_signoffs=asset_signoffs)
            candidate["_pilot_gate"] = result
            if result["all_platforms_passed"]:
                gated.append(candidate)
        selected, repetition_reasons = _select_pilot_candidates(candidates)
        rows = _persist(db, candidates, selected, existing_rows=existing_rows)
        packages = _export(
            selected,
            rows,
            args.output,
            pilot_run_id=pilot_run_id,
            pilot_generated_at=pilot_generated_at,
        )
        blocked_candidates = [
            {
                "concept_id": candidate["id"],
                "platform_reasons": {
                    platform: {
                        gate: result["reasons"]
                        for gate, result in gates.items()
                        if not result["passed"]
                    }
                    for platform, gates in candidate["_pilot_gate"]["platforms"].items()
                    if any(not result["passed"] for result in gates.values())
                },
            }
            for candidate in candidates
            if not candidate["_pilot_gate"]["all_platforms_passed"]
        ]
        print(json.dumps({
            "case_id": CASE_ID,
            "pilot_tag": PILOT_TAG,
            "pilot_run_id": pilot_run_id,
            "pilot_generated_at": pilot_generated_at,
            "generated": len(candidates),
            "passed_all_platform_gates": len(gated),
            "approved": len(selected),
            "persisted_rows": len(rows),
            "existing_rows": len(existing_rows),
            "persistence_mode": persistence_mode,
            "blocked_candidates": blocked_candidates,
            "status_counts": dict(Counter(row.status for row in rows)),
            "packages": packages,
            "package_counts": dict(Counter(item["platform"] for item in packages)),
            "published": False,
            "repetition_gate": {"passed": bool(selected), "reasons": repetition_reasons},
            "pilot_status": "exported" if packages else "blocked_no_eligible_candidates",
        }, indent=2))


if __name__ == "__main__":
    main()
