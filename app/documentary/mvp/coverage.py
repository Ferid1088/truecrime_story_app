"""Step 6/10 - coverage and the exact-film checks. Coverage is measured on the COMPILED production script
(what is really on screen when) and the file is probed independently of the script that made it."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin, ffprobe_bin
from app.db.models import MvpCandidate, MvpRequirement, VisualAsset, VisualPlan

PICTURE_KINDS = {"image", "video", "map", "document", "timeline", "detective_board"}
STRUCTURE_KINDS = {"chapter", "title", "intro", "host", "status", "label"}
EPS = 0.05


def _runs(shots: list[dict], kinds: set[str]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for s in sorted(shots, key=lambda x: x["start"]):
        if s.get("kind") in kinds:
            if out and abs(out[-1][1] - s["start"]) <= EPS:
                out[-1] = (out[-1][0], s["end"])
            else:
                out.append((s["start"], s["end"]))
    return out


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def measure(script: dict, plan: VisualPlan, db: Session, sentences: list[dict]) -> dict:
    """Everything the review needs to know about the picture track of one compiled script."""
    cfg = ai_config.mvp()
    shots = script.get("shots") or []
    duration = float(script.get("duration") or 0.0)
    covered = sum(s["end"] - s["start"] for s in shots if s.get("kind") not in (None, "black"))
    black_runs = _runs(shots, {"black"})
    longest_black = max((b - a for a, b in black_runs), default=0.0)
    long_stills = [{"asset": s.get("asset_id"), "start": s["start"], "seconds": round(s["end"] - s["start"], 2)}
                   for s in shots if s.get("kind") == "image"
                   and s["end"] - s["start"] > cfg.shots.max_still_seconds + 0.5]
    appearances: dict[str, int] = {}
    last = None
    for s in sorted(shots, key=lambda x: x["start"]):
        code = s.get("asset_id")
        if code and s.get("kind") in PICTURE_KINDS and code != last:
            appearances[code] = appearances.get(code, 0) + 1
        last = code if s.get("kind") in PICTURE_KINDS else None
    approved = {c.visual_asset_id for c in db.query(MvpCandidate).join(
        MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id).filter(
        MvpRequirement.visual_plan_id == plan.id, MvpCandidate.final_status == "approved")}
    approved_codes = {a.asset_code for a in db.query(VisualAsset).filter(VisualAsset.id.in_(approved))} \
        if approved else set()
    generated = {s.get("asset_id") for s in shots if s.get("kind") in ("map", "timeline", "detective_board")}
    unapproved = sorted({s["asset_id"] for s in shots if s.get("asset_id") and s.get("kind") in
                         ("image", "video", "document") and s["asset_id"] not in approved_codes
                         and s["asset_id"] not in generated})
    weak = []
    for sent in sentences:
        span = max(sent["end"] - sent["start"], 0.1)
        seen = sum(_overlap(sent["start"], sent["end"], s["start"], s["end"]) for s in shots
                   if s.get("kind") in PICTURE_KINDS)
        if seen / span < 0.5:
            weak.append({"start": round(sent["start"], 2), "text": (sent.get("display") or "")[:80]})
    return {
        "duration": round(duration, 2), "covered_seconds": round(covered, 2),
        "temporal_coverage": round(covered / duration, 4) if duration else 0.0,
        "black_seconds": round(sum(b - a for a, b in black_runs), 2),
        "longest_black": round(longest_black, 2), "long_stills": long_stills,
        "over_reused": {c: n for c, n in appearances.items() if n > cfg.shots.max_asset_reuse},
        "unapproved_assets": unapproved, "weak_sentences": weak[:50], "weak_sentence_count": len(weak),
        "requirements": requirement_coverage(db, plan, shots),
    }


def requirement_coverage(db: Session, plan: VisualPlan, shots: list[dict]) -> dict:
    """Semantic coverage per requirement: an approved asset of the right role really on screen in its beat."""
    on_screen: dict[str, set[str]] = {}
    for s in shots:
        if s.get("asset_id") and s.get("beat_id"):
            on_screen.setdefault(s["beat_id"], set()).add(s["asset_id"])
    need_role = ai_config.mvp().coverage.evidence_requirements_need_evidence_roles
    out = {"covered": 0, "shortage": 0, "uncovered": 0, "uncovered_ids": [], "shortage_ids": []}
    for req in db.query(MvpRequirement).filter(MvpRequirement.visual_plan_id == plan.id):
        if req.priority == "low" or req.role_needed == "illustration":
            continue
        accepted = {"evidence"} if (need_role and req.role_needed == "evidence") else {
            "evidence", "context"}
        ok = False
        for cand in req.candidates:
            if cand.final_status != "approved" or cand.role not in accepted:
                continue
            asset = db.get(VisualAsset, cand.visual_asset_id)
            if asset is not None and asset.asset_code in on_screen.get(req.beat_id, set()):
                ok = True
                break
        if ok:
            out["covered"] += 1
        elif req.status == "shortage":
            out["shortage"] += 1
            out["shortage_ids"].append(req.requirement_key)
        else:
            out["uncovered"] += 1
            out["uncovered_ids"].append(req.requirement_key)
    return out


def verdict(report: dict) -> dict:
    """Blocking problems for the owner-review film, and what additionally blocks publication."""
    cfg = ai_config.mvp().coverage
    blocking, publication = [], []
    if report["temporal_coverage"] < cfg.min_temporal_coverage:
        blocking.append(f"temporal coverage {report['temporal_coverage']:.3f} < {cfg.min_temporal_coverage}")
    if report["longest_black"] > cfg.max_uncovered_gap_seconds:
        blocking.append(f"a black stretch of {report['longest_black']}s")
    if report["unapproved_assets"]:
        blocking.append(f"{len(report['unapproved_assets'])} shot(s) use assets without an approved verdict")
    if report["long_stills"]:
        blocking.append(f"{len(report['long_stills'])} still(s) held beyond the approved maximum")
    if report["over_reused"]:
        blocking.append(f"{len(report['over_reused'])} asset(s) shown more often than allowed")
    if report["requirements"]["uncovered"]:
        blocking.append(f"{report['requirements']['uncovered']} requirement(s) approved but not on screen")
    if report["requirements"]["shortage"]:
        publication.append(f"{report['requirements']['shortage']} requirement(s) recorded as shortage")
    return {"review_ready": not blocking, "blocking": blocking, "publication_blockers": publication}


def probe_file(path: Path) -> dict:
    """The rendered FILE, probed independently of the script: streams, duration, black frames, frozen
    stretches and integrated loudness."""
    ffmpeg = ffmpeg_bin() or "ffmpeg"
    ffprobe = ffprobe_bin() or "ffprobe"
    info = json.loads(subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True).stdout)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), {})
    filters = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-vf",
         f"blackdetect=d=0.5:pix_th=0.05,freezedetect=n=-60dB:d={ai_config.mvp().shots.max_still_seconds + 5}",
         "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True).stderr
    black = sum(float(m) for m in re.findall(r"black_duration:([\d.]+)", filters))
    frozen = [float(m) for m in re.findall(r"freeze_duration:\s*([\d.]+)", filters)]
    loud = re.findall(r"I:\s+(-?[\d.]+) LUFS", filters)
    num, den = (video.get("r_frame_rate") or "0/1").split("/")
    return {"duration": float(info["format"]["duration"]),
            "size_bytes": int(info["format"]["size"]),
            "video_codec": video.get("codec_name"), "width": video.get("width"), "height": video.get("height"),
            "fps": round(int(num) / max(int(den), 1), 3), "audio_codec": audio.get("codec_name"),
            "audio_rate": int(audio.get("sample_rate") or 0), "black_seconds": round(black, 2),
            "frozen_stretches": frozen, "integrated_lufs": float(loud[-1]) if loud else None}
