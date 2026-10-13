from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.ai_config import ShortFormPlatformConfig
from app.core.files import serve as serve_file
from app.db.base import get_db
from app.db.models import (
    AppSetting,
    DocumentaryJob,
    EditLog,
    EditorialBlueprint,
    RenderedShort,
    ShortCandidate,
    ShortFormScript,
    Video,
)
from app.documentary import storage
from app.shortform import pipeline
from app.shortform.director import ShortFormDirectorAgent, candidate_table
from app.shortform.metrics import concept_type_comparison

router = APIRouter(tags=["short-form"])


class ShortFormSettingsPatch(BaseModel):
    distribution: dict[str, ShortFormPlatformConfig]
    language_mode: str = "episode"
    shorts_enabled: bool | None = None


@router.get("/api/short-form/settings")
def short_form_settings():
    from app.core.ai_config import ai_config

    return ai_config.short_form.model_dump(mode="json") | {
        "candidate_count": ai_config.short_form.candidate_count()
    }


@router.get("/api/short-form/metrics")
def short_form_metrics(case_id: int | None = None, db: Session = Depends(get_db)):
    if case_id is None:
        return {"case_id": None, "comparison_basis": "concept_type", "metric_rows": 0,
                "comparisons": [], "attribution_status": "unavailable"}
    return concept_type_comparison(db, case_id)


@router.patch("/api/short-form/settings")
def update_short_form_settings(payload: ShortFormSettingsPatch):
    from app.core.ai_config import ai_config

    cfg = ai_config.short_form.model_copy(update={
        "distribution": payload.distribution,
    })
    # Pydantic validates the copied model only when explicitly revalidated.
    cfg = type(ai_config.short_form).model_validate(cfg.model_dump())
    return cfg.model_dump(mode="json") | {
        "candidate_count": cfg.candidate_count(),
        "language_mode": payload.language_mode,
        "level": "episode_override",
    }


class ShortFormAction(BaseModel):
    action: str
    prompt: str | None = None
    edit: dict = {}


def _candidate_hash(row: ShortCandidate) -> str:
    return hashlib.sha256(json.dumps({
        "id": row.id, "status": row.status, "approval_state": row.approval_state,
        "hook": row.hook, "cta": row.cta, "content_hash": row.content_hash,
    }, sort_keys=True).encode()).hexdigest()


def _render_payload(db: Session, row: RenderedShort) -> dict:
    return {
        "id": row.id, "candidate_id": row.candidate_id, "language": row.language,
        "status": row.status, "approval_state": row.approval_state,
        "caption_text": row.caption_text, "cta": row.cta,
        "video_url": f"/api/short-form/files/{row.id}/video" if row.video_path else None,
        "cover_url": f"/api/short-form/files/{row.id}/cover" if row.cover_path else None,
        "captions_url": f"/api/short-form/files/{row.id}/captions" if row.captions_path else None,
        "manifest_url": f"/api/short-form/files/{row.id}/manifest" if row.manifest_path else None,
        "content_hash": row.content_hash, "gate_results": json.loads(row.gate_results_json or "{}"),
        "error": row.error, "film_job_id": row.film_job_id,
    }


def _candidate_payload(row: ShortCandidate) -> dict:
    return {
        "id": row.id, "stable_key": row.stable_key, "concept_type": row.concept_type,
        "source_beat_ids": json.loads(row.source_beat_ids_json or "[]"),
        "source_chapter_ids": json.loads(row.source_chapter_ids_json or "[]"),
        "source_start_seconds": row.source_start_seconds, "source_end_seconds": row.source_end_seconds,
        "hook": row.hook, "cta": row.cta, "status": row.status,
        "approval_state": row.approval_state, "rejection_reason": row.rejection_reason,
        "gate_results": json.loads(row.gate_results_json or "{}"),
        "selection": json.loads(row.selection_json or "{}"), "content_hash": row.content_hash,
    }


@router.get("/api/short-form/workspace")
def short_form_workspace(case_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(DocumentaryJob).filter(DocumentaryJob.status.in_(("completed", "completed_warnings")))
    if case_id is not None:
        query = query.filter(DocumentaryJob.case_id == case_id)
    job = query.order_by(DocumentaryJob.id.desc()).first()
    from app.core.ai_config import ai_config
    base = {"defaults": ai_config.short_form.production.model_dump(mode="json"),
            "shorts_enabled": True}
    if job is None:
        return {**base, "case": None, "candidates": [], "renders": [], "languages": []}
    setting = db.get(AppSetting, f"shorts:enabled:{job.case_id}")
    enabled = True if setting is None else bool(json.loads(setting.value_json or "{}").get("enabled", True))
    candidates = db.query(ShortCandidate).filter(ShortCandidate.film_job_id == job.id).order_by(ShortCandidate.id).all()
    renders = db.query(RenderedShort).filter(RenderedShort.film_job_id == job.id).order_by(RenderedShort.language, RenderedShort.id).all()
    films = db.query(Video).filter(Video.job_id == job.id, Video.state == "rendered").order_by(Video.language).all()
    case = db.get(__import__("app.db.models", fromlist=["Case"]).Case, job.case_id)
    return {**base, "shorts_enabled": enabled,
            "case": {"id": job.case_id, "title": case.canonical_title if case else "", "job_id": job.id,
                     "film_status": job.status},
            "languages": [film.language for film in films],
            "candidates": [_candidate_payload(row) for row in candidates],
            "renders": [_render_payload(db, row) for row in renders]}


@router.get("/api/short-form/{case_id}/workspace")
def short_form_case_workspace(case_id: int, db: Session = Depends(get_db)):
    return short_form_workspace(case_id, db)


@router.patch("/api/short-form/{case_id}/settings")
def update_case_short_form_settings(case_id: int, payload: ShortFormSettingsPatch, db: Session = Depends(get_db)):
    if payload.shorts_enabled is not None:
        pipeline.set_enabled(db, case_id, payload.shorts_enabled)
    return {"case_id": case_id, "shorts_enabled": payload.shorts_enabled if payload.shorts_enabled is not None else pipeline._enabled(db, case_id)}


@router.post("/api/short-form/{case_id}/candidates/{candidate_id}/preview")
def preview_candidate_action(case_id: int, candidate_id: int, payload: ShortFormAction, db: Session = Depends(get_db)):
    row = db.get(ShortCandidate, candidate_id)
    if row is None or row.case_id != case_id:
        raise HTTPException(status_code=404, detail="Short candidate not found")
    if payload.action not in {"approve", "edit", "regenerate", "reject"}:
        raise HTTPException(status_code=400, detail="Unknown candidate action")
    preview = {"case_id": case_id, "action": payload.action, "target_type": "candidate",
               "target_id": row.id, "edit": payload.edit, "prompt": payload.prompt,
               "scope": [{"id": row.id, "type": "short_candidate", "language": None, "will_change": True}],
               "bound_hashes": {str(row.id): _candidate_hash(row)}}
    edit = EditLog(actor="api", scope_type="short_form", scope_id=str(row.id),
                   instruction_text=payload.prompt, intent_json=json.dumps({"action": payload.action}),
                   scope_preview_json=json.dumps(preview, ensure_ascii=False, sort_keys=True), status="previewed")
    db.add(edit)
    db.flush()
    preview["preview_id"] = edit.id
    edit.scope_preview_json = json.dumps(preview, ensure_ascii=False, sort_keys=True)
    db.commit()
    return preview


@router.post("/api/short-form/{case_id}/renders/{render_id}/preview")
def preview_render_action(case_id: int, render_id: int, payload: ShortFormAction, db: Session = Depends(get_db)):
    row = db.get(RenderedShort, render_id)
    if row is None or row.case_id != case_id:
        raise HTTPException(status_code=404, detail="Rendered short not found")
    if payload.action not in {"approve", "reject", "edit"}:
        raise HTTPException(status_code=400, detail="Unknown render action")
    preview = {"case_id": case_id, "action": payload.action, "target_type": "render",
               "target_id": row.id, "edit": payload.edit, "prompt": payload.prompt,
               "scope": [{"id": row.id, "type": "rendered_short", "language": row.language, "will_change": True}],
               "bound_hashes": {str(row.id): row.content_hash}}
    edit = EditLog(actor="api", scope_type="short_form", scope_id=str(row.id),
                   instruction_text=payload.prompt, intent_json=json.dumps({"action": payload.action}),
                   scope_preview_json=json.dumps(preview, ensure_ascii=False, sort_keys=True), status="previewed")
    db.add(edit)
    db.flush()
    preview["preview_id"] = edit.id
    edit.scope_preview_json = json.dumps(preview, ensure_ascii=False, sort_keys=True)
    db.commit()
    return preview


@router.post("/api/short-form/{case_id}/operations/{preview_id}/apply")
async def apply_short_form_action(case_id: int, preview_id: int, db: Session = Depends(get_db)):
    edit = db.get(EditLog, preview_id)
    if edit is None or edit.status != "previewed" or edit.scope_type != "short_form":
        raise HTTPException(status_code=404, detail="Short-form preview not found")
    preview = json.loads(edit.scope_preview_json or "{}")
    if int(preview.get("case_id", -1)) != case_id:
        raise HTTPException(status_code=409, detail="Preview does not belong to case")
    target_id = int(preview["target_id"])
    action = preview["action"]
    if preview["target_type"] == "candidate":
        row = db.get(ShortCandidate, target_id)
        if row is None or _candidate_hash(row) != preview["bound_hashes"].get(str(target_id)):
            raise HTTPException(status_code=409, detail="Inputs changed since preview")
        if action == "approve":
            row.approval_state = "approved"
        elif action == "reject":
            row.approval_state, row.status = "rejected", "rejected"
        elif action == "edit":
            for field in ("hook", "cta"):
                if isinstance(preview.get("edit", {}).get(field), str):
                    setattr(row, field, preview["edit"][field])
            row.content_hash = _candidate_hash(row)
        elif action == "regenerate":
            row.status, row.approval_state = "selected", "pending"
        db.commit()
        if action == "regenerate":
            for render in db.query(RenderedShort).filter(RenderedShort.candidate_id == row.id).all():
                pipeline.run_one(db, render.id, force=True)
    else:
        row = db.get(RenderedShort, target_id)
        if row is None or row.content_hash != preview["bound_hashes"].get(str(target_id)):
            raise HTTPException(status_code=409, detail="Inputs changed since preview")
        if action == "approve":
            row.approval_state = "approved"
        elif action == "reject":
            row.approval_state = "rejected"
        elif action == "edit":
            script = db.get(ShortFormScript, row.script_id)
            if script is None:
                raise HTTPException(status_code=409, detail="Short script is missing")
            edit_values = preview.get("edit") or {}
            if isinstance(edit_values.get("caption"), str):
                script.caption_text = edit_values["caption"]
            if isinstance(edit_values.get("cta"), str):
                script.cta = edit_values["cta"]
            cues = json.loads(script.captions_json or "[]")
            if isinstance(edit_values.get("caption"), str) and cues:
                cues[0]["text"] = edit_values["caption"][:42]
            if isinstance(edit_values.get("cta"), str) and cues:
                cues[-1]["text"] = edit_values["cta"][:42]
            script.captions_json = json.dumps(cues, ensure_ascii=False)
            script.content_hash = hashlib.sha256(json.dumps({
                "hook": script.hook, "body": script.body, "cta": script.cta,
                "captions": script.captions_json, "language": script.language,
            }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            db.commit()
            pipeline.run_one(db, row.id, force=True)
        db.commit()
    edit.status = "completed"
    edit.result_json = json.dumps({"status": "completed", "target_id": target_id, "changed_outside_scope": []})
    db.commit()
    return {"preview_id": preview_id, "status": "completed", "target_id": target_id}


@router.get("/api/short-form/{case_id}/operations/{preview_id}")
def short_form_operation(case_id: int, preview_id: int, db: Session = Depends(get_db)):
    edit = db.get(EditLog, preview_id)
    if edit is None:
        raise HTTPException(status_code=404, detail="Preview not found")
    payload = json.loads(edit.scope_preview_json or "{}")
    if int(payload.get("case_id", -1)) != case_id:
        raise HTTPException(status_code=404, detail="Preview not found")
    return {"preview_id": preview_id, "status": edit.status, "preview": payload,
            "result": json.loads(edit.result_json or "{}")}


@router.get("/api/short-form/files/{render_id}/{kind}")
def short_form_file(render_id: int, kind: str, db: Session = Depends(get_db)):
    row = db.get(RenderedShort, render_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Rendered short not found")
    path = {"video": row.video_path, "cover": row.cover_path, "captions": row.captions_path,
            "manifest": row.manifest_path}.get(kind)
    media = {"video": "video/mp4", "cover": "image/png", "captions": "text/plain",
             "manifest": "application/json"}.get(kind)
    if path is None or media is None:
        raise HTTPException(status_code=404, detail="File not found")
    return serve_file(storage.resolve(path), media)


@router.get("/api/short-form/blueprints/{blueprint_id}/candidates")
def short_form_candidates(blueprint_id: int, db: Session = Depends(get_db)):
    row = db.get(EditorialBlueprint, blueprint_id)
    if not row:
        raise HTTPException(status_code=404, detail="Blueprint not found")
    try:
        candidates = ShortFormDirectorAgent().generate_for_blueprint_row(db, row)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "blueprint_id": blueprint_id,
        "count": len(candidates),
        "candidates": candidates,
        "table": candidate_table(candidates),
    }
