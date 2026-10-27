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
from app.documentary.visuals.usage import UsageTracker, asset_facts

PICTURE_KINDS = {"image", "video", "map", "document", "timeline", "detective_board", "graphic"}
TEXT_KINDS = {"graphic", "chapter", "title", "timeline", "detective_board"}
STRUCTURE_KINDS = {"chapter", "title", "intro", "host", "status", "label"}
EPS = 0.05
CARD_KINDS = ("black", "title", "chapter", "timeline", "date", "graphic")


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
    asset_rows = {a.asset_code: a for a in db.query(VisualAsset).filter(
        VisualAsset.case_id == plan.case_id, VisualAsset.asset_code.in_(
            {s["asset_id"] for s in shots if s.get("asset_id")} or {""})).all()}
    tracker = UsageTracker.from_shots(shots, {c: asset_facts(a) for c, a in asset_rows.items()})
    over_reused = {c: tracker.appearances(c) for c in tracker.facts
                   if tracker.appearances(c) > tracker.limit(c)}
    approved = {c.visual_asset_id for c in db.query(MvpCandidate).join(
        MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id).filter(
        MvpRequirement.visual_plan_id == plan.id, MvpCandidate.final_status == "approved")}
    approved_codes = {a.asset_code for a in db.query(VisualAsset).filter(VisualAsset.id.in_(approved))} \
        if approved else set()
    generated = {s.get("asset_id") for s in shots if s.get("kind") in ("map", "timeline", "detective_board")}
    generated |= {c for c, a in asset_rows.items() if a.provider == "generated"}
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
        "over_reused": over_reused,
        "unapproved_assets": unapproved, "weak_sentences": weak[:50], "weak_sentence_count": len(weak),
        "requirements": requirement_coverage(db, plan, shots, tracker),
        "graphics": graphic_checks(script),
    }


def fact_ids(value) -> set[str]:
    """Every fact id a graphic's data names (they say what evidence the page shows)."""
    found: set[str] = set()
    if isinstance(value, dict):
        if isinstance(value.get("fact_id"), str):
            found.add(value["fact_id"])
        for v in value.values():
            found |= fact_ids(v)
    elif isinstance(value, list):
        for v in value:
            found |= fact_ids(v)
    return found


def graphic_checks(script: dict) -> dict:
    """What the film shows that is not a picture: nothing of it appears twice, nothing of it moves, all of it is in
    the film's language, and none was dropped for no longer fitting."""
    shots = [s for s in script.get("shots") or [] if s.get("kind") == "graphic"]
    seen: dict[str, int] = {}
    repeated = []
    for n, s in enumerate(shots):
        for fid in fact_ids(s["graphic"]["data"]):
            if fid in seen and seen[fid] != n:
                repeated.append({"evidence": fid, "at": s["start"]})
            seen[fid] = n
    moving = [{"kind": s["kind"], "start": s["start"], "motion": s.get("motion")}
              for s in script.get("shots") or [] if s.get("kind") in TEXT_KINDS and s.get("motion") not in (None, "NONE")]
    wrong = [{"type": s["graphic"]["type"], "start": s["start"], "language": s["graphic"].get("language")}
             for s in shots if s["graphic"].get("language") != script.get("language")]
    by_type: dict[str, float] = {}
    for s in shots:
        by_type[s["graphic"]["type"]] = by_type.get(s["graphic"]["type"], 0.0) + s["end"] - s["start"]
    return {"count": len(shots), "seconds": {k: round(v, 1) for k, v in sorted(by_type.items())},
            "repeated": repeated, "moving": moving, "wrong_language": wrong,
            "dropped": script.get("graphics_dropped") or []}


def accepted_roles(req: MvpRequirement) -> set[str] | None:
    """The roles of picture that satisfy a requirement; None when the review does not count it (low priority, or an
    illustration)."""
    if req.priority == "low" or req.role_needed == "illustration":
        return None
    need_role = ai_config.mvp().coverage.evidence_requirements_need_evidence_roles
    return {"evidence"} if (need_role and req.role_needed == "evidence") else {"evidence", "context"}


def counted_requirements(db: Session, plan: VisualPlan) -> dict[str, list[dict]]:
    """The requirements the review counts, per beat, with the roles of picture that satisfy each."""
    out: dict[str, list[dict]] = {}
    for req in db.query(MvpRequirement).filter(MvpRequirement.visual_plan_id == plan.id):
        roles = accepted_roles(req)
        if roles is not None:
            out.setdefault(req.beat_id, []).append({"key": req.requirement_key, "roles": roles,
                                                    "priority": req.priority, "status": req.status})
    return out


def requirement_coverage(db: Session, plan: VisualPlan, shots: list[dict], tracker: UsageTracker) -> dict:
    """Semantic coverage per requirement: an approved asset of the right role really on screen in its beat. A
    requirement whose approved pictures are all shown as often as the film's repetition rules allow is a shortage
    of pictures (recorded, not blocking the review), not a picture the planner left off."""
    on_screen: dict[str, set[str]] = {}
    for s in shots:
        if s.get("asset_id") and s.get("beat_id"):
            on_screen.setdefault(s["beat_id"], set()).add(s["asset_id"])
    out = {"covered": 0, "shortage": 0, "uncovered": 0, "uncovered_ids": [], "shortage_ids": [], "used_up_ids": []}
    for req in db.query(MvpRequirement).filter(MvpRequirement.visual_plan_id == plan.id):
        roles = accepted_roles(req)
        if roles is None:
            continue
        approved = [a for a in (db.get(VisualAsset, c.visual_asset_id) for c in req.candidates
                                if c.final_status == "approved" and c.role in roles) if a is not None]
        if any(a.asset_code in on_screen.get(req.beat_id, set()) for a in approved):
            out["covered"] += 1
            continue
        for a in approved:
            tracker.facts.setdefault(a.asset_code, asset_facts(a))
        used_up = bool(approved) and all(
            tracker.appearances(a.asset_code) >= tracker.limit(a.asset_code) for a in approved)
        if req.status == "shortage" or used_up:
            out["shortage"] += 1
            out["shortage_ids"].append(req.requirement_key)
            if used_up:
                out["used_up_ids"].append(req.requirement_key)
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
    graphics = report["graphics"]
    if graphics["repeated"]:
        blocking.append(f"{len(graphics['repeated'])} graphic item(s) shown more than once")
    if graphics["moving"]:
        blocking.append(f"{len(graphics['moving'])} text page(s) zoom or move")
    if graphics["wrong_language"]:
        blocking.append(f"{len(graphics['wrong_language'])} graphic(s) not in the film's language")
    if graphics["dropped"]:
        blocking.append(f"{len(graphics['dropped'])} graphic(s) no longer fitted their length")
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


def window_cards(script: dict, start: float, end: float) -> tuple[float, float]:
    """(card seconds, longest card stretch) of the film inside [start, end]: a short cut from a
    stretch of cards would be a text frame, not a picture."""
    total, longest, run_end, run = 0.0, 0.0, None, 0.0
    for s in sorted(script.get("shots") or [], key=lambda x: x["start"]):
        a, b = max(s["start"], start), min(s["end"], end)
        if b <= a or s.get("kind") not in CARD_KINDS:
            continue
        total += b - a
        run = run + (b - a) if run_end is not None and abs(run_end - a) <= EPS else (b - a)
        run_end = b
        longest = max(longest, run)
    return round(total, 2), round(longest, 2)
