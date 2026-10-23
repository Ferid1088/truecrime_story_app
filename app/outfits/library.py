"""The outfit library: ``data/outfits`` is the single source of truth.

Layout: ``data/outfits/<studio>/<outfit>/<studio>_<outfit>_<situation>.png``
``<studio>`` is a studio/language id that exists in the studio registry
(``config/studio_registry.json``), ``<outfit>`` any folder name and ``<situation>``
any text after the second underscore — new situation names need no code change:
the pose classifier reads them by keyword (sitting/standing, hands, crossed legs).

``scan_library`` mirrors the folder into ``outfit_items``: new files are imported,
changed files (by sha256) updated, vanished files marked ``missing_on_disk`` —
rows and files are never deleted. Every photo gets checked on import (readable,
a person visible, naming matches its folder); problems are reported, not blocking.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import HostOutfit, OutfitItem, OutfitScan
from app.utils import utc_now

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
OUTFIT_ROOT = ROOT / "data" / "outfits"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MIN_SIDE = 64

# Situation keywords → pose facts. Matching is word-boundary on camelCase tokens, so
# "sitCrossFeetChin", "standPockets" and future names all classify without code edits.
_TOKENS = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")

_SIT = {"sit", "sitting", "seated", "squat", "crouch", "kneel"}
_STAND = {"stand", "standing", "walk", "stride", "lean"}
_HANDS = {"hand", "hands", "pockets", "chin", "arms", "folded"}
_CROSSED = {"cross", "crossed", "x"}
_LEGS = {"feet", "legs", "knee", "ankles"}


def _split_tokens(situation: str) -> list[str]:
    tokens = _TOKENS.findall(situation)
    out: list[str] = []
    for token in tokens:
        out.append(token.lower())
    return out


def classify_pose(situation: str) -> dict:
    """Pose facts from the situation name: stance + detail. Unknown names stay
    usable — stance ``unknown`` simply means selection can't lean on it."""
    tokens = _split_tokens(situation)
    joined = " ".join(tokens)
    if any(t in _SIT or t.startswith("sit") for t in tokens):
        stance = "sitting"
    elif any(t in _STAND for t in tokens):
        stance = "standing"
    else:
        stance = "unknown"
    detail = {
        "stance": stance,
        "hands": any(t in _HANDS for t in tokens),
        "crossed_legs": "cross" in joined and any(t in _LEGS for t in tokens)
        or "sitCrossFeet" in situation,
        "crossed_arms": "cross" in joined and "arm" in joined,
        "tokens": tokens,
    }
    detail["crossed_legs"] = detail["crossed_legs"] or "crossedlegs" in joined.replace(" ", "")
    return detail


def studio_ids() -> set[str]:
    """Studios the library accepts: every profile id in the studio registry."""
    from app.documentary import studio as ST

    try:
        reg = ST.load_registry()
        return {p.id for p in reg.profiles.values()} | {p.language for p in reg.profiles.values()}
    except Exception:
        log.warning("studio registry unavailable for outfit library check")
        return set()


def check_photo(path: Path, studio: str, outfit: str, situation: str) -> list[str]:
    """Per-photo problems, non-blocking: unreadable file, wrong studio/folder
    prefix in the name, implausible dimensions, no person visible."""
    issues: list[str] = []
    try:
        head = path.read_bytes()[:8]
    except OSError as exc:
        return [f"unreadable file: {exc}"]
    if head != _PNG_MAGIC:
        issues.append("not a PNG file")
    if not path.name.startswith(f"{studio}_{outfit}_"):
        issues.append(f"filename does not match folder {studio}/{outfit}")
    try:
        from PIL import Image

        with Image.open(path) as img:
            width, height = img.size
    except Exception as exc:
        issues.append(f"image does not open: {exc}")
        return issues
    if min(width, height) < _MIN_SIDE:
        issues.append(f"too small: {width}x{height}")
    issues += _person_issues(path)
    return issues


def _person_issues(path: Path) -> list[str]:
    """'A person is in this picture' via image statistics — a heuristic warning, never
    a block (dark fabrics and side profiles legitimately score low)."""
    try:
        import cv2  # type: ignore
    except Exception:
        return []
    img = cv2.imread(str(path))
    if img is None:
        return ["image unreadable by cv2"]
    issues = []
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if float(gray.std()) < 8.0:
        issues.append("flat image — likely no subject")
    # YCrCb skin band (Cr≈133–173): an outfit photo of the host shows some skin
    cr = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)[:, :, 1]
    skin_ratio = float(((cr > 133) & (cr < 173)).mean())
    if skin_ratio < 0.005:
        issues.append("no person visible")
    return issues


def photo_size(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return img.size
    except Exception:
        return None, None


def in_use(db: Session) -> set[str]:
    """outfit_refs case outfits point at — a library outfit in use is kept (and
    only ever marked missing_on_disk, never removed)."""
    return {ref for (ref,) in db.query(HostOutfit.outfit_ref)
            .filter(HostOutfit.outfit_ref.isnot(None)).all()}


def scan_library(db: Session, trigger: str = "manual",
                 root: Path | None = None) -> dict:
    """Mirror data/outfits into outfit_items. Returns the scan stats."""
    outfit_root = root or OUTFIT_ROOT
    started = utc_now()
    stats = {"found": 0, "added": 0, "updated": 0, "missing": 0}
    problems: list[dict] = []
    studios = studio_ids()
    seen_keys: set[tuple[str, str, str]] = set()

    if outfit_root.is_dir():
        for studio_dir in sorted(p for p in outfit_root.iterdir() if p.is_dir()):
            studio = studio_dir.name
            if studios and studio not in studios:
                problems.append({"folder": studio, "issue":
                                 "not a studio in the registry — skipped"})
                continue
            for outfit_dir in sorted(p for p in studio_dir.iterdir() if p.is_dir()):
                outfit = outfit_dir.name
                for photo in sorted(p for p in outfit_dir.iterdir()
                                    if p.is_file() and p.name != ".DS_Store"):
                    stats["found"] += 1
                    try:
                        rel = photo.relative_to(ROOT).as_posix()
                    except ValueError:
                        rel = photo.as_posix()
                    if photo.suffix.lower() != ".png" or not photo.name.startswith(
                            f"{studio}_{outfit}_"):
                        problems.append({"file": rel, "issue": "unexpected name — skipped"})
                        continue
                    situation = photo.stem[len(f"{studio}_{outfit}_"):]
                    key = (studio, outfit, situation)
                    seen_keys.add(key)
                    digest = hashlib.sha256(photo.read_bytes()).hexdigest()
                    row = (db.query(OutfitItem).filter_by(
                        studio=studio, outfit_slug=outfit, situation=situation).first())
                    if row is not None and row.sha256 == digest:
                        row.last_seen_at = started
                        if row.status != "available":
                            row.status = "available"
                            row.updated_at = started
                        continue
                    issues = check_photo(photo, studio, outfit, situation)
                    for issue in issues:
                        problems.append({"file": rel, "issue": issue})
                    width, height = photo_size(photo)
                    detail = classify_pose(situation)
                    if row is None:
                        row = OutfitItem(
                            studio=studio, outfit_slug=outfit, situation=situation,
                            file_path=rel, first_seen_at=started)
                        db.add(row)
                        stats["added"] += 1
                    else:
                        stats["updated"] += 1
                    row.sha256 = digest
                    row.size_bytes = photo.stat().st_size
                    row.width, row.height = width, height
                    row.stance = detail["stance"]
                    row.pose_detail_json = json.dumps(detail, ensure_ascii=False)
                    row.issues_json = json.dumps(issues, ensure_ascii=False)
                    row.status = "available"
                    row.last_seen_at = row.updated_at = started

    for stale in db.query(OutfitItem).filter(OutfitItem.status != "missing_on_disk").all():
        key = (stale.studio, stale.outfit_slug, stale.situation)
        if key not in seen_keys:
            stale.status = "missing_on_disk"
            stale.updated_at = started
            stats["missing"] += 1
            problems.append({"file": stale.file_path, "issue": "missing on disk"})

    scan = OutfitScan(trigger=trigger, started_at=started, finished_at=utc_now(),
                      found=stats["found"], added=stats["added"], updated=stats["updated"],
                      missing=stats["missing"],
                      problems_json=json.dumps(problems, ensure_ascii=False))
    db.add(scan)
    db.commit()
    log.info("outfit library scan (%s): %s", trigger, stats)
    return {"scan_id": scan.id, **stats, "problems": problems}


def library_view(db: Session) -> dict:
    """What the UI renders: per studio the outfits with their photos, plus the last
    scan time and counts. New situation names show their raw name as the label."""
    rows = (db.query(OutfitItem).order_by(OutfitItem.studio, OutfitItem.outfit_slug,
                                          OutfitItem.situation).all())
    used = in_use(db)
    studios: dict[str, dict] = {}
    for row in rows:
        studio = studios.setdefault(row.studio, {"studio": row.studio, "outfits": {}})
        outfit = studio["outfits"].setdefault(row.outfit_slug, {
            "slug": row.outfit_slug, "ref": f"{row.studio}/{row.outfit_slug}",
            "in_use": f"{row.studio}/{row.outfit_slug}" in used, "items": []})
        outfit["items"].append({
            "id": row.id, "situation": row.situation, "label": row.situation,
            "status": row.status, "stance": row.stance,
            "pose": json.loads(row.pose_detail_json or "{}"),
            "issues": json.loads(row.issues_json or "[]"),
            "file_path": row.file_path, "width": row.width, "height": row.height,
            "sha256": row.sha256[:12],
        })
    last = db.query(OutfitScan).order_by(OutfitScan.id.desc()).first()
    return {
        "studios": [{"studio": s["studio"],
                     "outfits": list(s["outfits"].values())} for s in studios.values()],
        "last_scan": None if last is None else {
            "id": last.id, "trigger": last.trigger, "finished_at": last.finished_at,
            "found": last.found, "added": last.added, "updated": last.updated,
            "missing": last.missing,
            "problems": json.loads(last.problems_json or "[]"),
        },
        "counts": {
            "items": len(rows),
            "outfits": sum(len(s["outfits"]) for s in studios.values()),
            "available": sum(1 for r in rows if r.status == "available"),
            "missing_on_disk": sum(1 for r in rows if r.status == "missing_on_disk"),
        },
    }
