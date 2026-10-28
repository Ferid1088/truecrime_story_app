"""The Film Experience Reviewer (Section J): checks the detective's achieved purpose and film
continuity, and routes findings to their owning repair stage — closing checklist rows H3/H4.

    raw = await review(db, graph_id, mode="plan_audit", language="en", text_version=1)
    findings = persist_findings(db, graph_id, raw)
    outcomes = await repair_findings(db, graph_id, findings, language="en", text_version=1)

Reuses Section I's `inspector.plan_summary`/`design_reviews_for` for `detective_plan`/
`design_reviews`, and Section C's `director.all_narration` for `final_narration` — this module
adds no new planning data, only the review/routing layer on top of what B-I already persist.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.prompts import prompt
from app.db.models import DetectiveFinding, ProductionScript, RevealGraph
from app.documentary import memory as MEM
from app.documentary.detective import director as DIR
from app.documentary.detective import inspector as INSP
from app.documentary.detective import pacing as PA
from app.documentary.detective import text_revision as TR
from app.documentary.detective import review_schema as RS
from app.documentary.detective.review_schema import ReviewOutputError, validate_review_output
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run


class ReviewValidationFailed(ValueError):
    def __init__(self, violations: list[str]):
        super().__init__("; ".join(violations))
        self.violations = violations


def _compiled_script(db: Session, graph: RevealGraph) -> dict | None:
    script = (db.query(ProductionScript).filter_by(blueprint_id=graph.blueprint_id)
              .order_by(ProductionScript.id.desc()).first())
    if script is None:
        return None
    body = json.loads(script.script_json or "{}")
    return {"duration": body.get("duration"), "shots": len(body.get("shots") or []),
           "board": body.get("board"), "detective_graphics": body.get("detective_graphics"),
           "overlays": body.get("overlays"), "credits": body.get("credits")}


def build_input(db: Session, graph: RevealGraph, *, mode: str, language: str, text_version: int,
                run_id: str, film_id: str, review_scope: list[str] | None = None,
                previous_findings: list[dict] | None = None) -> dict:
    compiled = _compiled_script(db, graph)
    payload = {
        "schema_version": "1.0", "run_id": run_id, "film_id": film_id, "language": language,
        "text_version": text_version, "mode": mode,
        "final_narration": DIR.all_narration(db, graph.id),
        "detective_plan": INSP.plan_summary(db, graph),
        "design_reviews": INSP.design_reviews_for(db, graph),
        "review_scope": review_scope or ["full_plan"],
        "deterministic_validation": {
            "checked": "schema/anchor validation from Sections B-H, plus the audio-only audit and the "
                       "question-payoff / return-reason / breathing-room checks",
            "audio_only_findings": TR.audio_only_findings(db, graph.id),
            "pacing_findings": PA.c3_report(db, graph.id)["findings"]},
    }
    payload["schema_hints"] = {
        "finding_fields": list(RS.FINDING_FIELDS),
        "repair_owner_values": sorted(RS.VALID_REPAIR_OWNERS),
        "severity_values": ["blocking", "major", "minor"],
        "outcome_values": sorted(RS.VALID_OUTCOMES),
        "inspection_mode_values": sorted(RS.MAX_MODES_FOR[mode]),
        "finding_format_rule": prompt("documentary/detective/finding_format_rule"),
    }
    if compiled is not None:
        payload["compiled_script"] = compiled
    if mode == "repair_check":
        payload["previous_findings"] = previous_findings or []
    # plan_audit/repair_check without a rendered file: no actual_media key at all — the reviewer
    # must infer transcript_only/plan-level scope itself rather than being told "no media exists"
    # and asked to describe that limitation, per the doc's own "be explicit about inspection scope".
    return payload


async def review(db: Session, graph_id: int, *, mode: str, language: str, text_version: int,
                 run_id: str = "j-review", film_id: str | None = None,
                 review_scope: list[str] | None = None,
                 previous_findings: list[dict] | None = None) -> dict:
    graph = db.get(RevealGraph, graph_id)
    if graph is None:
        raise ValueError(f"reveal graph {graph_id} not found")
    payload = build_input(db, graph, mode=mode, language=language, text_version=text_version,
                          run_id=run_id, film_id=film_id or f"case-{graph.case_id}",
                          review_scope=review_scope, previous_findings=previous_findings)
    with track_run(db, graph.case_id, f"Film Experience Reviewer: {mode}",
                   input_summary=f"graph={graph_id} mode={mode}") as run:
        raw, res = await run_agent("detective.film_reviewer", get_generation_provider(),
                                   json.dumps(payload, ensure_ascii=False))
        stamp_run(run, res, "detective_film_reviewer")
        run_id_db = run.id
    violations = validate_review_output(raw)
    if violations:
        raise ReviewValidationFailed(violations)
    raw["_agent_run_id"] = run_id_db
    return raw


def persist_findings(db: Session, graph_id: int, raw: dict) -> list[DetectiveFinding]:
    """One row per finding in the agent's validated output. A `finding_key` already seen for this
    graph is updated in place (same open problem re-reported by a later audit); a new one is
    inserted `open`. Never auto-closes anything here — only `recheck_finding` (after a real repair
    and a real re-review) may close a finding."""
    rows = []
    for f in raw.get("findings") or []:
        existing = (db.query(DetectiveFinding)
                   .filter_by(graph_id=graph_id, finding_key=f["id"]).first())
        row = existing or DetectiveFinding(graph_id=graph_id, finding_key=f["id"])
        row.agent_run_id = raw.get("_agent_run_id")
        row.mode = raw["mode"]
        row.severity = f.get("severity", "minor")
        row.repair_owner = f["repair_owner"]
        row.observed_problem = f.get("observed_problem", "")
        row.scene_key = f.get("scene_id")
        row.action_key = f.get("action_id")
        row.anchor_key = f.get("anchor")
        if f["repair_owner"] == "narration_workflow" and f.get("anchor"):
            # the reviewer names the passage by narration unit; anchoring it makes the exact words a
            # stable pointer the writing workflow can quote (text_revision.revision_items)
            try:
                row.anchor_key = TR.anchor_unit(db, graph_id, f"rev:{f['id']}", f["anchor"])
            except MEM.MemoryValidationError:
                pass    # not a narration unit key: keep what the reviewer wrote
        row.recommended_repair = f.get("recommended_repair")
        if existing is None:
            row.status = "open"
            db.add(row)
        rows.append(row)
    db.commit()
    return rows


async def apply_repair(db: Session, finding: DetectiveFinding, *, language: str,
                       text_version: int) -> dict:
    """Dispatch one finding to its `repair_owner`. Returns a result dict; never raises on a known
    owner (an interrupted/failed underlying call is caught and reported, leaving the finding's
    status untouched so the next call is a plain retry, not a double-charge or a lost finding)."""
    graph = db.get(RevealGraph, finding.graph_id)
    finding.status = "repair_in_progress"
    db.commit()
    try:
        if finding.repair_owner == "detective_director":
            scene = INSP.plan_summary(db, graph)
            previous = next((s for s in scene["scenes"] if s["scene_key"] == finding.scene_key), {})
            result = await DIR.repair(db, graph.id, previous, [finding.observed_problem],
                                      language, text_version)
            outcome = {"owner": "detective_director", "agent_run_id": result["agent_run_id"]}
        elif finding.repair_owner == "visual_design_director":
            from app.documentary.detective import design_review as DR
            from app.documentary.detective.dispatch import resolve_dispatchable_actions

            resolved = {a["action_key"]: a for a in resolve_dispatchable_actions(db, graph.id)}
            action = resolved.get(finding.action_key)
            if action is None or action.get("skip"):
                outcome = {"owner": "visual_design_director", "skipped": "action_not_dispatchable"}
            else:
                raw = await DR.review(db, graph.case_id, tool_kind=action["tool_id"],
                                      data=action["data"], language=language,
                                      detective_intent={"purpose": action.get("purpose", "")},
                                      scene_id=finding.scene_key or "unknown",
                                      visual_id=finding.action_key, text_version=text_version,
                                      mode="repair")
                outcome = {"owner": "visual_design_director", "design_outcome": raw.get("outcome")}
        elif finding.repair_owner == "scheduler_renderer":
            from app.documentary.detective.dispatch import resolve_dispatchable_actions

            resolve_dispatchable_actions(db, graph.id)  # re-resolves with current F timing
            outcome = {"owner": "scheduler_renderer", "rescheduled": True}
        else:  # narration_workflow: no automated repair exists; a human must rewrite the text
            outcome = {"owner": "narration_workflow",
                      "note": "text_revision_request persisted; requires the separate narration "
                              "workflow, not auto-repairable here"}
        finding.repair_result_json = json.dumps(outcome, ensure_ascii=False)
        db.commit()
        return outcome
    except Exception as exc:  # noqa: BLE001 — an interrupted call must leave a resumable finding
        finding.status = "repair_in_progress"  # unchanged from above; explicit for clarity on resume
        db.commit()
        return {"owner": finding.repair_owner, "error": str(exc), "interrupted": True}


async def recheck_finding(db: Session, finding: DetectiveFinding, *, language: str,
                          text_version: int) -> str:
    """Re-review the finding's scene after a repair. Closes the finding only if a fresh
    `repair_check` no longer reports it; any NEW finding from that recheck is persisted as its own
    row via `persist_findings` (never silently merged into this one — a regression is a new, open
    problem, not a reason to mark the old one fixed)."""
    graph = db.get(RevealGraph, finding.graph_id)
    raw = await review(db, graph.id, mode="repair_check", language=language,
                       text_version=text_version,
                       review_scope=[finding.scene_key] if finding.scene_key else None,
                       previous_findings=[{"id": finding.finding_key}])
    still_open = any(f["id"] == finding.finding_key for f in raw.get("findings") or [])
    persist_findings(db, graph.id, raw)  # regressions/still-open get their own fresh row state
    finding.status = "open" if still_open else "closed"
    db.commit()
    return finding.status


async def repair_and_recheck_until(db: Session, finding: DetectiveFinding, *, language: str,
                                   text_version: int, max_attempts: int = 3) -> str:
    """H4: bounded repair/recheck cycle. Stops at `max_attempts` with status 'incomplete' —
    never silently reports success when the finding is still open, and never loops unbounded."""
    for _ in range(max_attempts):
        result = await apply_repair(db, finding, language=language, text_version=text_version)
        if result.get("interrupted"):
            return finding.status  # left 'repair_in_progress': the next call is a plain resume
        status = await recheck_finding(db, finding, language=language, text_version=text_version)
        if status == "closed":
            return status
    finding.status = "incomplete"
    db.commit()
    return finding.status
