"""Step 5/8 - independent verification. Five separate decisions per (requirement, asset): provenance,
identity/context, rights, ethics and scene suitability. unknown / needs_review / transient_failure never
count as approval, and a conflict between two verifiers is stored, never averaged."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

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


OVERRIDE_PREFIX = "owner override of "


def owner_rights_override(asset: VisualAsset, who: str = "owner") -> None:
    """The owner decided to use a picture the rights check blocked. It becomes a news-style picture with the owner's
    approval recorded (what the review film needs) and keeps what it was in its reason; the app never treats it as
    cleared for publication."""
    if (asset.rights_reason or "").startswith(OVERRIDE_PREFIX):
        return
    was = f"{asset.rights_status}: {asset.rights_reason or 'no reason stored'}"
    asset.rights_status = "editorial_review_required"
    asset.rights_reason = f"{OVERRIDE_PREFIX}{was}"[:300]
    asset.editorial_approved_by = who
    asset.editorial_approved_at = datetime.now().astimezone()


def rights_decision(asset: VisualAsset, profile: str | None = None) -> tuple[str, str]:
    """cleared = publishable; preview_only = fine for an internal review film, blocked publication;
    blocked = not usable at all. Provider labels are evidence, not permission."""
    profile = profile or ai_config.mvp().verification.rights_profile
    if (asset.rights_reason or "").startswith(OVERRIDE_PREFIX):
        return "preview_only", "the owner decided to use it; publication needs a licence"
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
    if relation in ("exact", "context") and not str(match.get("signal") or "").strip():
        relation = "uncertain"                 # a claimed match must name what identifies it
    confidence = _num(verdict.get("confidence"), 0.0)
    reasons: list[str] = []
    role, identity = None, "rejected"
    if relation == "exact":
        role = next((r for r in ("evidence", "context", "illustration") if r in accepted), None)
        if role is None:
            reasons.append("shows the exact thing but only illustration was acceptable")
        elif confidence < _min_confidence(entity_type, "exact"):
            identity = "needs_review"
            reasons.append(f"exact identity at confidence {confidence:.2f}")
        else:
            identity = "approved"
    elif relation == "context":
        if "context" in accepted or "illustration" in accepted:
            role = "context" if "context" in accepted else "illustration"
            identity = "approved" if confidence >= cfg.min_confidence_other else "needs_review"
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

INSPECTOR_REMOVAL = "inspector_removal"
DUPLICATE_FILTER = "duplicate_filter"


def owner_settled(candidate: MvpCandidate) -> str | None:
    """What the owner has decided for this candidate, if anything: the latest entry of the owner decides ("approve" or
    "reject"), and an inspector's removal after it (an item that trimming and cutting could not make usable) rejects it.
    This is the only exception to an owner's decision standing; nothing else changes it but the owner again."""
    state = None
    for entry in json.loads(candidate.verifiers_json or "[]"):
        if entry.get("verifier") == "owner":
            state = "approved" if entry.get("decision") == "approve" else "rejected"
        elif entry.get("verifier") == INSPECTOR_REMOVAL:
            state = "rejected"
    return state


def is_duplicate(candidate: MvpCandidate) -> bool:
    """The duplicate filter marked this picture as a copy of another one that the planner keeps."""
    return any(e.get("verifier") == DUPLICATE_FILTER for e in json.loads(candidate.verifiers_json or "[]"))


def final_status(candidate: MvpCandidate) -> str:
    """The aggregate decision: an owner's decision stands; otherwise a duplicate is rejected, only all-approved is
    approved, any hard rejection rejects, and the rest wait."""
    settled = owner_settled(candidate)
    if settled is not None:
        return settled
    if is_duplicate(candidate):
        return "rejected"
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


def remove_unusable(candidate: MvpCandidate, reason: str) -> None:
    """The inspector takes out an approved item that trimming and cutting could not make usable, and says why."""
    candidate.verifiers_json = json.dumps(json.loads(candidate.verifiers_json or "[]")
                                          + [{"verifier": INSPECTOR_REMOVAL, "decision": "remove", "note": reason}])
    candidate.final_status = final_status(candidate)


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
        image = IM.data_url(await asyncio.to_thread(image_for_check, asset))
        gen = get_generation_provider()
        timeout = ai_config.mvp().call_timeout_s
        with track_run(db, case.id, "Main Visual Planner: verify", input_summary=asset.asset_code) as run:
            body = json.dumps(payload, ensure_ascii=False)
            if agent == "mvp.verify_second":
                data, res = await asyncio.wait_for(
                    run_agent("mvp.verify_second", gen, body, images=[image]), timeout)
            else:
                data, res = await asyncio.wait_for(
                    run_agent("mvp.verify", gen, body, images=[image]), timeout)
            stamp_run(run, res, ai_config.agents[agent].role)
    except (GenerationError, OSError, IM.ImageError, TimeoutError) as e:
        log.warning("verify %s failed transiently: %s", asset.asset_code, type(e).__name__)
        return Verdict(False, {"error": f"{type(e).__name__}: {e}"})
    if not isinstance(data, dict) or not isinstance(data.get("matches"), list):
        return Verdict(False, {"error": "answer outside the schema"}, getattr(res, "model", None))
    return Verdict(True, data, getattr(res, "model", None))


def remember_picture(asset: VisualAsset, verdict: Verdict) -> None:
    """What the picture was found to show stays on the asset, so the director matches pictures to the
    narration by what they depict, not by where they were found."""
    if not verdict.ok:
        return
    depicts = str(verdict.data.get("depicts") or "").strip()
    if depicts:
        asset.description = depicts[:1000]
    if verdict.data.get("subject_type"):
        asset.subject_type = str(verdict.data["subject_type"])[:20]


def apply_verdict(db: Session, cand: MvpCandidate, asset: VisualAsset, requirement: MvpRequirement,
                  verdict: Verdict, entity_type: str | None, verifier: str,
                  profile: str | None = None) -> MvpCandidate:
    remember_picture(asset, verdict)
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


def _first_could_not_tell(verdict: dict, requirement: MvpRequirement) -> bool:
    """The first verifier said 'uncertain' for this entity (not 'unrelated'): that is a lack of information,
    not a disagreement, so an independent second verifier that meets the confidence bar settles it."""
    match = next((m for m in verdict.get("matches") or []
                  if isinstance(m, dict) and m.get("entity") == requirement.entity_key), None)
    return bool(match) and match.get("relation") == "uncertain"


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
        if second_out["identity"] == "approved" and (
                first_out["role"] == second_out["role"] or _first_could_not_tell(verdict.data, req)):
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


WATERMARK_REASON = "watermark visible"


def release_watermark_blocks(db: Session, case_id: int) -> int:
    """Candidates that an earlier rule blocked for a visible watermark, with the block lifted: a watermark is cut away
    by the overlay scan (which gives a picture up only when it cannot be cut clean), so it no longer decides the
    picture's rights. The rights are decided again from the asset itself; a picture that is blocked for another reason
    stays blocked. Returns how many candidates were released."""
    from app.documentary.visuals.rights import reclassify_watermarked

    for asset in db.query(VisualAsset).filter(VisualAsset.case_id == case_id, VisualAsset.rights_status == "do_not_use",
                                              VisualAsset.rights_reason == WATERMARK_REASON):
        reclassify_watermarked(asset)
    released = 0
    for cand in (db.query(MvpCandidate).join(MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id)
                 .filter(MvpRequirement.case_id == case_id, MvpCandidate.rights_status == "blocked")):
        reasons = json.loads(cand.reasons_json or "[]")
        mine = [r for r in reasons if r == WATERMARK_REASON or r.endswith(f": {WATERMARK_REASON}")]
        if not mine:
            continue
        cand.reasons_json = json.dumps([r for r in reasons if r not in mine], ensure_ascii=False)
        status, _ = rights_decision(db.get(VisualAsset, cand.visual_asset_id))
        if status != "blocked":
            cand.rights_status = status
            cand.final_status = final_status(cand)
            released += 1
    db.commit()
    return released


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
