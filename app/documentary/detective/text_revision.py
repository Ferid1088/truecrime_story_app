"""Text-revision requests and the audio-only audit (Section F, checklist row F3).

The narration must stand on its own: "a visual cannot secretly supply a conclusion that listeners never
hear". Two things follow.

1. When an essential explanation is missing from the final text, the exact passage and a minimal correction
   become a persisted request for the narration workflow - never an edit made by the detective toolkit.
   A request is a `DetectiveFinding` owned by `narration_workflow` (migration 0076), anchored to the exact
   words by a `ReleaseAnchor`, so nothing new is stored. `revision_items` re-shapes the open requests into
   the item shape the language pipeline's revision round already consumes (`language_pipeline.review`).
2. `audio_only_findings` is the deterministic audit: every claim a scene's visuals show must have its meaning
   in the narration spans that support it. A claim whose wording is not there is something only the screen
   says, and becomes a request."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    DetectiveClaim,
    DetectiveFinding,
    DetectiveQuestion,
    DetectiveScene,
    DetectiveToolAction,
    NarrationUnit,
    ReleaseAnchor,
)
from app.documentary import memory as MEM
from app.documentary.detective.dispatch import binding_keys

_ITEM_SEVERITY = {"blocking": "high", "major": "medium", "minor": "low"}
_OPEN = ("open", "repair_in_progress", "incomplete")


def anchor_unit(db: Session, graph_id: int, anchor_key: str, unit_key: str, passage: str | None = None) -> str:
    """Anchor `passage` inside narration unit `unit_key` (the whole unit when no passage is given or it is
    not found in the unit) and return the anchor key. The exact words are then a stable, validated pointer
    like every other release anchor."""
    unit = db.query(NarrationUnit).filter_by(graph_id=graph_id, unit_key=unit_key).first()
    if unit is None:
        raise MEM.MemoryValidationError(f"narration unit {unit_key!r} is not in graph {graph_id}")
    start = unit.text.find(passage) if passage else -1
    start, end = (start, start + len(passage)) if start >= 0 else (0, len(unit.text))
    MEM.add_release_anchor(db, graph_id, anchor_key[:80], unit.id, start, end)
    return anchor_key[:80]


def request_revision(db: Session, graph_id: int, *, finding_key: str, mode: str, unit_key: str | None,
                     passage: str | None, problem: str, proposed_repair: str, scene_key: str | None = None,
                     agent_run_id: int | None = None) -> DetectiveFinding:
    """Persist one request for the narration workflow, anchored to `passage` inside narration unit
    `unit_key` (the whole unit when the passage is not given or not found in it). Idempotent on
    `finding_key`: asking again returns the request already open, it never duplicates or rewrites it."""
    existing = db.query(DetectiveFinding).filter_by(graph_id=graph_id, finding_key=finding_key).first()
    if existing is not None:
        return existing
    anchor_key = anchor_unit(db, graph_id, f"rev:{finding_key}", unit_key, passage) if unit_key else None
    row = DetectiveFinding(
        graph_id=graph_id, finding_key=finding_key, agent_run_id=agent_run_id, mode=mode,
        severity="major", repair_owner="narration_workflow", observed_problem=problem,
        scene_key=scene_key, anchor_key=anchor_key, recommended_repair=proposed_repair, status="open")
    db.add(row)
    db.flush()
    return row


def revision_items(db: Session, graph_id: int) -> list[dict]:
    """The open narration-workflow requests as critique items (`severity`, `kind`, `act`, `quote`,
    `problem`, `instruction`), the shape `language_pipeline.review.critique_from` builds its revision
    round from. The quote is the exact passage; the caller decides whether a revision round runs."""
    rows = (db.query(DetectiveFinding)
            .filter(DetectiveFinding.graph_id == graph_id,
                    DetectiveFinding.repair_owner == "narration_workflow",
                    DetectiveFinding.status.in_(_OPEN))
            .order_by(DetectiveFinding.id).all())
    items = []
    for f in rows:
        anchor = (db.query(ReleaseAnchor).filter_by(graph_id=graph_id, anchor_key=f.anchor_key).first()
                  if f.anchor_key else None)
        unit = db.get(NarrationUnit, anchor.narration_unit_id) if anchor else None
        items.append({
            "severity": _ITEM_SEVERITY[f.severity], "kind": f"detective_{f.mode}",
            "act": unit.beat_id if unit else "",
            "quote": unit.text[anchor.char_start:anchor.char_end] if unit else "",
            "problem": f.observed_problem, "instruction": f.recommended_repair or f.observed_problem})
    return items


def _unsaid(claim: DetectiveClaim, units: dict[str, NarrationUnit]) -> str | None:
    """Why this claim's meaning is not in the narration that supports it, or None when it is."""
    support = json.loads(claim.supporting_unit_keys_json or "[]")
    if not support:
        return "no narration span supports it, so only the visual says it"
    said = " ".join(units[k].text for k in support if k in units)
    if not said:
        return f"its supporting spans {support} are not narration units"
    from app.documentary.mvp.direct import content_words

    words = content_words(claim.text)
    if not words:
        return None
    fraction = len(words & content_words(said)) / len(words)
    if fraction < ai_config.detective_toolkit.audio_only.min_supported_fraction:
        return (f"the narration says only {fraction:.0%} of this claim's content words, so a listener "
                "without the screen would not hear it")
    return None


def audio_only_findings(db: Session, graph_id: int) -> list[dict]:
    """Every claim a scene's actions bind to (directly, or as the premise of a bound question) whose
    meaning is not in the narration that supports it, one finding per scene and claim, in scene order.
    Each names the exact narration unit that should have said it and a minimal correction."""
    units = {u.unit_key: u for u in db.query(NarrationUnit).filter_by(graph_id=graph_id)}
    claims = {c.claim_key: c for c in db.query(DetectiveClaim).filter_by(graph_id=graph_id)}
    questions = {q.question_key: q for q in db.query(DetectiveQuestion).filter_by(graph_id=graph_id)}
    findings: list[dict] = []
    scenes = db.query(DetectiveScene).filter_by(graph_id=graph_id).order_by(DetectiveScene.scene_order).all()
    for scene in scenes:
        covered = [k for k in json.loads(scene.covered_unit_keys_json or "[]") if k in units]
        seen: set[str] = set()
        actions = (db.query(DetectiveToolAction).filter_by(scene_id=scene.id)
                   .order_by(DetectiveToolAction.order_index).all())
        for action in actions:
            wanted = binding_keys(action, "claim_key")
            for q_key in binding_keys(action, "question_key"):
                q = questions.get(q_key)
                premises = json.loads(q.supporting_claim_keys_json or "[]") if q else []
                if q is not None and not any(k in claims or k in units for k in premises):
                    findings.append(_finding(scene, covered, f"question:{q_key}", q.text,
                                             "the question has no premise in the narration"))
                wanted += [k for k in premises if k in claims]
            for key in wanted:
                if key in seen or key not in claims:
                    continue
                seen.add(key)
                problem = _unsaid(claims[key], units)
                if problem:
                    findings.append(_finding(scene, covered, key, claims[key].text, problem))
    return findings


def _finding(scene: DetectiveScene, covered: list[str], subject: str, shown: str, problem: str) -> dict:
    return {"finding_key": f"audio:{scene.scene_key}:{subject}"[:80], "scene_key": scene.scene_key,
            "unit_key": covered[0] if covered else None, "subject": subject,
            "problem": f"Scene {scene.scene_key} shows {shown!r}, but {problem}.",
            "proposed_repair": f"State in this passage what the visual would otherwise supply: {shown!r}."}


def request_audio_only_revisions(db: Session, graph_id: int) -> list[DetectiveFinding]:
    """Run the audit and persist each finding as a narration-workflow request (idempotent)."""
    return [request_revision(db, graph_id, finding_key=f["finding_key"], mode="audio_only_audit",
                             unit_key=f["unit_key"], passage=None, problem=f["problem"],
                             proposed_repair=f["proposed_repair"], scene_key=f["scene_key"])
            for f in audio_only_findings(db, graph_id)]
