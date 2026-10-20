"""Hash-bound per-language approval bundles for films and Shorts."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.db.models import ApprovalBundle, ApprovalBundleItem, PolicyEvent, RenderedShort, Video
from app.documentary import storage
from app.services.upload_adapter import (
    SandboxUploadAdapter,
    UploadAdapterError,
    YouTubeUploadAdapter,
)
from app.utils import utc_now


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def film_hash(row: Video) -> str:
    if row.file_path:
        path = storage.resolve(row.file_path)
        if path is not None and path.exists():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    return _digest({"type": "film", "id": row.id, "language": row.language,
                    "title": row.title, "file_path": row.file_path,
                    "metadata": row.metadata_json})


def short_hash(row: RenderedShort) -> str:
    return row.content_hash or _digest({"type": "short", "id": row.id,
                                        "language": row.language,
                                        "caption": row.caption_text,
                                        "video": row.video_path,
                                        "manifest": row.manifest_path})


def _current_items(db: Session, bundle: ApprovalBundle) -> list[tuple[str, int, str, str]]:
    films = (db.query(Video).filter(Video.case_id == bundle.case_id,
                                    Video.language == bundle.language,
                                    Video.state != "archived")
             .order_by(Video.id.desc()).all())
    result: list[tuple[str, int, str, str]] = []
    if films:
        film = films[0]
        result.append(("film", film.id, film.title or f"Film {film.id}", film_hash(film)))
        shorts = (db.query(RenderedShort)
                  .filter(RenderedShort.film_job_id == film.job_id,
                          RenderedShort.language == bundle.language)
                  .order_by(RenderedShort.id).all())
        result.extend(("short", row.id, f"Short {row.id}", short_hash(row)) for row in shorts)
    return result


def sync_bundle(db: Session, bundle: ApprovalBundle) -> ApprovalBundle:
    wanted = {(kind, item_id): (label, digest)
              for kind, item_id, label, digest in _current_items(db, bundle)}
    existing = {(item.item_type, item.item_id): item for item in bundle.items}
    for key, item in existing.items():
        if key not in wanted:
            if item.status == "approved":
                item.status = "needs_review"
            continue
        label, digest = wanted[key]
        if item.content_hash != digest:
            item.content_hash = digest
            item.label = label
            if item.status == "approved":
                item.status = "needs_review"
                item.approved_hash = None
        else:
            item.label = label
    for (kind, item_id), (label, digest) in wanted.items():
        if (kind, item_id) not in existing:
            db.add(ApprovalBundleItem(bundle=bundle, item_type=kind, item_id=item_id,
                                      label=label, content_hash=digest))
    db.flush()
    active = [item for item in bundle.items if item.status != "excluded"]
    if active and all(item.status == "approved" and item.approved_hash == item.content_hash
                      for item in active):
        bundle.status = "approved"
        bundle.approved_at = bundle.approved_at or utc_now()
    else:
        bundle.status = "draft"
        bundle.approved_at = None
    bundle.bundle_hash = _digest(sorted((item.item_type, item.item_id, item.content_hash,
                                         item.status) for item in bundle.items))
    bundle.updated_at = utc_now()
    return bundle


def bundle_for(db: Session, case_id: int, language: str) -> ApprovalBundle:
    bundle = (db.query(ApprovalBundle).filter_by(case_id=case_id, language=language).one_or_none())
    if bundle is None:
        bundle = ApprovalBundle(case_id=case_id, language=language)
        db.add(bundle)
        db.flush()
    sync_bundle(db, bundle)
    db.commit()
    db.refresh(bundle)
    return bundle


def bundle_dict(db: Session, bundle: ApprovalBundle) -> dict:
    sync_bundle(db, bundle)
    return {
        "id": bundle.id, "case_id": bundle.case_id, "language": bundle.language,
        "status": bundle.status, "visibility": bundle.visibility,
        "account_connected": bundle.account_connected, "bundle_hash": bundle.bundle_hash,
        "approved_at": bundle.approved_at, "upload_status": bundle.upload_status,
        "upload_id": bundle.upload_id, "upload_error": bundle.upload_error,
        "uploaded_at": bundle.uploaded_at, "release_now": bundle.release_now,
        "items": [{"id": item.id, "item_type": item.item_type, "item_id": item.item_id,
                   "label": item.label, "content_hash": item.content_hash,
                   "approved_hash": item.approved_hash, "status": item.status,
                   "reviewed_at": item.reviewed_at} for item in bundle.items],
    }


def decide_item(db: Session, bundle: ApprovalBundle, item_type: str, item_id: int,
                decision: str) -> ApprovalBundle:
    sync_bundle(db, bundle)
    item = next((row for row in bundle.items
                 if row.item_type == item_type and row.item_id == item_id), None)
    if item is None:
        raise ValueError("Bundle item not found")
    if decision == "approve":
        item.status, item.approved_hash = "approved", item.content_hash
    elif decision == "exclude":
        item.status, item.approved_hash = "excluded", None
    elif decision == "re-review":
        item.status, item.approved_hash = "pending", None
    else:
        raise ValueError("Unknown bundle item decision")
    item.reviewed_at = utc_now()
    sync_bundle(db, bundle)
    db.commit()
    db.refresh(bundle)
    return bundle


def upload_bundle(db: Session, bundle: ApprovalBundle, *, visibility: str = "private",
                  release_now: bool = False,
                  adapter: YouTubeUploadAdapter | None = None) -> dict:
    if visibility not in {"private", "draft"}:
        raise ValueError("Uploads must be private or draft")
    sync_bundle(db, bundle)
    if bundle.status != "approved":
        bundle.upload_status = "failed"
        bundle.upload_error = "bundle is not fully approved"
        db.commit()
        raise ValueError("Every included item must be approved or excluded")
    if release_now and not bundle.account_connected:
        bundle.upload_status = "failed"
        bundle.upload_error = "release now requires a connected account"
        db.commit()
        raise ValueError("Release now requires a connected account")
    try:
        receipt = (adapter or SandboxUploadAdapter()).upload(
            bundle_id=bundle.id, bundle_hash=bundle.bundle_hash or "", visibility=visibility,
        )
    except UploadAdapterError as exc:
        # Approval is deliberately retained: quota/token/partial failures are
        # transport failures, not content-review failures, and can be retried.
        bundle.upload_status = "retryable"
        bundle.upload_error = str(exc)[:500]
        db.commit()
        raise
    bundle.visibility = receipt.visibility
    bundle.release_now = release_now
    bundle.upload_status, bundle.upload_id = "uploaded", receipt.upload_id
    bundle.upload_error, bundle.uploaded_at = None, utc_now()
    db.add(PolicyEvent(
        case_id=bundle.case_id, language=bundle.language,
        policy="approval_bundle_upload", decision="uploaded", reason="sandbox adapter",
        context_json=json.dumps({"bundle_id": bundle.id, "bundle_hash": bundle.bundle_hash,
                                 "visibility": visibility, "release_now": release_now}, sort_keys=True),
    ))
    db.commit()
    db.refresh(bundle)
    return {"bundle": bundle_dict(db, bundle), "upload_id": receipt.upload_id,
            "visibility": receipt.visibility}
