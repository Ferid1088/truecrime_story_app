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
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import update
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
    ProductionScript,
    RenderedShort,
    ShortCandidate,
    ShortFormConcept,
    ShortFormScript,
    ShortProductionJob,
    StoryVersion,
    Video,
    VisualAsset,
)
from app.documentary import storage
from app.services.artifacts import ArtifactRegistry
from app.shortform.agents import MockShortsTextAdapter, ProviderShortsTextAdapter, ShortsTextAdapter
from app.shortform.director import ShortFormDirectorAgent
from app.shortform.export import CAPTION_ALIGNMENT, LABEL_ALIGNMENT, build_export_package
from app.shortform.platform_packages import build_platform_packages
from app.shortform.selection import evaluate_candidate, select_candidates
from app.utils import detect_language, language_quality, utc_now

log = logging.getLogger(__name__)
SHORT_QUEUE_LEASE_SECONDS = 120


class ShortsAuditRejected(RuntimeError):
    pass


class ShortsCostCapReached(RuntimeError):
    pass


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


def _short_cost_cap(db: Session, case_id: int) -> float:
    setting = db.get(AppSetting, f"shorts:cost_cap:{case_id}")
    if setting is not None:
        try:
            value = json.loads(setting.value_json or "{}")
            configured = value.get("max_cost_usd", value.get("cost_cap_usd"))
            if configured is not None and float(configured) > 0:
                return float(configured)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return float(ai_config.short_form.production.max_cost_usd_per_case)


def _case_provider_spend(db: Session, case_id: int) -> float:
    from sqlalchemy import func

    total = db.query(func.coalesce(func.sum(ShortProductionJob.provider_cost_usd), 0.0)).filter(
        ShortProductionJob.case_id == case_id
    ).scalar()
    return float(total or 0.0)


def _record_provider_result(db: Session, queue: ShortProductionJob,
                            result: Any) -> None:
    if result is None:
        return
    queue.provider_call_count = int(queue.provider_call_count or 0) + 1
    reported = getattr(result, "cost_usd", None)
    if reported is not None:
        queue.provider_cost_usd = round(
            float(queue.provider_cost_usd or 0.0) + max(0.0, float(reported)), 8
        )
    queue.updated_at = utc_now()
    db.commit()
    cap = queue.cost_cap_usd
    if cap is not None and _case_provider_spend(db, queue.case_id) >= cap:
        raise ShortsCostCapReached(
            f"provider cost cap reached (${_case_provider_spend(db, queue.case_id):.4f} / ${cap:.4f})"
        )


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


def _film_script(db: Session, film: Video | None) -> dict | None:
    row = db.get(ProductionScript, film.production_script_id) if film and film.production_script_id else None
    return json.loads(row.script_json or "{}") if row is not None else None


def film_window(script: dict | None, beat_ids: list[str]) -> tuple[float, float] | None:
    """The stretch of the finished film (its own seconds, host and logo included) a short from these
    beats is cut from: from the first beat's start, target_seconds long, inside the film."""
    if not script:
        return None
    beats = {b["beat_id"]: b for b in script.get("beats") or []}
    spans = [beats[b] for b in beat_ids if b in beats]
    if not spans:
        return None
    target = float(ai_config.short_form.production.target_seconds)
    duration = float(script.get("duration") or 0.0)
    start = min(b["start"] for b in spans)
    end = start + target
    if duration and end > duration:
        end = duration
        start = max(0.0, end - target)
    return round(start, 2), round(end, 2)


def window_cues(script: dict, start: float, end: float, max_chars: int = 42) -> list[dict]:
    """The film's own subtitle cues inside the window, times from the window's start, long lines split
    at word boundaries (the share of a cue's time follows the characters)."""
    out: list[dict] = []
    for cue in script.get("subtitles") or []:
        if cue["end"] <= start or cue["start"] >= end:
            continue
        lines: list[str] = []
        cur: list[str] = []
        for word in str(cue["text"]).split():
            if cur and len(" ".join(cur + [word])) > max_chars:
                lines.append(" ".join(cur))
                cur = []
            cur.append(word)
        if cur:
            lines.append(" ".join(cur))
        total = sum(len(x) for x in lines) or 1
        t = cue["start"]
        for line in lines:
            span = (cue["end"] - cue["start"]) * len(line) / total
            a, b = max(t, start) - start, min(t + span, end) - start
            t += span
            if b - a > 0.2:
                out.append({"start": round(a, 3), "end": round(b, 3), "text": line})
    return out


def _upsert_candidate(db: Session, job: DocumentaryJob, film: Video | None,
                      candidate: dict) -> ShortCandidate:
    script = _film_script(db, film)
    window = film_window(script, candidate.get("source_beat_ids") or [])
    if window and candidate.get("source_start_seconds") is None:
        from app.documentary.visuals.coverage_fill import window_cards

        assert script is not None   # a window comes from the script
        cards, longest = window_cards(script, *window)
        candidate = {**candidate, "source_start_seconds": window[0], "source_end_seconds": window[1]}
        limit = ai_config.coverage.max_card_stretch_seconds
        if longest > limit:
            candidate = {**candidate, "status": "rejected", "rejection_reasons": [
                *candidate.get("rejection_reasons", []),
                f"its stretch of the film shows cards for {longest} s in a row (max {limit})"]}
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


READY_STATUSES = ("ready", "approved", "complete", "completed")


def _spoken_text(db: Session, job: DocumentaryJob, language: str) -> str:
    """Return only the finished film's own spoken text for ``language``: the spoken version
    told from the film's final version, else the language's newest approved story."""
    told = (db.query(StoryVersion)
            .filter(StoryVersion.case_id == job.case_id, StoryVersion.language == language,
                    StoryVersion.kind == "spoken", StoryVersion.master_version_id == job.master_version_id,
                    StoryVersion.status.in_(READY_STATUSES))
            .order_by(StoryVersion.version.desc(), StoryVersion.id.desc()).first())
    if told is not None and (told.story_text or "").strip():
        return told.story_text
    rows = (db.query(StoryVersion)
            .filter(StoryVersion.case_id == job.case_id,
                    StoryVersion.language == language,
                    StoryVersion.status.in_(("approved", "complete", "completed")))
            .order_by(StoryVersion.is_best.desc(), StoryVersion.version.desc(), StoryVersion.id.desc())
            .all())
    if rows:
        return rows[0].story_text or ""
    # The master is the source of the English branch only.  Never use it to
    # silently populate a non-English short.
    if language == "en":
        master = db.get(StoryVersion, job.master_version_id)
        return master.story_text if master and master.status in ("approved", "complete", "completed") else ""
    return ""


def _write_script(db: Session, job: DocumentaryJob, candidate: ShortCandidate,
                  language: str, adapter: ShortsTextAdapter,
                  on_provider_result=None) -> tuple[ShortFormScript, dict]:
    """Call the native-language writer and independent auditor."""
    spoken_text = _spoken_text(db, job, language)
    if not spoken_text.strip():
        raise RuntimeError(f"finished film has no approved spoken text for {language}")
    target = ai_config.short_form.production.target_seconds
    writer_payload = {
        "candidate": candidate.id, "language": language, "hook": candidate.hook,
        "spoken_text": spoken_text, "allowed_reveals": json.loads(candidate.gate_results_json or "{}").get(
            "RevealFirewall", {}).get("metadata", {}).get("allowed_reveals", []),
        "claims": json.loads(candidate.claims_json or "[]"),
        "target_duration": target,
    }
    film = (db.query(Video).filter_by(job_id=job.id, language=language, state="rendered")
            .order_by(Video.id.desc()).first())
    cues = _cues_for(db, film, candidate)
    if cues:       # the words really spoken in the stretch of the film this short is cut from
        writer_payload["window_narration"] = " ".join(c["text"] for c in cues)
    draft, writer_result = adapter.write(db, job.case_id, writer_payload)
    if on_provider_result is not None:
        on_provider_result(writer_result)
    draft = draft if isinstance(draft, dict) else {}
    hook = str(draft.get("hook") or candidate.hook).strip()
    body = str(draft.get("body") or "").strip()
    cta = str(draft.get("cta") or candidate.cta or "Watch the full evidence-led story.").strip()
    captions = draft.get("captions") or []
    if not hook or not body or not isinstance(captions, list):
        raise RuntimeError("short writer returned an incomplete script")
    combined_text = " ".join([hook, body, cta] + [str(c.get("text", "")) for c in captions])
    detected, confidence = detect_language(combined_text, min_chars=30)
    if detected is not None and detected != language and confidence >= 0.6:
        raise RuntimeError(f"short writer returned {detected} text for requested language {language}")
    quality = ai_config.language_quality_for(language)
    if quality is not None and not language_quality(combined_text, language, quality)["pass"]:
        raise RuntimeError(f"short writer returned text outside the {language} language-quality limits")
    max_words = max(30, target * 3)
    if len(body.split()) > max_words:
        raise RuntimeError(f"short writer returned over-length body ({len(body.split())} words; max {max_words})")
    audit, audit_result = adapter.audit(db, job.case_id, {
        **writer_payload, "draft": {"hook": hook, "body": body, "cta": cta, "captions": captions},
    })
    if on_provider_result is not None:
        on_provider_result(audit_result)
    if not bool(audit.get("approved", audit.get("ok", False))):
        reasons = "; ".join(str(reason) for reason in (audit.get("reasons") or ["auditor rejected script"]))
        raise ShortsAuditRejected(reasons[:500])
    payload = {"candidate": candidate.id, "language": language, "hook": hook,
               "body": body, "cta": cta, "target": target, "captions": captions,
               "spoken_text_hash": _hash(spoken_text)}
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
            hook=hook, body=body, cta=cta, target_duration=target,
            caption_text=" ".join(str(c.get("text", "")) for c in captions),
            captions_json=_json(captions), content_hash=digest,
            audit_json=_json({"passed": True, "writer_adapter": type(adapter).__name__,
                              "auditor_adapter": type(adapter).__name__}),
            status="approved", approval_state="pending")
        db.add(existing)
    else:
        existing.hook, existing.body, existing.cta = hook, body, cta
        existing.caption_text = " ".join(str(c.get("text", "")) for c in captions)
        existing.captions_json = _json(captions)
        existing.content_hash, existing.status = digest, "approved"
        existing.audit_json = _json({"passed": True, "writer_adapter": type(adapter).__name__,
                                     "auditor_adapter": type(adapter).__name__})
    db.flush()
    return existing, payload


def _film_source(film: Video | None) -> Path | None:
    return storage.resolve(film.file_path) if film and film.file_path else None


def _cues_for(db: Session, film: Video | None, candidate: ShortCandidate) -> list[dict] | None:
    """The film's real subtitle cues of the candidate's stretch (None: no stretch is known)."""
    script = _film_script(db, film)
    if script is None or candidate.source_start_seconds is None:
        return None
    start = float(candidate.source_start_seconds)
    return window_cues(script, start, start + float(ai_config.short_form.production.target_seconds)) or None


def _labels_for(db: Session, film: Video | None, candidate: ShortCandidate) -> list[dict]:
    """The film's "Illustration" labels inside the candidate's stretch, times from its start."""
    script = _film_script(db, film)
    if script is None or candidate.source_start_seconds is None:
        return []
    start = float(candidate.source_start_seconds)
    end = start + float(ai_config.short_form.production.target_seconds)
    out = []
    for o in script.get("overlays") or []:
        if o.get("kind") == "label" and o["end"] > start and o["start"] < end:
            out.append({"start": round(max(o["start"], start) - start, 3),
                        "end": round(min(o["end"], end) - start, 3), "text": o.get("text") or ""})
    return [x for x in out if x["end"] - x["start"] > 0.3 and x["text"]]


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
                    "caption_text": script.caption_text,
                    "film": film.file_path if film else None,
                    "language": script.language, "config": ai_config.short_form.production.model_dump(),
                    "labels": _labels_for(db, film, candidate),
                    "layout": {"caption_alignment": CAPTION_ALIGNMENT, "label_alignment": LABEL_ALIGNMENT}}
    content_hash = _hash(render_input)
    if not force and row.status == "done" and row.content_hash == content_hash:
        return row
    run_root = storage.case_dir(job.case_id) / "shorts" / f"job_{job.id}"
    final_dir = run_root / script.language / candidate.stable_key / content_hash[:16]
    work_root = final_dir.parent
    work_root.mkdir(parents=True, exist_ok=True)
    for orphan in work_root.glob("short-*"):
        if orphan.is_dir():
            shutil.rmtree(orphan, ignore_errors=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="short-", dir=str(work_root)))
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
            cues=_cues_for(db, film, candidate), labels=_labels_for(db, film, candidate),
        )
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        if final_dir.exists():
            shutil.rmtree(final_dir)
        os.replace(temp_dir, final_dir)
        platforms = build_platform_packages(final_dir, caption=script.caption_text, cta=script.cta,
                                            language=script.language)
        rel_files: dict[str, Any] = {key: storage.rel(final_dir / name) for key, name in {
            "video": manifest["video"], "cover": manifest["cover"],
            "captions": manifest["subtitles"], "manifest": "manifest.json"}.items()}
        row.script_id = script.id
        row.film_version_id = film.id if film else None
        row.status, row.approval_state = "done", "pending"
        row.caption_text, row.cta = script.caption_text, script.cta
        row.video_path, row.cover_path = rel_files["video"], rel_files["cover"]
        row.captions_path, row.manifest_path = rel_files["captions"], rel_files["manifest"]
        rel_files["platforms"] = {p: storage.rel(final_dir / "platforms" / p / "package.json")
                                  for p in platforms}
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


def _queue(db: Session, *, job: DocumentaryJob, stage: str, unique_key: str,
           candidate_id: int | None = None, rendered_short_id: int | None = None,
           language: str | None = None, input_hash: str | None = None) -> ShortProductionJob:
    row = db.query(ShortProductionJob).filter_by(unique_key=unique_key).one_or_none()
    if row is None:
        row = ShortProductionJob(unique_key=unique_key, case_id=job.case_id,
                                 film_job_id=job.id, candidate_id=candidate_id,
                                 rendered_short_id=rendered_short_id, language=language,
                                 stage=stage, input_hash=input_hash)
        db.add(row)
    elif row.status in ("failed", "skipped"):
        row.status, row.error = "queued", None
        row.worker_id, row.lease_until, row.heartbeat_at = None, None, None
    db.flush()
    return row


def enqueue_for_finished_job(db: Session, job_id: int, language: str | None = None) -> ShortProductionJob | None:
    """Persist the post-film selection task; this never runs work in-process."""
    job = db.get(DocumentaryJob, job_id)
    if job is None or job.status not in ("completed", "completed_warnings"):
        return None
    suffix = f":{language}" if language else ""
    row = _queue(db, job=job, stage="selection", unique_key=f"selection:{job.id}{suffix}", language=language)
    db.commit()
    return row


def claim_next_short_job(db: Session, worker_id: str, *, lease_seconds: float = SHORT_QUEUE_LEASE_SECONDS,
                         film_job_id: int | None = None) -> int | None:
    from app.services import case_control

    if case_control.global_stop_active(db):
        return None
    held = case_control.held_case_ids(db)
    now = utc_now()
    query = (db.query(ShortProductionJob.id)
             .filter(ShortProductionJob.status == "queued", ShortProductionJob.worker_id.is_(None)))
    if held:
        query = query.filter(ShortProductionJob.case_id.notin_(held))
    if film_job_id is not None:
        query = query.filter(ShortProductionJob.film_job_id == film_job_id)
    candidates = (query
                  .order_by(ShortProductionJob.id).limit(10).all())
    for (job_id,) in candidates:
        result = db.execute(update(ShortProductionJob).where(
            ShortProductionJob.id == job_id,
            ShortProductionJob.status == "queued",
            ShortProductionJob.worker_id.is_(None),
        ).values(status="running", worker_id=worker_id, attempts=ShortProductionJob.attempts + 1,
                 heartbeat_at=now, lease_until=now + timedelta(seconds=lease_seconds), updated_at=now))
        db.commit()
        if result.rowcount == 1:
            return job_id
    return None


def beat_short_jobs(db: Session, worker_id: str, *, lease_seconds: float = SHORT_QUEUE_LEASE_SECONDS) -> int:
    now = utc_now()
    result = db.execute(update(ShortProductionJob).where(
        ShortProductionJob.worker_id == worker_id, ShortProductionJob.status == "running",
    ).values(heartbeat_at=now, lease_until=now + timedelta(seconds=lease_seconds), updated_at=now))
    db.commit()
    return result.rowcount


def reap_short_jobs(db: Session, *, lease_seconds: float = SHORT_QUEUE_LEASE_SECONDS) -> list[int]:
    cutoff = utc_now() - timedelta(seconds=lease_seconds)
    stale = (db.query(ShortProductionJob)
             .filter(ShortProductionJob.status == "running",
                     ShortProductionJob.heartbeat_at < cutoff).all())
    ids = []
    for row in stale:
        row.status, row.worker_id, row.lease_until, row.heartbeat_at = "queued", None, None, None
        row.error = "worker lease expired; safely requeued"
        row.updated_at = utc_now()
        ids.append(row.id)
    db.commit()
    return ids


def _prepare_for_job(db: Session, job: DocumentaryJob, language: str | None = None) -> dict:
    if job.status not in ("completed", "completed_warnings"):
        return {"status": "skipped", "reason": "long film is not finished"}
    if not _enabled(db, job.case_id):
        for lang in json.loads(job.languages_json or "[]"):
            _set_stage(db, job, f"short_script:{lang}", "skipped", {"reason": "shorts disabled for case"})
            _set_stage(db, job, f"short_render:{lang}", "skipped", {"reason": "shorts disabled for case"})
        _set_stage(db, job, "short_selection", "skipped", {"reason": "shorts disabled for case"})
        return {"status": "skipped", "reason": "shorts disabled for case"}
    films = _finished_films(db, job)
    if language is not None:
        films = [film for film in films if film.language == language]
    languages = [film.language for film in films if film.language in ai_config.short_form.production.languages]
    if not films or not languages:
        _set_stage(db, job, "short_selection", "skipped", {"reason": "no finished language film"})
        return {"status": "skipped", "reason": "no finished language film"}
    blueprint = _latest_blueprint(db, job)
    blueprint_data = json.loads(blueprint.blueprint_json or "{}")
    if job.mode == "pilot":
        # Shorts selection is part of the pilot editorial target: candidates
        # must come from the same opening beats shown by the film, not from the
        # full master story that the pilot deliberately did not narrate.
        from app.documentary.jobs import pilot_beats

        focus = set(pilot_beats(
            blueprint_data, job.pilot_seconds or ai_config.documentary.pilot_seconds
        ))
        blueprint_data = {
            **blueprint_data,
            "beats": [b for b in blueprint_data.get("beats") or []
                      if b.get("id") in focus],
        }
    candidates = ShortFormDirectorAgent().generate_from_blueprint(
        blueprint_data, assets=db.query(VisualAsset).filter(VisualAsset.case_id == job.case_id).all())
    evaluated = [_candidate_payload(db, c, _asset_details(db, job.case_id)) for c in candidates]
    selection = select_candidates(evaluated, target_count=ai_config.short_form.production.target_count)
    selected_ids = {c.get("id") for c in selection["selected"]}
    film_by_lang = {film.language: film for film in films}
    anchor = film_by_lang.get("en") or films[0]
    rows = [_upsert_candidate(db, job, anchor, {**c, "status": "selected" if c.get("id") in selected_ids else c.get("status", "rejected")}) for c in evaluated]
    db.commit()
    _register(db, job, "short_moment_selection", language=None, short_id=None,
              inputs={"film_job_id": job.id, "film_ids": [f.id for f in films],
                      "blueprint_id": blueprint.id, "pilot_beat_ids": sorted(focus)
                      if job.mode == "pilot" else None, "selection": selection,
                      "config": ai_config.short_form.production.model_dump()},
              output_ref=None, output_hash=_hash(selection))
    _set_stage(db, job, "short_selection", "done", {
        "candidate_ids": [row.id for row in rows],
        "selected": len(selected_ids),
        "selection_hash": _hash(selection),
    })
    selected = [row for row in rows if row.stable_key in selected_ids]
    queue_ids = []
    for language in languages:
        _set_stage(db, job, f"short_script:{language}", "queued", None)
        _set_stage(db, job, f"short_render:{language}", "queued", None)
        for candidate in selected:
            render = (db.query(RenderedShort)
                      .filter_by(candidate_id=candidate.id, language=language).one_or_none())
            if render is None:
                render = RenderedShort(case_id=job.case_id, candidate_id=candidate.id,
                                       film_job_id=job.id, film_version_id=film_by_lang[language].id,
                                       language=language, status="queued")
                db.add(render)
                db.flush()
            queue = _queue(db, job=job, stage="script_render",
                           unique_key=f"render:{job.id}:{candidate.stable_key}:{language}",
                           candidate_id=candidate.id, rendered_short_id=render.id,
                           language=language, input_hash=candidate.content_hash)
            queue_ids.append(queue.id)
    db.commit()
    return {"status": "queued", "candidates": len(rows), "selected": len(selected),
            "queue_ids": queue_ids, "renders": 0, "errors": {}}


def _process_short_job(db: Session, queue: ShortProductionJob, *, adapter: ShortsTextAdapter) -> dict:
    job = db.get(DocumentaryJob, queue.film_job_id)
    candidate = db.get(ShortCandidate, queue.candidate_id) if queue.candidate_id else None
    if job is None or candidate is None or queue.language is None:
        raise RuntimeError("short queue item is missing its production input")
    film = (db.query(Video).filter_by(job_id=job.id, language=queue.language, state="rendered")
            .order_by(Video.id.desc()).first())
    render = db.get(RenderedShort, queue.rendered_short_id) if queue.rendered_short_id else None
    if render is None:
        render = (db.query(RenderedShort).filter_by(candidate_id=candidate.id, language=queue.language)
                  .one_or_none())
    if render is None:
        raise RuntimeError("short queue item has no rendered-short row")
    queue.cost_cap_usd = queue.cost_cap_usd or _short_cost_cap(db, job.case_id)
    if _case_provider_spend(db, job.case_id) >= queue.cost_cap_usd:
        reason = (
            f"provider cost cap reached (${_case_provider_spend(db, job.case_id):.4f} / "
            f"${queue.cost_cap_usd:.4f})"
        )
        render.status, render.error = "skipped", reason
        queue.status, queue.error = "skipped", reason
        db.commit()
        _refresh_language_stage(db, job, queue.language)
        return {"status": "skipped", "error": reason}
    script = None
    last_error: Exception | None = None
    for _attempt in range(ai_config.short_form.production.max_redos + 1):
        try:
            script, script_payload = _write_script(
                db, job, candidate, queue.language, adapter,
                on_provider_result=lambda result: _record_provider_result(db, queue, result),
            )
            _register(db, job, "short_script_captions", language=queue.language,
                      short_id=candidate.stable_key, inputs=script_payload,
                      output_ref=None, output_hash=script.content_hash)
            break
        except Exception as exc:
            last_error = exc
            if isinstance(exc, ShortsCostCapReached):
                break
    if script is None:
        render.status = "skipped" if isinstance(
            last_error, (ShortsAuditRejected, ShortsCostCapReached)
        ) else "failed"
        render.error = f"{type(last_error).__name__}: {last_error}"[:1000]
        queue.status, queue.error = render.status, render.error
        db.commit()
        _refresh_language_stage(db, job, queue.language)
        return {"status": render.status, "error": render.error}
    _render_one(db, job, candidate, script, film)
    queue.rendered_short_id = render.id
    queue.output_hash = render.content_hash
    queue.status, queue.error = "done", None
    db.commit()
    _refresh_language_stage(db, job, queue.language)
    _set_stage(db, job, f"short_script:{queue.language}:{candidate.stable_key}", "done",
               {"candidate_id": candidate.id})
    _set_stage(db, job, f"short_render:{queue.language}:{candidate.stable_key}", "done",
               {"rendered_short_id": render.id, "isolated": True})
    return {"status": "done", "rendered_short_id": render.id}


def _refresh_language_stage(db: Session, job: DocumentaryJob, language: str) -> None:
    rows = (db.query(ShortProductionJob)
            .filter(ShortProductionJob.film_job_id == job.id,
                    ShortProductionJob.language == language,
                    ShortProductionJob.stage == "script_render").all())
    if not rows:
        return
    statuses = {row.status for row in rows}
    status = ("failed" if "failed" in statuses else
              "skipped" if "skipped" in statuses else
              "done" if statuses == {"done"} else "running")
    detail = {"queue_items": len(rows), "done": sum(row.status == "done" for row in rows),
              "failed": sum(row.status == "failed" for row in rows),
              "skipped": sum(row.status == "skipped" for row in rows), "isolated": True}
    _set_stage(db, job, f"short_script:{language}", status, detail)
    _set_stage(db, job, f"short_render:{language}", status, detail)


def produce_for_job(db: Session, job: DocumentaryJob, *, adapter: ShortsTextAdapter | None = None) -> dict:
    """Synchronous composition entry point used by unit tests and operators.

    Production uses the durable queue and ``ProviderShortsTextAdapter``;
    callers that explicitly invoke this helper may inject a test adapter.
    """
    result = _prepare_for_job(db, job)
    if result.get("status") != "queued":
        return result
    adapter = adapter or MockShortsTextAdapter()
    errors: dict[str, str] = {}
    rendered = 0
    for queue_id in result.get("queue_ids", []):
        queue = db.get(ShortProductionJob, queue_id)
        if queue is None:
            continue
        queue.status, queue.worker_id = "running", "direct"
        queue.attempts += 1
        db.commit()
        try:
            outcome = _process_short_job(db, queue, adapter=adapter)
            if outcome["status"] == "done":
                rendered += 1
            elif outcome.get("error"):
                errors[f"{queue.language}:{queue.candidate_id}"] = outcome["error"]
        except Exception as exc:
            queue.status, queue.error = "failed", f"{type(exc).__name__}: {exc}"[:1000]
            db.commit()
            errors[f"{queue.language}:{queue.candidate_id}"] = queue.error
    return {**result, "status": "completed", "renders": rendered, "errors": errors}


def run_short_queue_job(queue_id: int, session_factory=SessionLocal) -> dict:
    db = session_factory()
    try:
        queue = db.get(ShortProductionJob, queue_id)
        if queue is None:
            return {"status": "skipped", "reason": "short queue item missing"}
        job = db.get(DocumentaryJob, queue.film_job_id)
        if job is None or job.status not in ("completed", "completed_warnings"):
            queue.status, queue.error = "skipped", "long film is not finished"
            db.commit()
            return {"status": "skipped", "reason": "long film is not finished"}
        if queue.stage == "selection":
            result = _prepare_for_job(db, job, language=queue.language)
            queue.status = "done" if result.get("status") == "queued" else result.get("status", "skipped")
            queue.output_hash = _hash(result)
            db.commit()
            return result
        return _process_short_job(db, queue, adapter=ProviderShortsTextAdapter())
    except Exception as exc:
        log.exception("shorts queue item %s failed", queue_id)
        if 'queue' in locals() and queue is not None:
            queue.status, queue.error = "failed", f"{type(exc).__name__}: {exc}"[:1000]
            db.commit()
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        db.close()


def launch_for_finished_job(job_id: int):
    """Compatibility wrapper: enqueue only; it never starts an asyncio task."""
    db = SessionLocal()
    try:
        return enqueue_for_finished_job(db, job_id)
    finally:
        db.close()


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
                                         "caption_text": script.caption_text,
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
