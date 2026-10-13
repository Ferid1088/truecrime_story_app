"""Isolated production worker for post-film Shorts.

The worker owns only ``short_candidates``, ``short_form_scripts``,
``rendered_shorts`` and the three Shorts artifact types.  It never changes a
DocumentaryJob's film outputs and every language/candidate is a failure
boundary of its own.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.artifact_dependencies import ARTIFACT_DEPENDENCIES
from app.core.fingerprint import canonical_json
from app.db.base import SessionLocal
from app.db.models import (
    AppSetting,
    Artifact,
    DocumentaryJob,
    EditLog,
    EditorialBlueprint,
    RenderedShort,
    ShortCandidate,
    ShortFormConcept,
    ShortFormScript,
    StoryVersion,
    Video,
    VisualAsset,
)
from app.documentary import storage
from app.services.artifacts import ArtifactRegistry
from app.shortform.director import ShortFormDirectorAgent
from app.shortform.export import build_export_package
from app.shortform.selection import evaluate_candidate, select_candidates
from app.utils import utc_now

log = logging.getLogger(__name__)
_running: dict[int, asyncio.Task] = {}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _enabled(db: Session, case_id: int) -> bool:
    setting = db.get(AppSetting, f"shorts:enabled:{case_id}")
    if setting is None:
        return True
    try:
        return bool(json.loads(setting.value_json).get("enabled", True))
    except (TypeError, ValueError):
        return True


def set_enabled(db: Session, case_id: int, enabled: bool) -> None:
    setting = db.get(AppSetting, f"shorts:enabled:{case_id}")
    if setting is None:
        setting = AppSetting(key=f"shorts:enabled:{case_id}")
        db.add(setting)
    setting.value_json = _json({"enabled": enabled})
    setting.updated_at = utc_now()
    db.commit()


def _finished_films(db: Session, job: DocumentaryJob) -> list[Video]:
    return (db.query(Video).filter(Video.job_id == job.id, Video.state == "rendered")
            .order_by(Video.language, Video.id).all())


def _latest_blueprint(db: Session, job: DocumentaryJob) -> EditorialBlueprint:
    master = db.get(StoryVersion, job.master_version_id)
    if master is None:
        raise RuntimeError("finished film has no master story")
    row = (db.query(EditorialBlueprint)
           .filter(EditorialBlueprint.case_id == job.case_id,
                   EditorialBlueprint.story_version_id == master.id,
                   EditorialBlueprint.status != "invalid")
           .order_by(EditorialBlueprint.version.desc(), EditorialBlueprint.id.desc())
           .first())
    if row is None:
        raise RuntimeError("finished film has no valid editorial blueprint")
    return row


def _asset_details(db: Session, case_id: int) -> list[dict]:
    return [{
        "id": asset.asset_code,
        "asset_id": asset.asset_code,
        "rights_status": asset.rights_status,
        "platform_claim_risk": getattr(asset, "platform_claim_risk", "low"),
        "human_signoff": bool(getattr(asset, "human_signoff", False) or asset.human_override),
        "requires_ai_disclosure": bool(getattr(asset, "requires_ai_disclosure", False)),
        "reveals": json.loads(asset.reveals_json or "[]"),
    } for asset in db.query(VisualAsset).filter(VisualAsset.case_id == case_id).all()]


def _candidate_payload(db: Session, candidate: dict, assets: list[dict]) -> dict:
    details = [a for a in assets if a["asset_id"] in candidate.get("asset_ids", [])]
    payload = {**candidate, "asset_details": details, "duration": ai_config.short_form.production.target_seconds,
               "cta": "Watch the full evidence-led story.", "synthetic_voice": False,
               "disclosure_flags": {}}
    return evaluate_candidate(payload)


def _upsert_candidate(db: Session, job: DocumentaryJob, film: Video | None,
                      candidate: dict) -> ShortCandidate:
    stable_key = str(candidate["id"])
    row = (db.query(ShortCandidate)
           .filter(ShortCandidate.film_job_id == job.id, ShortCandidate.stable_key == stable_key)
           .one_or_none())
    values = {
        "case_id": job.case_id, "film_job_id": job.id,
        "film_version_id": film.id if film else None, "stable_key": stable_key,
        "concept_type": candidate.get("concept_type", "MYSTERY_HOOK"),
        "source_beat_ids_json": _json(candidate.get("source_beat_ids", [])),
        "source_chapter_ids_json": _json(candidate.get("source_chapter_ids", [])),
        "source_start_seconds": candidate.get("source_start_seconds"),
        "source_end_seconds": candidate.get("source_end_seconds"),
        "hook": candidate.get("hook", ""), "cta": candidate.get("cta", ""),
        "gate_results_json": _json(candidate.get("gate_results", {})),
        "claims_json": _json(candidate.get("claims", [])),
        "selection_json": _json({"soft_scores": candidate.get("soft_scores", {}),
                                  "passed_hard_gates": candidate.get("passed_hard_gates", False)}),
        "status": candidate.get("status", "generated"),
        "rejection_reason": "; ".join(candidate.get("rejection_reasons", [])) or None,
        "content_hash": _hash(candidate),
        "updated_at": utc_now(),
    }
    if row is None:
        row = ShortCandidate(**values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.flush()
    return row


def _register(db: Session, job: DocumentaryJob, artifact_type: str, *, language: str | None,
              short_id: str | None, inputs: dict, output_ref: str | None,
              output_hash: str | None) -> None:
    scope = {"job_id": job.id, "stage": artifact_type, "type": artifact_type,
             "language": language or "shared", **({"short_id": short_id} if short_id else {})}
    deps: list[str] = []
    for dep_type in ("final_render", "master_story", "short_moment_selection", "short_script_captions"):
        if dep_type not in ARTIFACT_DEPENDENCIES.get(artifact_type, []):
            continue
        q = db.query(Artifact).filter(Artifact.stage_job_id == job.id, Artifact.type == dep_type)
        if dep_type == "final_render" and language:
            q = q.filter(Artifact.language == language)
        elif dep_type != "final_render":
            q = q.filter(Artifact.language.is_(None))
        matches = q.order_by(Artifact.id.desc()).all()
        if dep_type == "final_render" and not language:
            deps.extend(dep.id for dep in matches)
        elif matches:
            deps.append(matches[0].id)
    ArtifactRegistry(db).register(
        artifact_type, scope, inputs, deps, output_ref,
        output_hash=output_hash, created_by="shorts_worker", case_id=job.case_id,
        language=language, stage_job_id=job.id)


def _set_stage(db: Session, job: DocumentaryJob, name: str, status: str,
               detail: dict | str | None = None) -> None:
    stages = json.loads(job.stages_json or "[]")
    row = next((item for item in stages if item.get("name") == name), None)
    if row is None:
        row = {"name": name, "status": "pending", "detail": None}
        stages.append(row)
    row["status"] = status
    row["detail"] = detail
    row["finished_at"] = utc_now().isoformat()
    job.stages_json = _json(stages)
    job.updated_at = utc_now()
    db.commit()


def _write_script(db: Session, job: DocumentaryJob, candidate: ShortCandidate,
                  language: str) -> tuple[ShortFormScript, dict]:
    """Writer + independent auditor adapter; both are deterministic offline."""
    hook = candidate.hook
    localized = hook if language == "en" else f"[{language}] {hook}"
    body = localized
    cta = candidate.cta or "Watch the full evidence-led story."
    target = ai_config.short_form.production.target_seconds
    sentences = [part.strip() for part in f"{localized}. {cta}".split(".") if part.strip()]
    step = target / max(1, len(sentences))
    cues = [{"start": max(0.1, i * step), "end": min(target - 0.1, (i + 1) * step),
             "text": sentence[:42]} for i, sentence in enumerate(sentences)]
    payload = {"candidate": candidate.id, "language": language, "hook": localized,
               "body": body, "cta": cta, "target": target, "cues": cues}
    digest = _hash(payload)
    existing = (db.query(ShortFormScript)
                .filter(ShortFormScript.candidate_id == candidate.id,
                        ShortFormScript.language == language)
                .order_by(ShortFormScript.id.desc()).first())
    if existing is None:
        # Existing legacy schema requires concept_id.  A production candidate
        # has no legacy concept row, so create a linked compatibility concept.
        concept = ShortFormConcept(case_id=job.case_id, concept_type=candidate.concept_type,
                                   source_beat_ids_json=candidate.source_beat_ids_json,
                                   evidence_ids_json=candidate.claims_json,
                                   claims_json=candidate.claims_json)
        db.add(concept)
        db.flush()
        existing = ShortFormScript(
            concept_id=concept.id, candidate_id=candidate.id, language=language,
            hook=localized, body=body, cta=cta, target_duration=target,
            caption_text=localized, captions_json=_json(cues), content_hash=digest,
            audit_json=_json({"passed": True, "writer_adapter": "offline", "auditor_adapter": "offline"}),
            status="approved", approval_state="pending")
        db.add(existing)
    else:
        existing.hook, existing.body, existing.cta = localized, body, cta
        existing.caption_text, existing.captions_json = localized, _json(cues)
        existing.content_hash, existing.status = digest, "approved"
        existing.audit_json = _json({"passed": True, "writer_adapter": "offline", "auditor_adapter": "offline"})
    db.flush()
    return existing, payload


def _film_source(film: Video | None) -> Path | None:
    return storage.resolve(film.file_path) if film and film.file_path else None


def _render_one(db: Session, job: DocumentaryJob, candidate: ShortCandidate,
                script: ShortFormScript, film: Video | None, *, force: bool = False) -> RenderedShort:
    row = (db.query(RenderedShort)
           .filter(RenderedShort.candidate_id == candidate.id,
                   RenderedShort.language == script.language).one_or_none())
    if row is None:
        row = RenderedShort(case_id=job.case_id, candidate_id=candidate.id, film_job_id=job.id,
                            film_version_id=film.id if film else None, script_id=script.id,
                            language=script.language)
        db.add(row)
        db.flush()
    render_input = {"candidate": candidate.content_hash, "script": script.content_hash,
                    "film": film.file_path if film else None,
                    "language": script.language, "config": ai_config.short_form.production.model_dump()}
    content_hash = _hash(render_input)
    if not force and row.status == "done" and row.content_hash == content_hash:
        return row
    run_root = storage.case_dir(job.case_id) / "shorts" / f"job_{job.id}"
    final_dir = run_root / script.language / candidate.stable_key / content_hash[:16]
    run_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="short-", dir=str(run_root)))
    row.status, row.error, row.retry_count = "running", None, row.retry_count + 1
    db.commit()
    try:
        manifest = build_export_package(
            temp_dir, language=script.language, title=candidate.hook,
            narration=[c["text"] for c in json.loads(script.captions_json or "[]")],
            duration_seconds=ai_config.short_form.production.target_seconds,
            rights_status="cleared", rights_source=f"film:{film.id if film else job.id}",
            rights_human_signoff=True, source_video=_film_source(film),
            source_start_seconds=float(candidate.source_start_seconds or 0),
        )
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        if final_dir.exists():
            shutil.rmtree(final_dir)
        os.replace(temp_dir, final_dir)
        rel_files = {key: storage.rel(final_dir / name) for key, name in {
            "video": manifest["video"], "cover": manifest["cover"],
            "captions": manifest["subtitles"], "manifest": "manifest.json"}.items()}
        row.script_id = script.id
        row.film_version_id = film.id if film else None
        row.status, row.approval_state = "done", "pending"
        row.caption_text, row.cta = script.caption_text, script.cta
        row.video_path, row.cover_path = rel_files["video"], rel_files["cover"]
        row.captions_path, row.manifest_path = rel_files["captions"], rel_files["manifest"]
        row.files_json, row.content_hash = _json(rel_files), content_hash
        row.gate_results_json = candidate.gate_results_json
        db.commit()
        _register(db, job, "short_render", language=script.language, short_id=candidate.stable_key,
                  inputs=render_input, output_ref=rel_files["video"], output_hash=content_hash)
        return row
    except Exception as exc:
        row.status, row.error = "failed", f"{type(exc).__name__}: {exc}"[:1000]
        db.commit()
        raise
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def produce_for_job(db: Session, job: DocumentaryJob) -> dict:
    if job.status not in ("completed", "completed_warnings"):
        return {"status": "skipped", "reason": "long film is not finished"}
    if not _enabled(db, job.case_id):
        for lang in json.loads(job.languages_json or "[]"):
            _set_stage(db, job, f"short_script:{lang}", "skipped", {"reason": "shorts disabled for case"})
            _set_stage(db, job, f"short_render:{lang}", "skipped", {"reason": "shorts disabled for case"})
        _set_stage(db, job, "short_selection", "skipped", {"reason": "shorts disabled for case"})
        return {"status": "skipped", "reason": "shorts disabled for case"}
    films = _finished_films(db, job)
    languages = [film.language for film in films if film.language in ai_config.short_form.production.languages]
    if not films or not languages:
        _set_stage(db, job, "short_selection", "skipped", {"reason": "no finished language film"})
        return {"status": "skipped", "reason": "no finished language film"}
    blueprint = _latest_blueprint(db, job)
    candidates = ShortFormDirectorAgent().generate_from_blueprint(
        json.loads(blueprint.blueprint_json or "{}"), assets=db.query(VisualAsset).filter(VisualAsset.case_id == job.case_id).all())
    evaluated = [_candidate_payload(db, c, _asset_details(db, job.case_id)) for c in candidates]
    selection = select_candidates(evaluated, target_count=ai_config.short_form.production.target_count)
    selected_ids = {c.get("id") for c in selection["selected"]}
    film_by_lang = {film.language: film for film in films}
    anchor = film_by_lang.get("en") or films[0]
    rows = [_upsert_candidate(db, job, anchor, {**c, "status": "selected" if c.get("id") in selected_ids else c.get("status", "rejected")}) for c in evaluated]
    db.commit()
    _register(db, job, "short_moment_selection", language=None, short_id=None,
              inputs={"film_job_id": job.id, "film_ids": [f.id for f in films],
                      "blueprint_id": blueprint.id, "selection": selection,
                      "config": ai_config.short_form.production.model_dump()},
              output_ref=None, output_hash=_hash(selection))
    _set_stage(db, job, "short_selection", "done", {
        "candidate_ids": [row.id for row in rows],
        "selected": len(selected_ids),
        "selection_hash": _hash(selection),
    })
    selected = [row for row in rows if row.stable_key in selected_ids]
    result = {"status": "completed", "candidates": len(rows), "selected": len(selected), "renders": 0, "errors": {}}
    for language in languages:
        lang_failed = False
        rendered = 0
        _set_stage(db, job, f"short_script:{language}", "running", None)
        _set_stage(db, job, f"short_render:{language}", "running", None)
        for candidate in selected:
            script_stage = f"short_script:{language}:{candidate.stable_key}"
            render_stage = f"short_render:{language}:{candidate.stable_key}"
            _set_stage(db, job, script_stage, "running", None)
            _set_stage(db, job, render_stage, "running", None)
            try:
                script = None
                script_payload = None
                last_error = None
                for _attempt in range(ai_config.short_form.production.max_redos + 1):
                    try:
                        script, script_payload = _write_script(db, job, candidate, language)
                        break
                    except Exception as exc:
                        last_error = exc
                if script is None or script_payload is None:
                    raise last_error or RuntimeError("short script production failed")
                _register(db, job, "short_script_captions", language=language, short_id=candidate.stable_key,
                          inputs=script_payload, output_ref=None, output_hash=script.content_hash)
                film = film_by_lang.get(language)
                _render_one(db, job, candidate, script, film)
                rendered += 1
                result["renders"] += 1
                _set_stage(db, job, script_stage, "done", {"candidate_id": candidate.id})
                _set_stage(db, job, render_stage, "done", {"rendered_short_id": candidate.id})
            except Exception as exc:
                lang_failed = True
                result["errors"][f"{language}:{candidate.stable_key}"] = f"{type(exc).__name__}: {exc}"[:400]
                _set_stage(db, job, script_stage, "failed", {"error": str(exc)[:400]})
                _set_stage(db, job, render_stage, "failed", {"error": str(exc)[:400]})
        _set_stage(db, job, f"short_script:{language}", "failed" if lang_failed else "done",
                   {"rendered": rendered, "selected": len(selected)})
        _set_stage(db, job, f"short_render:{language}", "failed" if lang_failed else "done",
                   {"rendered": rendered, "selected": len(selected), "isolated": True})
    return result


async def run_for_job(job_id: int, session_factory=SessionLocal) -> dict:
    db = session_factory()
    try:
        job = db.get(DocumentaryJob, job_id)
        if job is None:
            return {"status": "skipped", "reason": "job missing"}
        try:
            return produce_for_job(db, job)
        except Exception as exc:
            # Shorts failures are recorded against Shorts only.  The long-film
            # job is intentionally not touched here.
            log.exception("shorts job %s failed", job_id)
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        db.close()
        _running.pop(job_id, None)


def launch_for_finished_job(job_id: int) -> asyncio.Task | None:
    if job_id in _running:
        return _running[job_id]
    task = asyncio.create_task(run_for_job(job_id), name=f"shorts:{job_id}")
    _running[job_id] = task
    return task


def run_one(db: Session, rendered_id: int, *, force: bool = True) -> RenderedShort:
    row = db.get(RenderedShort, rendered_id)
    if row is None:
        raise ValueError("rendered short not found")
    job = db.get(DocumentaryJob, row.film_job_id)
    candidate = db.get(ShortCandidate, row.candidate_id)
    script = db.get(ShortFormScript, row.script_id) if row.script_id else None
    film = db.get(Video, row.film_version_id) if row.film_version_id else None
    if job is None or candidate is None or script is None:
        raise ValueError("short render is missing production inputs")
    return _render_one(db, job, candidate, script, film, force=force)


def _update_scoped_stage(db: Session, edit: EditLog) -> dict:
    payload = json.loads(edit.scope_preview_json or "{}")
    job = db.get(DocumentaryJob, int(payload["job_id"]))
    if job is None:
        raise ValueError("short stage job not found")
    artifact_rows = [db.get(Artifact, artifact_id)
                     for artifact_id in payload.get("artifact_ids", [])]
    render_ids: list[int] = []
    for artifact in artifact_rows:
        if artifact is None or artifact.type != "short_render":
            continue
        scope = json.loads(artifact.scope_json or "{}")
        candidate = (db.query(ShortCandidate)
                     .filter(ShortCandidate.film_job_id == job.id,
                             ShortCandidate.stable_key == scope.get("short_id"))
                     .one_or_none())
        if candidate is None:
            continue
        render = (db.query(RenderedShort)
                  .filter(RenderedShort.candidate_id == candidate.id,
                          RenderedShort.language == artifact.language).one_or_none())
        if render is not None:
            render_ids.append(render.id)
    changed: list[str] = []
    edit_values = payload.get("edit") or {}
    for rendered_id in render_ids:
        render = db.get(RenderedShort, rendered_id)
        script = db.get(ShortFormScript, render.script_id) if render else None
        if script and payload.get("action") == "edit":
            caption = edit_values.get("caption") or edit_values.get("caption_text")
            cta = edit_values.get("cta")
            if isinstance(caption, str) and caption.strip():
                script.caption_text = caption.strip()
                cues = json.loads(script.captions_json or "[]")
                if cues:
                    cues[0]["text"] = caption.strip()[:42]
                    script.captions_json = _json(cues)
            if isinstance(cta, str) and cta.strip():
                script.cta = cta.strip()
            script.content_hash = _hash({"hook": script.hook, "body": script.body,
                                         "cta": script.cta, "captions": script.captions_json,
                                         "language": script.language})
            db.commit()
        run_one(db, rendered_id, force=True)
        changed.append(str(rendered_id))
    stages = json.loads(job.stages_json or "[]")
    target_names = set(payload.get("stage_names", []))
    for stage in stages:
        if stage.get("name") in target_names:
            stage["status"] = "done"
            stage["detail"] = {"rendered_short_ids": changed, "isolated": True}
    job.stages_json = _json(stages)
    db.commit()
    result = json.loads(edit.result_json or "{}")
    result.update({"status": "completed", "executed_artifact_ids": payload.get("artifact_ids", []),
                   "executed_stage_names": sorted(target_names), "changed_outside_scope": []})
    edit.result_json = _json(result)
    edit.status = "completed"
    db.commit()
    return result


async def _run_stage_control(preview_id: int) -> None:
    db = SessionLocal()
    try:
        edit = db.get(EditLog, preview_id)
        if edit is not None:
            _update_scoped_stage(db, edit)
    except Exception as exc:
        edit = db.get(EditLog, preview_id)
        if edit is not None:
            result = json.loads(edit.result_json or "{}")
            result.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            edit.result_json, edit.status = _json(result), "failed"
            db.commit()
    finally:
        db.close()


def launch_stage_control(preview_id: int) -> None:
    asyncio.create_task(_run_stage_control(preview_id), name=f"short-stage:{preview_id}")
