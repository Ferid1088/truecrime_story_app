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


def _chunks(text: str, max_chars: int) -> list[str]:
    """Split a sentence into subtitle lines of at most ~max_chars,
    preferring breaks after commas, never inside a word."""
    words = text.split()
    out, cur = [], []
    for w in words:
        if cur and len(" ".join(cur + [w])) > max_chars:
            # step back to a comma inside the current line if there is one
            cut = max((k for k, x in enumerate(cur[:-1]) if x[-1:] in ",،;:"), default=None)
            if cut is not None and cut >= len(cur) // 2:
                out.append(" ".join(cur[:cut + 1]))
                cur = cur[cut + 1:]
            else:
                out.append(" ".join(cur))
                cur = []
        cur.append(w)
    if cur:
        out.append(" ".join(cur))
    # no orphan word at the end
    if len(out) > 1 and len(out[-1].split()) == 1:
        last = out.pop()
        out[-1] = out[-1] + " " + last
    return out


def sentence_subtitles(sentences: list[dict], words: list[dict]) -> list[dict]:
    """Subtitles from the narration's sentences: the DISPLAY text (no
    audio tags, no emphasis capitals; Persian script for Finglish
    narration), timed by the sentence and — where the spoken words line
    up — by the words inside it."""
    cfg = ai_config.render
    out = []
    for sn in sentences:
        text = " ".join((sn.get("display") or sn.get("speech") or "").split())
        if not text:
            continue
        start, end = float(sn["start"]), float(sn["end"])
        lines = _chunks(text, cfg.subtitle_max_chars * 2)
        inside = [w for w in words if start - 0.05 <= w["start"] <= end + 0.05]
        same = (len(inside) == len(text.split())
                and (sn.get("display") or "") == (sn.get("speech") or ""))
        pos = 0
        total_chars = max(sum(len(x) for x in lines), 1)
        acc = 0
        for line in lines:
            n = len(line.split())
            if same:
                a, b = inside[pos]["start"], inside[pos + n - 1]["end"]
            else:
                a = start + (end - start) * acc / total_chars
                b = start + (end - start) * (acc + len(line)) / total_chars
            out.append({"start": round(a, 3), "end": round(max(b, a + 0.4), 3), "text": line})
            pos += n
            acc += len(line)
    # never overlap
    for x, y in zip(out, out[1:]):
        if x["end"] > y["start"]:
            x["end"] = round(max(x["start"] + 0.2, y["start"] - 0.02), 3)
    return out


def display_words(manifest: dict) -> list[dict]:
    """Timed words in the script people read: the alignment words, or —
    when the narration is read from another script (Finglish) — the
    display sentences' words spread over each sentence."""
    tl = manifest.get("timeline") or {}
    sentences = tl.get("sentences") or []
    if not sentences or all((sn.get("display") or "") == (sn.get("speech") or "")
                            for sn in sentences):
        return tl.get("words") or []
    out = []
    for sn in sentences:
        ws = (sn.get("display") or sn.get("speech") or "").split()
        if not ws:
            continue
        step = (sn["end"] - sn["start"]) / len(ws)
        for k, w in enumerate(ws):
            out.append({"word": w, "start": round(sn["start"] + k * step, 3),
                        "end": round(sn["start"] + (k + 1) * step, 3)})
    return out


_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _spoken_at(words: list[dict], text_en: str, start: float, end: float,
               sentences: list[dict] | None = None) -> float | None:
    """When the narration says the year of a date (any language), so the
    date appears exactly then. Narration that says years as words
    (Finglish) is matched through the sentence's display text."""
    import re

    years = re.findall(r"\b(1[89]\d\d|20\d\d)\b", text_en or "")
    if not years:
        return None
    for w in words:
        if start - 0.5 <= w["start"] <= end and years[0] in w["word"].translate(_DIGITS):
            return w["start"]
    for sn in sentences or []:
        disp = (sn.get("display") or "").translate(_DIGITS)
        if start - 0.5 <= sn["start"] <= end and years[0] in disp:
            # proportional position of the year inside the sentence
            k = disp.find(years[0]) / max(len(disp), 1)
            return round(sn["start"] + (sn["end"] - sn["start"]) * k, 3)
    return None


def _shows_investigation(a: VisualAsset | None) -> bool:
    if a is None:
        return False
    ver = json.loads(a.verification_json or "{}") if a.verification_json else {}
    text = " ".join(str(x or "") for x in (a.description, a.title, a.caption, a.found_for,
                                            ver.get("depicts"))).lower()
    return any(t in text for t in ai_config.attention.investigation_terms)


_CUT_PATTERN = (11.0, 15.0, 12.5, 16.0, 10.0, 14.0)


def plan_cuts(start: float, end: float, sentence_starts: list[float],
              seed: int = 0) -> list[float]:
    """Cut times between start and end like an editor: lengths vary
    (never a metronome) and each cut lands where a sentence begins when
    one is near (±4 s)."""
    cuts, t, k = [], start, seed
    while True:
        target = t + _CUT_PATTERN[k % len(_CUT_PATTERN)]
        k += 1
        if end - target < 7.0:
            break
        near = [x for x in sentence_starts if abs(x - target) <= 4.0 and x - t >= 7.0
                and end - x >= 7.0]
        cut = min(near, key=lambda x: abs(x - target)) if near else target
        cuts.append(round(cut, 3))
        t = cut
    return cuts


def _cap_black(shots: list[dict], plan: dict, assets: dict[str, VisualAsset],
               order: list[str], before_incident: set[str], max_black: float,
               sentence_starts: list[float] | None = None,
               no_investigation: set[str] | None = None) -> list[dict]:
    """A black screen stays a short pause: a black run longer than
    max_black keeps its first seconds and then shows a picture the story
    has already earned — one already shown, or a checked candidate of this
    or an earlier beat (never a later reveal)."""
    if max_black <= 0:
        return shots
    cands = plan.get("candidates") or {}
    out: list[dict] = []
    moves = ("SLOW_PUSH", "PAN_RIGHT", "SLOW_PULL", "PAN_LEFT")
    for sh in shots:
        length = sh["end"] - sh["start"]
        if sh.get("kind") != "black" or length <= max_black + 1.0:
            out.append(sh)
            continue
        if out and out[-1].get("kind") == "black":
            # continuing black: already paused long enough
            cut = sh["start"]
        else:
            cut = round(sh["start"] + max_black, 3)
        bi = order.index(sh["beat_id"]) if sh["beat_id"] in order else len(order)
        earned = [c for b in order[:bi + 1] for c in cands.get(b, [])]
        earned += [x.get("asset_id") for x in out if x.get("kind") == "image"]
        pool: list[VisualAsset] = []
        for code in dict.fromkeys(earned):  # keep order, no duplicates
            a = assets.get(code or "")
            if (a is None or not _still_usable(a) or a.asset_type != "photo"
                    or (sh["beat_id"] in (no_investigation or before_incident)
                        and _shows_investigation(a))):
                continue
            pool.append(a)
        if not pool:
            out.append(sh)
            continue
        if sh["end"] - cut < ai_config.motion.min_hold_seconds:
            out.append(sh)  # too little time for a picture: stay black
            continue
        if cut > sh["start"]:
            out.append({**sh, "end": cut})
        # rotate through the earned pictures, one per ~12 s, never the same
        # picture twice in a row and least recently shown first
        bounds = [cut] + plan_cuts(cut, sh["end"], sentence_starts or [], len(out)) + [sh["end"]]
        n = len(bounds) - 1
        t = cut
        for k in range(n):
            recent = [x.get("asset_id") for x in out[-3:]]
            pick = next((a for a in pool if a.asset_code not in recent), None) or next(
                (a for a in pool if a.asset_code != recent[-1]), pool[0])
            pool.remove(pick)
            pool.append(pick)  # least recently used moves to the back
            end = bounds[k + 1]
            out.append({**{k2: v for k2, v in sh.items() if k2 not in ("kind", "command", "motion")},
                        "start": round(t, 3), "end": end, "command": "NEW_IMAGE",
                        "kind": "image", "motion": moves[len(out) % 4],
                        "transition_in": "CROSSFADE", "black_filled": True,
                        **_asset_info(pick)})
            t = end
    # merge consecutive fills of the same picture
    merged: list[dict] = []
    for sh in out:
        if (merged and sh.get("black_filled") and merged[-1].get("black_filled")
                and merged[-1].get("asset_id") == sh.get("asset_id")):
            merged[-1]["end"] = sh["end"]
            continue
        merged.append(sh)
    return merged


def _earned_pool(beat_id: str, out: list[dict], plan: dict, assets: dict[str, VisualAsset],
                 order: list[str], before_incident: set[str]) -> list[VisualAsset]:
    """Pictures the story has earned at this beat: candidates of this or
    an earlier beat, and pictures already shown (never a later reveal)."""
    cands = plan.get("candidates") or {}
    bi = order.index(beat_id) if beat_id in order else len(order)
    earned = [c for b in order[:bi + 1] for c in cands.get(b, [])]
    earned += [x.get("asset_id") for x in out if x.get("kind") == "image"]
    pool = []
    for code in dict.fromkeys(earned):
        a = assets.get(code or "")
        if (a is None or not _still_usable(a) or a.asset_type != "photo"
                or (beat_id in before_incident and _shows_investigation(a))):
            continue
        pool.append(a)
    return pool


def _vary_long_holds(shots: list[dict], plan: dict, assets: dict[str, VisualAsset],
                     order: list[str], before_incident: set[str],
                     sentence_starts: list[float] | None = None,
                     no_investigation: set[str] | None = None) -> list[dict]:
    """One picture held (or reframed) much longer than max_still becomes a
    sequence: the picture first, then other earned pictures every ~13 s,
    least recently shown first; reframes only when there is nothing else."""
    max_still = ai_config.motion.max_still_seconds
    moves = ("SLOW_PUSH", "PAN_RIGHT", "SLOW_PULL", "PAN_LEFT", "CROP_FOCUS")
    # runs of consecutive shots of the same picture
    runs: list[list[dict]] = []
    for sh in shots:
        if (runs and sh.get("kind") in ("image", "map")
                and runs[-1][0].get("kind") == sh.get("kind")
                and sh.get("asset_id") == runs[-1][0].get("asset_id")):
            runs[-1].append(sh)
        else:
            runs.append([sh])
    final: list[dict] = []
    for run in runs:
        first = run[0]
        total = run[-1]["end"] - first["start"]
        if first.get("kind") == "map" and total > max_still * 2 and len(first.get("map_paths") or []) > 1:
            # a long map: the zoom levels take turns (wide, close, middle ...)
            paths = first["map_paths"]
            bounds = [first["start"]] + plan_cuts(first["start"], run[-1]["end"],
                                                  sentence_starts or [], 1) + [run[-1]["end"]]
            n = len(bounds) - 1
            t = first["start"]
            for k in range(n):
                seg_end = bounds[k + 1]
                seg = {**first, "start": round(t, 3), "end": seg_end}
                if k:
                    seg.update({"path": paths[(len(paths) - 1 - k) % len(paths)],
                                "map_paths": [paths[(len(paths) - 1 - k) % len(paths)]],
                                "motion": ("SLOW_PULL", "PAN_RIGHT", "SLOW_PUSH", "PAN_LEFT")[k % 4],
                                "command": "SHOW_MAP", "transition_in": "CROSSFADE"})
                final.append(seg)
                t = seg_end
            continue
        if first.get("kind") != "image" or total <= max_still * 2:
            final.extend(run)
            continue
        lead_end = round(first["start"] + min(max(first["end"] - first["start"], 10.0),
                                              max_still), 3)
        near = [x for x in sentence_starts or [] if abs(x - lead_end) <= 3.0
                and x - first["start"] >= 7.0]
        if near:
            lead_end = round(min(near, key=lambda x: abs(x - lead_end)), 3)
        final.append({**first, "end": lead_end})
        guard = no_investigation or before_incident
        pool = [a for a in _earned_pool(first["beat_id"], final, plan, assets, order,
                                        guard) if a.asset_code != first.get("asset_id")]
        alts = [x for x in first.get("alternatives") or []
                if x.get("asset_id") != first.get("asset_id")]
        bounds = [lead_end] + plan_cuts(lead_end, run[-1]["end"], sentence_starts or [],
                                        len(final)) + [run[-1]["end"]]
        n = len(bounds) - 1
        t = lead_end
        for k in range(n):
            seg_end = bounds[k + 1]
            # the beat the segment falls in (for firewall / incident rules)
            beat = next((x["beat_id"] for x in run if x["start"] <= t < x["end"]), first["beat_id"])
            recent = [x.get("asset_id") for x in final[-2:]]
            pick = next((a for a in pool if a.asset_code not in recent
                         and not (beat in guard and _shows_investigation(a))), None)
            if pick is not None:
                pool.remove(pick)
                pool.append(pick)
                info = _asset_info(pick)
            else:
                alt = next((x for x in alts if x["asset_id"] not in recent), None)
                if alt is None and first.get("asset_id") not in recent:
                    # back to the first picture, with another move
                    alt = {k: first[k] for k in ("asset_id", "path", "type", "role", "width",
                                                 "height", "focus", "rights", "credit",
                                                 "subject_type", "highlight", "marker")
                           if k in first}
                info = alt or {}
            seg = {**first, **info, "start": round(t, 3), "end": seg_end, "beat_id": beat,
                   "command": "NEW_IMAGE" if info else "CROP_EXISTING",
                   "motion": moves[len(final) % len(moves)], "transition_in": "CROSSFADE"}
            if not info:
                seg["reframe"] = True
            final.append(seg)
            t = seg_end
    return final


def compose(manifest: dict, plan: dict, assets: dict[str, VisualAsset],
            texts: dict[str, str], language: str, incident_beat: str | None = None) -> dict:
    """Pure function: manifest (audio timeline) + visual plan + localized
    texts -> production timeline. `incident_beat`: where the story's first
    incident happens (voice performance arc) — no investigation pictures
    before it."""
    motion_cfg = ai_config.motion
    att = ai_config.attention
    duration = float(manifest.get("duration_seconds") or 0.0)
    spans = sorted(manifest["timeline"].get("beats") or [], key=lambda b: b["start"])
    plan_beats = {b["beat_id"]: b for b in plan.get("beats") or []}
    words = manifest["timeline"].get("words") or []
    shots: list[dict] = []
    overlays: list[dict] = []
    credits: set[str] = set()
    current: dict | None = None  # last image shot

    def add_overlay(kind, text, start, end):
        if not text or end - start <= 0.5:
            return
        if kind in ("date", "place") and any(
                o["kind"] == kind and o["text"] == text
                and start - o["start"] < att.repeat_overlay_seconds for o in overlays):
            return  # the viewer has just read it
        overlays.append({"kind": kind, "text": text, "start": round(start, 3),
                         "end": round(min(end, duration), 3)})

    order = [s["beat_id"] for s in spans]
    before_incident = set(order[:order.index(incident_beat)]) if incident_beat in order else set()

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
                said = _spoken_at(words, s.get("overlay", {}).get("text_en"), w_start, w_end,
                                  manifest["timeline"].get("sentences"))
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
                if span["beat_id"] in before_incident and _shows_investigation(a):
                    # a search/police picture would give the story away
                    alt = next((assets[c] for c in (plan.get("candidates") or {}).get(
                        span["beat_id"], []) if c in assets and _still_usable(assets[c])
                        and not _shows_investigation(assets[c])
                        and c not in {x.get("asset_id") for x in shots[-2:]}), None)
                    if alt is None and shots:
                        shots[-1]["end"] = round(end, 3)
                        continue
                    a = alt
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
    # a short picture after a black frame or a card gives its time to the
    # next picture instead (still no flash cut)
    short = motion_cfg.min_hold_seconds * 0.7
    folded: list[dict] = []
    for k, sh in enumerate(merged):
        nxt = merged[k + 1] if k + 1 < len(merged) else None
        if (sh.get("kind") == "image" and sh["end"] - sh["start"] < short
                and nxt is not None and nxt.get("kind") in ("image", "map")):
            nxt["start"] = sh["start"]
            continue
        folded.append(sh)
    shots = folded
    starts = [float(x["start"]) for x in manifest["timeline"].get("sentences") or []]
    # pictures the editor adds (fills, sequences) never show the
    # investigation before the incident — nor during the incident beat,
    # where the story is still before the moment it happens
    guard = before_incident | ({incident_beat} if incident_beat in order else set())
    shots = _cap_black(shots, plan, assets, order, before_incident, att.max_black_seconds,
                       starts, guard)
    final = _vary_long_holds(shots, plan, assets, order, before_incident, starts, guard)
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
        "subtitles": (sentence_subtitles(manifest["timeline"]["sentences"],
                                         manifest["timeline"].get("words") or [])
                      if manifest["timeline"].get("sentences")
                      else _subtitles(manifest["timeline"].get("words") or [], language)),
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
    # Other languages may have stored their texts while we waited for the
    # model (language branches run in parallel): merge into the CURRENT plan.
    db.refresh(plan_row)
    plan = json.loads(plan_row.plan_json or "{}")
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
    from app.documentary.voice_performance import latest_performance

    perf = latest_performance(db, version.id)
    incident = json.loads(perf.performance_json or "{}").get("incident_beat") if perf else None
    script = compose(manifest, plan, assets, texts, language, incident_beat=incident)
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
