"""Production script per language (Parts 35–37): the render-ready
timeline composed from the REAL narration audio of that language (beat
and word times), the language-independent visual plan and the music mix.

Each language gets its own exact timing — sentence lengths, pauses and
voice cadence differ — while the editorial plan stays shared.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.story import stored_sections
from app.core.ai_config import ai_config
from app.db.models import ProductionScript, StoryVersion, VisualAsset, VisualPlan
from app.documentary import storage
from app.documentary.visuals import rights as R
from app.documentary.visuals.generated import LABELS, localize_texts

IMAGE_COMMANDS = {"NEW_IMAGE", "ATMOSPHERIC_BROLL", "CROP_EXISTING", "ZOOM_EXISTING"}


@lru_cache(maxsize=256)
def _focus(path: str | None) -> list[int] | None:
    """Largest face (x, y, w, h) — slow pushes and reframes go there."""
    p = storage.resolve(path)
    if p is None or not p.exists():
        return None
    try:
        from PIL import Image

        from app.documentary.visuals.images import face_boxes

        faces = face_boxes(Image.open(p).convert("RGB"))
    except Exception:  # detection is a nicety, never a failure
        return None
    if not faces:
        return None
    x, y, w, h = max(faces, key=lambda b: b[2] * b[3])
    return [x, y, x + w, y + h]


def _still_usable(a: VisualAsset) -> bool:
    return a.verification_status != "rejected" and R.allowed(a.rights_status)


def _asset_info(a: VisualAsset) -> dict:
    spec = json.loads(a.spec_json or "{}")
    ver = json.loads(a.verification_json or "{}") if a.verification_json else {}
    focus = _focus(a.local_path) if a.asset_type == "photo" else None
    return {
        "focus": focus,
        "asset_id": a.asset_code, "path": a.local_path, "type": a.asset_type,
        "role": a.asset_role, "width": a.width, "height": a.height,
        "highlight": spec.get("highlight"), "marker": spec.get("marker"),
        "rights": a.rights_status, "credit": a.credit,
        "subject_type": a.subject_type or ver.get("subject_type"),
    }


def _subtitles(words: list[dict], language: str) -> list[dict]:
    cfg = ai_config.render
    out, cur = [], []

    def flush():
        if cur:
            out.append({"start": round(cur[0]["start"], 3), "end": round(cur[-1]["end"], 3),
                        "text": " ".join(w["word"] for w in cur)})
            cur.clear()

    for w in words:
        if cur:
            text = " ".join(x["word"] for x in cur + [w])
            gap = w["start"] - cur[-1]["end"]
            if (len(text) > cfg.subtitle_max_chars * 2 or gap > 0.8
                    or w["end"] - cur[0]["start"] > cfg.subtitle_max_seconds):
                flush()
        cur.append(w)
        if w["word"][-1:] in ".?!؟" and len(" ".join(x["word"] for x in cur)) > 20:
            flush()
    flush()
    return out


_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _spoken_at(words: list[dict], text_en: str, start: float, end: float) -> float | None:
    """When the narration says the year of a date (any language), so the
    date appears exactly then."""
    import re

    years = re.findall(r"\b(1[89]\d\d|20\d\d)\b", text_en or "")
    if not years:
        return None
    for w in words:
        if start - 0.5 <= w["start"] <= end and years[0] in w["word"].translate(_DIGITS):
            return w["start"]
    return None


def compose(manifest: dict, plan: dict, assets: dict[str, VisualAsset],
            texts: dict[str, str], language: str) -> dict:
    """Pure function: manifest (audio timeline) + visual plan + localized
    texts -> production timeline."""
    motion_cfg = ai_config.motion
    duration = float(manifest.get("duration_seconds") or 0.0)
    spans = sorted(manifest["timeline"].get("beats") or [], key=lambda b: b["start"])
    plan_beats = {b["beat_id"]: b for b in plan.get("beats") or []}
    words = manifest["timeline"].get("words") or []
    shots: list[dict] = []
    overlays: list[dict] = []
    credits: set[str] = set()
    current: dict | None = None  # last image shot

    def add_overlay(kind, text, start, end):
        if text and end - start > 0.5:
            overlays.append({"kind": kind, "text": text, "start": round(start, 3),
                             "end": round(min(end, duration), 3)})

    for i, span in enumerate(spans):
        w_start = 0.0 if i == 0 else span["start"]
        w_end = spans[i + 1]["start"] if i + 1 < len(spans) else duration
        if w_end - w_start <= 0.05:
            continue
        pb = plan_beats.get(span["beat_id"]) or {"shots": [{"command": "KEEP_CURRENT_IMAGE", "share": 1.0}]}
        t = w_start
        for j, s in enumerate(pb["shots"]):
            dur = (w_end - w_start) * s["share"] if j < len(pb["shots"]) - 1 else w_end - t
            start, end = t, t + dur
            t = end
            cmd = s["command"]
            text_key = f"{s.get('overlay', {}).get('kind', 'x')}|{s.get('overlay', {}).get('text_en', '')}"
            if cmd == "SHOW_DATE":
                said = _spoken_at(words, s.get("overlay", {}).get("text_en"), w_start, w_end)
                at = said - 0.2 if said is not None else start + 0.4
                add_overlay("date", texts.get(text_key), at, at + 4.6)
            if cmd in ("KEEP_CURRENT_IMAGE", "NO_VISUAL_CHANGE", "SHOW_DATE") and shots:
                shots[-1]["end"] = round(end, 3)
                continue
            shot = {"beat_id": span["beat_id"], "start": round(start, 3), "end": round(end, 3),
                    "command": cmd, "motion": s.get("motion", "NONE"),
                    "speed": s.get("speed", 1.0),
                    "transition_in": s.get("transition_in", "CROSSFADE")}
            if cmd in ("NEW_IMAGE", "ATMOSPHERIC_BROLL", "SHOW_DOCUMENT"):
                a = assets.get(s.get("asset_id") or "")
                if a is not None and not _still_usable(a):
                    # reviewed after planning (rejected / rights changed):
                    # another candidate of the beat, else keep the picture
                    alt = next((assets[c] for c in (plan.get("candidates") or {}).get(
                        span["beat_id"], []) if c in assets and _still_usable(assets[c])
                        and c not in {x.get("asset_id") for x in shots[-2:]}), None)
                    if alt is None and shots:
                        shots[-1]["end"] = round(end, 3)
                        continue
                    a = alt
                if a is None:
                    shot.update({"command": "BLACK_SCREEN", "kind": "black", "motion": "NONE"})
                else:
                    shot.update({"kind": "document" if cmd == "SHOW_DOCUMENT" else "image",
                                 **_asset_info(a)})
                    if cmd != "SHOW_DOCUMENT":
                        shot["alternatives"] = [
                            _asset_info(assets[c]) for c in
                            (plan.get("candidates") or {}).get(span["beat_id"], [])
                            if c in assets and c != a.asset_code
                            and assets[c].asset_type == "photo"][:4]
                    if R.needs_attribution(a.rights_status) and a.credit:
                        credits.add(a.credit)
                        add_overlay("credit", a.credit, start, end)
                    if s.get("label") == "illustration":
                        add_overlay("label", LABELS["illustration"].get(language, ""), start, end)
                    current = shot
            elif cmd in ("CROP_EXISTING", "ZOOM_EXISTING") and current:
                shot.update({k: current[k] for k in current
                             if k not in ("start", "end", "command", "motion", "speed",
                                          "transition_in", "beat_id")})
                shot["motion"] = "CROP_FOCUS"
            elif cmd == "SHOW_MAP":
                maps = [assets[c] for c in s.get("map_assets") or [] if c in assets]
                if not maps:
                    shot.update({"command": "BLACK_SCREEN", "kind": "black", "motion": "NONE"})
                else:
                    shot.update({"kind": "map", "map_paths": [m.local_path for m in maps],
                                 "marker": json.loads(maps[-1].spec_json or "{}").get("marker"),
                                 "asset_id": maps[-1].asset_code, "path": maps[-1].local_path})
                    credits.add(ai_config.maps.attribution)
                    add_overlay("credit", ai_config.maps.attribution, start, end)
                    add_overlay("place", texts.get(text_key), start + 1.0, min(start + 6.0, end - 0.5))
                    current = shot
            elif cmd == "SHOW_QUOTE":
                shot.update({"kind": "black", "motion": "NONE"})
                add_overlay("quote", texts.get(text_key), start + 0.3, end - 0.3)
                current = None
            elif cmd == "SHOW_DATE":
                shot.update({"kind": "black", "motion": "NONE"})
            else:  # BLACK_SCREEN, or reframing with nothing on screen
                shot.update({"kind": "black", "motion": "NONE", "command": "BLACK_SCREEN"})
                current = None
            shots.append(shot)
    # A picture shorter than a real hold in THIS language's timing is
    # folded into the previous one (no flash cuts).
    merged: list[dict] = []
    for sh in shots:
        if (merged and sh["end"] - sh["start"] < motion_cfg.min_hold_seconds * 0.7
                and sh.get("kind") == "image" and merged[-1].get("kind") in ("image", "map")):
            merged[-1]["end"] = sh["end"]
            continue
        merged.append(sh)
    if (len(merged) > 1 and merged[0]["end"] - merged[0]["start"] < motion_cfg.min_hold_seconds * 0.7
            and merged[0].get("kind") == "image" and merged[1].get("kind") == "image"):
        merged[1]["start"] = merged[0]["start"]
        merged.pop(0)
    shots = merged
    # Very long stills: first another picture the director considered for
    # the same beat, only then a reframe — and the reframe's move varies,
    # so no "image, then punch-in" template appears.
    final = []
    reframe_moves = ("CROP_FOCUS", "PAN_RIGHT", "SLOW_PULL", "PAN_LEFT")
    for sh in shots:
        length = sh["end"] - sh["start"]
        if sh.get("kind") == "image" and length > motion_cfg.max_still_seconds * 2:
            mid = round(sh["start"] + length * 0.55, 3)
            a, b = dict(sh), dict(sh)
            a["end"] = mid
            used = {x.get("asset_id") for x in final[-2:]} | {sh.get("asset_id")}
            alt = next((x for x in sh.get("alternatives") or [] if x["asset_id"] not in used), None)
            if alt:
                b.update({**alt, "start": mid, "command": "NEW_IMAGE",
                          "motion": reframe_moves[(len(final) + 1) % 4],
                          "transition_in": "CROSSFADE"})
            else:
                b.update({"start": mid, "command": "CROP_EXISTING",
                          "motion": reframe_moves[len(final) % 4],
                          "transition_in": "CROSSFADE", "reframe": True})
            final += [a, b]
        else:
            final.append(sh)
    for n, sh in enumerate(final):
        sh["index"] = n
        if n == 0:
            sh["transition_in"] = "FADE_BLACK"
    blocks = manifest["timeline"].get("blocks") or []
    silences = [
        {"start": round(b["end"], 3), "duration": round(n["start"] - b["end"], 3),
         "kind": b.get("pause_after_kind")}
        for b, n in zip(blocks, blocks[1:]) if n["start"] - b["end"] > 0.3
    ]
    return {
        "language": language,
        "duration": round(duration, 3),
        "width": ai_config.render.width, "height": ai_config.render.height,
        "fps": ai_config.render.fps,
        "beats": [{"beat_id": s["beat_id"], "start": s["start"], "end": s["end"]} for s in spans],
        "voice": [{"block_id": b["block_id"], "start": b["start"], "end": b["end"]} for b in blocks],
        "silences": silences,
        "shots": final,
        "overlays": sorted(overlays, key=lambda o: o["start"]),
        "music": [{k: p.get(k) for k in ("role", "cue_id", "mood", "start", "duration", "level_db")}
                  for p in (manifest.get("mix") or {}).get("placements", [])],
        "subtitles": _subtitles(manifest["timeline"].get("words") or [], language),
        "credits": sorted(credits),
    }


def overlay_texts(plan: dict) -> dict[str, str]:
    items = {}
    for pb in plan.get("beats") or []:
        for s in pb["shots"]:
            ov = s.get("overlay")
            if ov and ov.get("text_en"):
                items[f"{ov['kind']}|{ov['text_en']}"] = ov["text_en"]
    return items


async def localized_plan_texts(db: Session, version: StoryVersion,
                               plan_row: VisualPlan) -> dict[str, str]:
    """On-screen texts of the plan in the version's language, cached on
    the plan (one localizer call per language, reused by every render)."""
    language = version.language or "en"
    plan = json.loads(plan_row.plan_json or "{}")
    items = overlay_texts(plan)
    cache = plan.get("texts", {}).get(language, {})
    if all(k in cache for k in items):
        return {k: cache[k] for k in items}
    keys = {f"{'date' if k.startswith('date|') else 'text'}{i}": k
            for i, k in enumerate(items)}
    excerpt = " ".join(s["text"] for s in stored_sections(version))[:3000]
    localized = await localize_texts(db, version.case_id,
                                     {short: items[k] for short, k in keys.items()},
                                     language, excerpt) if items else {}
    texts = {k: localized.get(short, items[k]) for short, k in keys.items()}
    plan.setdefault("texts", {})[language] = texts
    plan_row.plan_json = json.dumps(plan, ensure_ascii=False)
    db.commit()
    return texts


async def build_production_script(db: Session, version: StoryVersion, plan_row: VisualPlan,
                                  manifest: dict, mode: str = "pilot") -> ProductionScript:
    language = version.language or "en"
    texts = await localized_plan_texts(db, version, plan_row)
    plan = json.loads(plan_row.plan_json or "{}")
    assets = {a.asset_code: a for a in db.query(VisualAsset).filter(
        VisualAsset.case_id == version.case_id).all()}
    script = compose(manifest, plan, assets, texts, language)
    script["audio"] = {"path": (manifest.get("mix") or {}).get("files", {}).get("documentary_wav")
                       or manifest["files"].get("narration_wav")}
    count = db.query(ProductionScript).filter(
        ProductionScript.story_version_id == version.id).count()
    row = ProductionScript(
        case_id=version.case_id, story_version_id=version.id, language=language,
        blueprint_id=plan_row.blueprint_id, visual_plan_id=plan_row.id,
        version=count + 1, mode=mode, status="composed",
        duration_seconds=script["duration"],
        script_json=json.dumps(script, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    out = storage.production_dir(version.case_id, language) / f"production_v{version.id}_{row.version}.json"
    Path(out).write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
    return row
