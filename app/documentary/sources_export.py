"""The sources of a finished film: every picture and clip it shows, with where it came from, the
rights status as recorded, the credit and, for clips, the time range of the source that plays."""

from __future__ import annotations

import csv
import io
import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import ProductionScript, VisualAsset

PICTURE_KINDS = ("image", "video", "document", "map")
COLUMNS = ["asset_code", "kind", "title", "source_url", "page_url", "source", "provider", "license",
           "rights_status", "credit", "clip_start", "clip_end", "source_start", "source_end", "source_class",
           "source_date", "downloaded_at", "film_start", "film_end", "crop_region"]


def rows(db: Session, ps: ProductionScript) -> list[dict]:
    shots = [s for s in json.loads(ps.script_json or "{}").get("shots") or []
             if s.get("asset_id") and s.get("kind") in PICTURE_KINDS]
    codes = {s["asset_id"] for s in shots}
    assets = {a.asset_code: a for a in db.query(VisualAsset).filter(
        VisualAsset.case_id == ps.case_id, VisualAsset.asset_code.in_(codes or {""}))}
    parents = {json.loads(a.spec_json or "{}").get("parent") for a in assets.values()} - {None}
    sources = {a.asset_code: a for a in db.query(VisualAsset).filter(
        VisualAsset.case_id == ps.case_id, VisualAsset.asset_code.in_(parents or {""}))}
    out = []
    for s in sorted(shots, key=lambda x: x["start"]):
        a = assets.get(s["asset_id"])
        if a is None:
            continue
        credit = a.credit
        if s["kind"] == "map":
            credit = " · ".join(s.get("attribution") or [ai_config.maps.attribution])
        spec = json.loads(a.spec_json or "{}")
        record = spec.get("source_record") or {}
        origin = json.loads(sources[spec["parent"]].spec_json or "{}") if spec.get("parent") in sources else spec
        base = (origin.get("source_window") or [None])[0]
        has_clip = s["kind"] == "video" and base is not None and s.get("clip_start") is not None
        out.append({
            "asset_code": a.asset_code, "kind": s["kind"], "title": a.title or "",
            "source_url": a.source_url or "", "page_url": a.page_url or "", "source": a.source_name or "",
            "provider": a.provider, "license": a.license or "", "rights_status": a.rights_status,
            "credit": credit or "",
            "clip_start": s.get("clip_start", "") if s["kind"] == "video" else "",
            "clip_end": s.get("clip_end", "") if s["kind"] == "video" else "",
            "source_start": round(base + s["clip_start"], 3) if has_clip else "",
            "source_end": round(base + s["clip_end"], 3) if has_clip else "",
            "source_class": record.get("source_class") or "", "source_date": record.get("video_date") or "",
            "downloaded_at": record.get("downloaded_at") or "",
            "film_start": s["start"], "film_end": s["end"],
            "crop_region": s.get("crop_region") if s.get("crop_region") is not None else ""})
    return out


def csv_text(db: Session, ps: ProductionScript) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows(db, ps))
    return buf.getvalue()
