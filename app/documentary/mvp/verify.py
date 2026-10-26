"""Step 5/8 - independent verification. Five separate decisions per (requirement, asset): provenance,
identity/context, rights, ethics and scene suitability. unknown / needs_review / transient_failure never
count as approval, and a conflict between two verifiers is stored, never averaged."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import Case, MvpCandidate, MvpRequirement, VisualAsset
from app.documentary import storage
from app.documentary.mvp.media import image_for_check
from app.documentary.visuals import images as IM
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)

RELATIONS = ("exact", "context", "unrelated", "uncertain")


@dataclass(frozen=True)
class Verdict:
    """One model judgement of one picture, normalised to the schema or marked transient."""

    ok: bool
    data: dict
    model: str | None = None


def entity_view(entity: dict) -> dict:
    """What the verifier is told about an entity: its identity and, when the case has a dossier for it,
    the plain description and the look-alikes it must not be confused with."""
    view = {"key": entity["key"], "name": entity["name"], "type": entity["type"],
            "period": entity.get("period")}
    if entity.get("visual_description"):
        view["description"] = entity["visual_description"]
    if entity.get("not_this"):
        view["not_this"] = entity["not_this"]
    return view


def provenance_decision(asset: VisualAsset) -> tuple[str, str | None]:
    """Source identity, a retrievable origin and readable bytes: 'passed' or 'needs_review'."""
    path = storage.resolve(asset.local_path) if asset.local_path else None
    if path is None or not path.exists():
        return "needs_review", "file missing"
    if not asset.sha256:
        return "needs_review", "no content hash"
    if asset.provider not in ("upload", "owned", "generated") and not (asset.source_url or asset.page_url):
        return "needs_review", "no origin URL"
    return "passed", None


def rights_decision(asset: VisualAsset, profile: str | None = None) -> tuple[str, str]:
    """cleared = publishable; preview_only = fine for an internal review film, blocked publication;
    blocked = not usable at all. Provider labels are evidence, not permission."""
    profile = profile or ai_config.mvp().verification.rights_profile
    lists = ai_config.rights.allowed_for_render
    status = asset.rights_status
    if status in lists.get("publish", []):
        return "cleared", f"{status}"
    if status == "editorial_review_required":
        if asset.editorial_approved_at is not None:
            return "cleared", "editorial review recorded"
        if status in lists.get(profile, []):
            return "preview_only", "editorial review required before publication"
    if status in lists.get(profile, []):
        return "preview_only", status
    return "blocked", f"{status}: {asset.rights_reason or 'not allowed for ' + profile}"


def _min_confidence(entity_type: str | None, relation: str) -> float:
    cfg = ai_config.mvp().verification
    return cfg.min_confidence_exact_person if relation == "exact" and entity_type == "person" \
        else cfg.min_confidence_other


def interpret(verdict: dict, requirement: MvpRequirement, entity_type: str | None) -> dict:
    """Turn a picture verdict into this requirement's identity / ethics / scene decisions."""
    cfg = ai_config.mvp().verification
    spec = json.loads(requirement.spec_json or "{}")
    accepted = set(spec.get("acceptable_roles") or [requirement.role_needed])
    match = next((m for m in verdict.get("matches") or []
                  if isinstance(m, dict) and m.get("entity") == requirement.entity_key), None)
    relation = match.get("relation") if match and match.get("relation") in RELATIONS else "unrelated"
    confidence = _num(verdict.get("confidence"), 0.0)
    reasons: list[str] = []
    role, identity = None, "rejected"
    if relation == "exact":
        role = "evidence" if "evidence" in accepted else ("context" if "context" in accepted else None)
        if role is None:
            reasons.append("shows the exact thing but only illustration was acceptable")
        elif confidence < _min_confidence(entity_type, "exact"):
            identity = "needs_review"
            reasons.append(f"exact identity at confidence {confidence:.2f}")
        else:
            identity = "approved"
    elif relation == "context":
        if "context" in accepted:
            role, identity = "context", "approved" if confidence >= cfg.min_confidence_other else "needs_review"
        else:
            reasons.append("only a contextual equivalent; this requirement needs the exact thing")
    elif relation == "uncertain":
        identity = "needs_review"
        reasons.append("verifier could not tell")
    else:
        reasons.append((match or {}).get("reason") or "not what this requirement needs")
    ethics_block = verdict.get("ethics") if isinstance(verdict.get("ethics"), dict) else {}
    ethics = {"ok": "approved", "sensitive": "approved", "unsuitable": "rejected"}.get(
        ethics_block.get("concern"), "needs_review")
    if ethics == "rejected":
        reasons.append("ethically unsuitable")
    scene = "approved"
    if verdict.get("scene_fit") == "poor" or _num(verdict.get("quality"), 1.0) < cfg.min_quality:
        scene = "rejected"
        reasons.append("scene unsuitable or too poor to hold on screen")
    overlays = verdict.get("overlays") if isinstance(verdict.get("overlays"), dict) else {}
    return {"role": role, "identity": identity, "ethics": ethics, "scene": scene,
            "watermark": bool(overlays.get("watermark")), "reasons": reasons}


def _num(value, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def final_status(candidate: MvpCandidate) -> str:
    """The aggregate decision: only all-approved is approved; any hard rejection rejects; the rest wait."""
    parts = [candidate.provenance_status, candidate.identity_status, candidate.ethics_status,
             candidate.scene_status]
    rights = candidate.rights_status
    if "transient_failure" in parts:
        return "transient_failure"
    if candidate.identity_status == "rejected" or candidate.ethics_status == "rejected" \
            or candidate.scene_status == "rejected" or rights == "blocked":
        return "rejected"
    if candidate.identity_status == "needs_review" or candidate.provenance_status == "needs_review" \
            or candidate.ethics_status == "needs_review":
        return "needs_review"
    if "unknown" in parts or rights == "unknown":
        return "pending"
    return "approved"


Judge = Callable[[Case, VisualAsset, list[dict], list[dict], str], Awaitable[Verdict]]


async def model_judge(case: Case, asset: VisualAsset, entities: list[dict], facts: list[dict],
                      agent: str = "mvp.verify", db: Session | None = None) -> Verdict:
    """One vision call. A transport error or an answer outside the schema is a transient failure,
    never a rejection of the picture."""
    payload = {
        "case": case.canonical_title,
        "media": ("video: start, middle and end frames, left to right"
                  if asset.asset_type == "video" else "image"),
        "title": asset.title, "caption": asset.caption, "page_url": asset.page_url,
        "source": asset.source_name, "found_for": asset.found_for,
        "entity_list": entities, "facts": [{"id": f["id"], "claim": f["claim"]} for f in facts],
    }
    try:
        image = IM.data_url(image_for_check(asset))
        gen = get_generation_provider()
        with track_run(db, case.id, "Main Visual Planner: verify", input_summary=asset.asset_code) as run:
            body = json.dumps(payload, ensure_ascii=False)
            if agent == "mvp.verify_second":
                data, res = await run_agent("mvp.verify_second", gen, body, images=[image])
            else:
                data, res = await run_agent("mvp.verify", gen, body, images=[image])
            stamp_run(run, res, ai_config.agents[agent].role)
    except (GenerationError, OSError, IM.ImageError) as e:
        log.warning("verify %s failed transiently: %s", asset.asset_code, e)
        return Verdict(False, {"error": f"{type(e).__name__}: {e}"})
    if not isinstance(data, dict) or not isinstance(data.get("matches"), list):
        return Verdict(False, {"error": "answer outside the schema"}, getattr(res, "model", None))
    return Verdict(True, data, getattr(res, "model", None))


def apply_verdict(db: Session, cand: MvpCandidate, asset: VisualAsset, requirement: MvpRequirement,
                  verdict: Verdict, entity_type: str | None, verifier: str,
                  profile: str | None = None) -> MvpCandidate:
    prov, prov_reason = provenance_decision(asset)
    rights, rights_reason = rights_decision(asset, profile)
    reasons = [r for r in (prov_reason, None if rights != "blocked" else rights_reason) if r]
    cand.provenance_status = prov
    cand.rights_status = rights
    if not verdict.ok:
        cand.identity_status = cand.ethics_status = cand.scene_status = "transient_failure"
        reasons.append(verdict.data.get("error", "transient failure"))
    else:
        out = interpret(verdict.data, requirement, entity_type)
        if out["watermark"]:
            cand.rights_status, rights_reason = "blocked", "watermark visible"
            reasons.append(rights_reason)
        cand.role = out["role"]
        cand.identity_status, cand.ethics_status, cand.scene_status = (
            out["identity"], out["ethics"], out["scene"])
        reasons += out["reasons"]
    log_entry = {"verifier": verifier, "model": verdict.model, "ok": verdict.ok,
                 "confidence": verdict.data.get("confidence") if verdict.ok else None}
    cand.verifiers_json = json.dumps(json.loads(cand.verifiers_json or "[]") + [log_entry])
    cand.reasons_json = json.dumps(reasons, ensure_ascii=False)
    cand.final_status = final_status(cand)
    db.commit()
    return cand


async def verify_asset(db: Session, case: Case, asset: VisualAsset,
                       items: list[tuple[MvpCandidate, MvpRequirement]], entities: list[dict],
                       facts: list[dict], judge: Judge | None = None,
                       second: Judge | None = None) -> list[MvpCandidate]:
    """Judge one picture once for every requirement it is a candidate for (one vision call, not one per
    requirement). An uncertain exact identity is escalated, once per picture, to the independent second
    verifier; if the two disagree the conflict is stored and the candidate waits for review."""
    cfg = ai_config.mvp().verification
    wanted = {req.entity_key for _c, req in items}
    shown = [entity_view(e) for e in entities if e["key"] in wanted][:cfg.max_requirements_per_asset]
    judge = judge or (lambda c, a, e, f, agent: model_judge(c, a, e, f, agent, db))
    verdict = await judge(case, asset, shown, facts, "mvp.verify")
    retries = cfg.transient_failure_retries
    while not verdict.ok and retries > 0:
        retries -= 1
        verdict = await judge(case, asset, shown, facts, "mvp.verify")
    other: Verdict | None = None
    for cand, req in items:
        ent = next((e for e in entities if e["key"] == req.entity_key), None)
        etype = ent["type"] if ent else None
        apply_verdict(db, cand, asset, req, verdict, etype, "mvp.verify")
        if not (cand.identity_status == "needs_review" and verdict.ok
                and cfg.escalate_conflicts_to_second_verifier):
            continue
        if other is None:
            second = second or (lambda c, a, e, f, agent: model_judge(c, a, e, f, agent, db))
            other = await second(case, asset, shown, facts, "mvp.verify_second")
        if not other.ok:
            continue
        first_out = interpret(verdict.data, req, etype)
        second_out = interpret(other.data, req, etype)
        if first_out["role"] == second_out["role"] and second_out["identity"] == "approved":
            apply_verdict(db, cand, asset, req, other, etype, "mvp.verify_second")
        else:
            cand.reasons_json = json.dumps(json.loads(cand.reasons_json) + ["verifiers disagree; stored for review"])
            cand.verifiers_json = json.dumps(json.loads(cand.verifiers_json) + [
                {"verifier": "mvp.verify_second", "model": other.model, "ok": True,
                 "identity": second_out["identity"]}])
            db.commit()
    return [c for c, _r in items]


async def verify_candidate(db: Session, case: Case, cand: MvpCandidate, requirement: MvpRequirement,
                           entities: list[dict], facts: list[dict], judge: Judge | None = None,
                           second: Judge | None = None) -> MvpCandidate:
    asset = db.get(VisualAsset, cand.visual_asset_id)
    await verify_asset(db, case, asset, [(cand, requirement)], entities, facts, judge, second)
    return cand


def sync_asset_status(db: Session, case_id: int) -> dict:
    """The library asset carries the aggregate of its candidates so the production script's usability
    check sees it: approved somewhere -> verified; rejected everywhere it was judged -> rejected;
    otherwise needs_review. Assets never judged stay as they are."""
    counts = {"verified": 0, "rejected": 0, "needs_review": 0}
    rows = (db.query(MvpCandidate, MvpRequirement)
            .join(MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id)
            .filter(MvpRequirement.case_id == case_id).all())
    by_asset: dict[int, list[MvpCandidate]] = {}
    for cand, _req in rows:
        by_asset.setdefault(cand.visual_asset_id, []).append(cand)
    for asset_id, cands in by_asset.items():
        asset = db.get(VisualAsset, asset_id)
        statuses = {c.final_status for c in cands}
        if "approved" in statuses:
            asset.verification_status = "verified"
            approved = next(c for c in cands if c.final_status == "approved" and c.role)
            asset.asset_role = approved.role or asset.asset_role
            counts["verified"] += 1
        elif statuses <= {"rejected"}:
            asset.verification_status = "rejected"
            counts["rejected"] += 1
        else:
            asset.verification_status = "needs_review"
            counts["needs_review"] += 1
    db.commit()
    return counts
