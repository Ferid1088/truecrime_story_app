"""Production script per language (Parts 35–37): the render-ready
timeline composed from the REAL narration audio of that language (beat
and word times), the language-independent visual plan and the music mix.

Each language gets its own exact timing — sentence lengths, pauses and
voice cadence differ — while the editorial plan stays shared.

Editing rules applied here (Master task §9):
  * cut rhythm from visual_direction.cut_pattern / min_cut_seconds,
    every cut snapped to a sentence start when one is near;
  * SHOW_CLIP becomes a "video" shot (the muted clip, its window, no
    camera move — footage moves by itself);
  * repetition control: every path that puts a picture on screen (the
    director's shots, swaps of pictures that became unusable, black
    fills, long-hold sequences, clip overruns) asks one UsageTracker
    (visuals/usage.py) — an unused picture first, a reuse only when the
    sentence names what it shows or nothing else exists — and each
    appearance is stored as a MediaUsage row with its reason;
  * an UNSOLVED case carries a status card at the start and near the
    end; a follow-up opens with its "now solved" card.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.story import stored_sections
from app.core.ai_config import ai_config
from app.db.models import (
    Case, EditorialBlueprint, MusicUsage, ProductionScript, StoryVersion, VisualAsset, VisualPlan,
)
from app.documentary import storage
from app.documentary.visuals import rights as R
from app.documentary.visuals import spoilers as SP
from app.documentary.visuals.generated import LABELS, localize_texts
from app.documentary.visuals.usage import (
    UsageTracker, annotate, asset_facts, named_between, record_media_usage,
)

IMAGE_COMMANDS = {"NEW_IMAGE", "ATMOSPHERIC_BROLL", "CROP_EXISTING", "ZOOM_EXISTING"}
# A cut moves at most this far to land where a sentence begins.
CUT_SNAP_SECONDS = 3.0
# The closing status card ends this long before the film (the picture
# fades out over the last 1.5 s).
STATUS_END_MARGIN_SECONDS = 1.0
# Fill/repeat bookkeeping keys that never travel to a shot made from another.
_PER_SHOT = ("fill_reason", "repeat_reason", "repeat_justified", "usage", "why",
             "black_filled", "reframe", "held_reason")


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


def _clip_length(a: VisualAsset) -> float:
    lo = float(a.clip_start or 0.0)
    hi = a.clip_end if a.clip_end is not None else a.duration_seconds
    return float(hi) - lo if hi is not None else 0.0


def _fits(a: VisualAsset | None, seconds: float) -> bool:
    """A photo fits any segment; a clip only one it can fill (it plays once)."""
    return a is not None and (a.asset_type != "video" or _clip_length(a) >= seconds - 0.5)


def _video_first(assets: dict[str, VisualAsset]):
    """tracker.pick preference: a real clip before a photo of the same tier."""
    return lambda c: 0 if (assets.get(c) is not None and assets[c].asset_type == "video") else 1


def _media(a: VisualAsset) -> dict:
    """Shot fields of a fill: a photo (moves) or a clip (plays as it is)."""
    info = _asset_info(a)
    if a.asset_type == "video":
        info.update(_clip_info(a))
    else:
        info.update({"kind": "image", "command": "NEW_IMAGE"})
    return info


def _asset_info(a: VisualAsset) -> dict:
    spec = json.loads(a.spec_json or "{}")
    ver = json.loads(a.verification_json or "{}") if a.verification_json else {}
    focus = _focus(a.local_path) if a.asset_type == "photo" else None
    facts = asset_facts(a)
    return {
        "focus": focus,
        "asset_id": a.asset_code, "path": a.local_path, "type": a.asset_type,
        "role": a.asset_role, "width": a.width, "height": a.height,
        "highlight": spec.get("highlight"), "marker": spec.get("marker"),
        "rights": a.rights_status, "credit": a.credit,
        "subject_type": a.subject_type or ver.get("subject_type"),
        # what repetition control needs (critics check a script alone)
        "tier": facts["tier"], "entity_type": facts["entity_type"],
        "entities": facts["entities"],
        **({"parent": facts["parent"], "window": facts["window"]}
           if facts.get("parent") else {}),
    }


def _clip_info(a: VisualAsset, s: dict | None = None) -> dict:
    """A footage shot: the muted clip and the window it plays (the
    director's start inside the stored window, else the whole window)."""
    lo = float(a.clip_start or 0.0)
    hi = a.clip_end if a.clip_end is not None else a.duration_seconds
    start = (s or {}).get("clip_start")
    try:
        start = max(lo, float(start)) if start is not None else lo
    except (TypeError, ValueError):
        start = lo
    end = (s or {}).get("clip_end")
    end = hi if end is None else end
    return {"kind": "video", "command": "SHOW_CLIP", "motion": "NONE",
            "clip_start": round(start, 3),
            "clip_end": round(float(end), 3) if end is not None else None}


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
    audio tags, no emphasis capitals, no pronunciation harakat), timed by the sentence and — where the spoken words line
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
    when the spoken text differs from the display text — the display
    sentences' words spread over each sentence."""
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
    date appears exactly then. Narration that says years as words is
    matched through the sentence's display text."""
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


def plan_cuts(start: float, end: float, sentence_starts: list[float],
              seed: int = 0) -> list[float]:
    """Cut times between start and end like an editor: lengths follow
    visual_direction.cut_pattern (never a metronome), no piece shorter
    than min_cut_seconds, and each cut lands where a sentence begins when
    one is near (±CUT_SNAP_SECONDS)."""
    vd = ai_config.visual_direction
    pattern = [float(x) for x in vd.cut_pattern if float(x) > 0] or [8.0]
    shortest = vd.min_cut_seconds
    cuts, t, k = [], start, seed
    while True:
        target = t + pattern[k % len(pattern)]
        k += 1
        if end - target < shortest:
            break
        near = [x for x in sentence_starts if abs(x - target) <= CUT_SNAP_SECONDS
                and x - t >= shortest and end - x >= shortest]
        cut = min(near, key=lambda x: abs(x - target)) if near else target
        cuts.append(round(cut, 3))
        t = cut
    return cuts


def _facts(assets: dict[str, VisualAsset]) -> dict[str, dict]:
    return {code: asset_facts(a) for code, a in assets.items()}


def _base(shot: dict) -> dict:
    """A shot's fields without its own bookkeeping (for a shot made from it)."""
    return {k: v for k, v in shot.items() if k not in _PER_SHOT}


def _repeat_fields(choice: dict) -> dict:
    if not choice.get("repeat"):
        return {}
    return {"repeat_justified": choice["repeat_justified"], "repeat_reason": choice["repeat_reason"]}


def _cap_black(shots: list[dict], plan: dict, assets: dict[str, VisualAsset],
               order: list[str], fw: SP.Firewall, max_black: float,
               sentence_starts: list[float] | None = None,
               tracker: UsageTracker | None = None,
               sentences: list[dict] | None = None) -> list[dict]:
    """A black screen stays a short pause: a black run longer than
    max_black keeps its first seconds and then shows a picture the story
    has already earned — a checked candidate of this or an earlier beat,
    or one already shown (never a later reveal). The usage tracker picks:
    an unused picture first; a reuse only when justified; when nothing
    may be shown the previous fill continues (or the pause stays black)."""
    if max_black <= 0:
        return shots
    tracker = tracker or UsageTracker.from_shots(shots, _facts(assets))
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
        earned += [x.get("asset_id") for x in out if x.get("kind") in ("image", "video")]
        pool: list[VisualAsset] = []
        for code in dict.fromkeys(earned):  # keep order, no duplicates
            a = assets.get(code or "")
            if (a is None or not _still_usable(a) or a.asset_type not in ("photo", "video")
                    or fw.blocks(sh["beat_id"], a)):
                continue
            pool.append(a)
        prev = out[-1] if out else None
        if ((not pool or tracker.pick([a.asset_code for a in pool], cut, sh["end"]) is None)
                and prev is not None and prev.get("kind") in ("image", "map", "document")
                and abs(prev["end"] - sh["start"]) <= 0.05
                and sh["end"] - max_black - prev["start"] > 0):
            # nothing the film has not shown yet (no picture twice): the
            # picture before the pause stays, the pause comes at its end
            pause_at = round(sh["end"] - max_black, 3)
            if prev.get("asset_id"):
                tracker.add(prev["asset_id"], prev["end"], pause_at)
            prev["end"] = pause_at
            prev["held_reason"] = "nothing else may be shown (no picture twice)"
            out.append({**sh, "start": pause_at})
            continue
        if not pool:
            out.append(sh)
            continue
        if sh["end"] - cut < ai_config.motion.min_hold_seconds:
            out.append(sh)  # too little time for a picture: stay black
            continue
        if cut > sh["start"]:
            out.append({**sh, "end": cut})
        # one picture per cut of the rhythm, never the same picture twice
        # in a row, unused pictures first
        bounds = [cut] + plan_cuts(cut, sh["end"], sentence_starts or [], len(out)) + [sh["end"]]
        t = cut
        for k in range(len(bounds) - 1):
            end = bounds[k + 1]
            # not one of the last pictures — else at least not the one
            # right before
            names = named_between(sentences or [], t, end)
            codes = [a.asset_code for a in pool if _fits(a, end - t)]
            choice = tracker.pick(codes, t, end, names,
                                  avoid={x.get("asset_id") for x in out[-3:]},
                                  prefer=_video_first(assets))
            if choice is None and out:
                choice = tracker.pick(codes, t, end, names, avoid={out[-1].get("asset_id")},
                                      prefer=_video_first(assets))
            if choice is None:
                prev = out[-1] if out else None
                if prev is not None and prev.get("black_filled") and prev["end"] >= t - 0.01:
                    prev["end"] = end  # the fill continues (same appearance)
                    tracker.add(prev["asset_id"], prev["start"], end)
                else:
                    out.append({**_base(sh), "start": round(t, 3), "end": end})
            else:
                pick = assets[choice["asset_id"]]
                tracker.add(pick.asset_code, t, end)
                out.append({**{k2: v for k2, v in _base(sh).items()
                               if k2 not in ("kind", "command", "motion")},
                            "start": round(t, 3), "end": end,
                            "motion": moves[len(out) % 4],
                            "transition_in": "CROSSFADE", "black_filled": True,
                            "fill_reason": (f"black pause capped at {max_black:g}s: "
                                            f"{choice['reason']}"),
                            **_repeat_fields(choice), **_media(pick)})
            t = end
    # merge consecutive pieces of one black pause / one fill
    merged: list[dict] = []
    for sh in out:
        prev = merged[-1] if merged else None
        if prev is not None and abs(prev["end"] - sh["start"]) <= 0.01 and (
                (sh.get("black_filled") and prev.get("black_filled")
                 and prev.get("asset_id") == sh.get("asset_id"))
                or (sh.get("kind") == "black" and prev.get("kind") == "black"
                    and not sh.get("black_filled") and prev.get("beat_id") == sh.get("beat_id")
                    and prev.get("command") == sh.get("command"))):
            prev["end"] = sh["end"]
            continue
        merged.append(sh)
    return merged


def _earned_pool(beat_id: str, out: list[dict], plan: dict, assets: dict[str, VisualAsset],
                 order: list[str], fw: SP.Firewall) -> list[VisualAsset]:
    """Pictures the story has earned at this beat: candidates of this or
    an earlier beat, and pictures already shown (never a later reveal)."""
    cands = plan.get("candidates") or {}
    bi = order.index(beat_id) if beat_id in order else len(order)
    earned = [c for b in order[:bi + 1] for c in cands.get(b, [])]
    earned += [x.get("asset_id") for x in out if x.get("kind") in ("image", "video")]
    pool = []
    for code in dict.fromkeys(earned):
        a = assets.get(code or "")
        if (a is None or not _still_usable(a) or a.asset_type not in ("photo", "video")
                or fw.blocks(beat_id, a)):
            continue
        pool.append(a)
    return pool


def _vary_long_holds(shots: list[dict], plan: dict, assets: dict[str, VisualAsset],
                     order: list[str], fw: SP.Firewall,
                     sentence_starts: list[float] | None = None,
                     tracker: UsageTracker | None = None,
                     sentences: list[dict] | None = None) -> list[dict]:
    """One picture held longer than motion.max_hold_seconds becomes a
    sequence: the picture first, then other earned pictures in the cut
    rhythm that the film has not shown yet (no picture appears twice);
    when nothing else may be shown, the picture on screen simply stays
    (one shot, one appearance). A map is orientation, not a backdrop:
    after motion.max_map_seconds the story's pictures take over; when none
    may be shown the map stays as one shot (its zoom levels are not cut
    in again)."""
    motion = ai_config.motion
    max_still, max_hold, max_map = (motion.max_still_seconds, motion.max_hold_seconds,
                                    motion.max_map_seconds)
    shortest = ai_config.visual_direction.min_cut_seconds
    tracker = tracker or UsageTracker.from_shots(shots, _facts(assets))
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
    for ri, run in enumerate(runs):
        first = run[0]
        run_end = run[-1]["end"]
        # the picture right after this run (never cut to it a moment early)
        nxt = runs[ri + 1][0].get("asset_id") if ri + 1 < len(runs) else None
        total = run_end - first["start"]
        is_map = first.get("kind") == "map"
        # a stand-in (generic / illustration, tier >= 4) is a short
        # moment, never a backdrop: it gets a map's budget
        stand_in = first.get("kind") == "image" and int(first.get("tier") or 3) >= 4
        if is_map or stand_in:
            lead_end = first["start"] + max_map
            min_lead = min(max_map, 7.0)
        else:
            lead_end = first["start"] + min(max(first["end"] - first["start"], 10.0), max_still)
            min_lead = 7.0
        if not (((is_map or stand_in) and total > max_map + shortest)
                or (first.get("kind") == "image" and total > max_hold)):
            final.extend(run)
            continue
        near = [x for x in sentence_starts or [] if abs(x - lead_end) <= 3.0
                and x - first["start"] >= min_lead and run_end - x >= shortest]
        if near:
            lead_end = min(near, key=lambda x: abs(x - lead_end))
        lead_end = round(lead_end, 3)
        if run_end - lead_end < shortest:
            final.extend(run)
            continue
        code0 = first.get("asset_id")
        if code0:
            tracker.discard(code0, first["start"], run_end)
            tracker.add(code0, first["start"], lead_end)
        final.append({**first, "end": lead_end})
        pool = [a.asset_code for a in _earned_pool(first["beat_id"], final, plan, assets,
                                                   order, fw)
                if a.asset_code != code0]
        alts = {x["asset_id"]: x for x in first.get("alternatives") or []
                if x.get("asset_id") and x.get("asset_id") != code0}
        options = list(dict.fromkeys(pool + list(alts) + ([code0] if code0 and not is_map
                                                          else [])))
        bounds = [lead_end] + plan_cuts(lead_end, run_end, sentence_starts or [],
                                        len(final)) + [run_end]
        t = lead_end
        for k in range(len(bounds) - 1):
            seg_end = bounds[k + 1]
            # the beat the segment falls in (for firewall / incident rules)
            beat = next((x["beat_id"] for x in run if x["start"] <= t < x["end"]), first["beat_id"])
            recent = {x.get("asset_id") for x in final[-2:]}
            last_seg = k == len(bounds) - 2
            if last_seg and nxt:
                recent.add(nxt)
            allowed = [c for c in options if not fw.blocks(beat, assets.get(c))
                       and (c not in assets or _fits(assets[c], seg_end - t))]
            choice = tracker.pick(allowed, t, seg_end, named_between(sentences or [], t, seg_end),
                                  avoid=recent, prefer=_video_first(assets))
            base = {**_base(first), "start": round(t, 3), "end": seg_end, "beat_id": beat,
                    "motion": moves[len(final) % len(moves)], "transition_in": "CROSSFADE"}
            if choice is not None:
                c = choice["asset_id"]
                info = (_media(assets[c]) if c in assets
                        else {**dict(alts.get(c) or {}), "kind": "image", "command": "NEW_IMAGE"})
                if is_map:  # a picture after the map: none of the map's fields
                    base = {k2: v for k2, v in base.items()
                            if k2 not in ("map_paths", "place", "marker", "held")}
                if info.get("kind") == "video":
                    base.pop("motion", None)
                seg = {**base, **info,
                       "fill_reason": (f"{'map' if is_map else 'long hold'} varied: "
                                       f"{choice['reason']}"),
                       **_repeat_fields(choice)}
                tracker.add(c, t, seg_end)
            else:
                # nothing else may be shown (every picture appears once):
                # the picture on screen stays — one shot, one appearance,
                # no cut to a crop of itself
                prev = final[-1]
                prev["end"] = seg_end
                if prev.get("asset_id"):
                    tracker.add(prev["asset_id"], t, seg_end)
                prev["held_reason"] = "nothing else may be shown (no picture twice)"
                t = seg_end
                continue
            final.append(seg)
            t = seg_end
    return final


def label_overlays(shots: list[dict], language: str, duration: float) -> list[dict]:
    """The 'illustration' label is on screen exactly while an
    illustration (a stand-in) is."""
    text = LABELS["illustration"].get(language) or LABELS["illustration"].get("en")
    out: list[dict] = []
    for sh in shots:
        if sh.get("role") != "illustration" or sh.get("kind") not in ("image", "video"):
            continue
        start, end = round(sh["start"], 3), round(min(sh["end"], duration), 3)
        if out and abs(out[-1]["end"] - start) <= 0.05:
            out[-1]["end"] = end
        elif end - start > 0.5:
            out.append({"kind": "label", "text": text, "start": start, "end": end})
    return out


def credit_overlays(shots: list[dict], duration: float) -> list[dict]:
    """Credits follow the final cut: each attributed picture (and every
    map) carries its credit exactly while it is on screen."""
    out: list[dict] = []
    for sh in shots:
        if sh.get("kind") == "map":
            text = ai_config.maps.attribution
        elif sh.get("credit") and R.needs_attribution(sh.get("rights")):
            text = sh["credit"]
        else:
            continue
        start, end = round(sh["start"], 3), round(min(sh["end"], duration), 3)
        if out and out[-1]["text"] == text and abs(out[-1]["end"] - start) <= 0.05:
            out[-1]["end"] = end
        elif end - start > 0.5:
            out.append({"kind": "credit", "text": text, "start": start, "end": end})
    return out


def _clip_overruns(shots: list[dict], plan: dict, assets: dict[str, VisualAsset],
                   order: list[str], fw: SP.Firewall, tracker: UsageTracker,
                   sentences: list[dict], sentence_starts: list[float]) -> list[dict]:
    """A clip plays once. When its shot runs much longer than the clip,
    the cut comes when the footage ends (at a sentence start when one is
    close) and an earned picture takes over — a frozen last frame is not
    a picture. Without an allowed picture the last frame holds."""
    hold = ai_config.motion.min_hold_seconds
    out: list[dict] = []
    for sh in shots:
        if sh.get("kind") != "video" or sh.get("clip_end") is None:
            out.append(sh)
            continue
        length = float(sh["clip_end"]) - float(sh.get("clip_start") or 0.0)
        if length <= 0 or sh["end"] - sh["start"] <= length + hold:
            out.append(sh)
            continue
        cut = round(sh["start"] + length, 3)
        near = [x for x in sentence_starts if sh["start"] + hold <= x <= cut and cut - x <= 3.0]
        if near:
            cut = round(max(near), 3)
        pool = [a.asset_code for a in _earned_pool(sh["beat_id"], out, plan, assets, order, fw)]
        choice = tracker.pick(pool, cut, sh["end"], named_between(sentences, cut, sh["end"]),
                              avoid={sh.get("asset_id")})
        if choice is None:
            out.append(sh)
            continue
        tracker.discard(sh["asset_id"], cut, sh["end"])
        out.append({**sh, "end": cut})
        pick = assets[choice["asset_id"]]
        tracker.add(pick.asset_code, cut, sh["end"])
        out.append({**{k: v for k, v in _base(sh).items()
                       if k not in ("clip_start", "clip_end", "kind", "command", "motion")},
                    "start": cut, "end": sh["end"], "command": "NEW_IMAGE", "kind": "image",
                    "motion": "SLOW_PUSH", "transition_in": "CROSSFADE",
                    "fill_reason": f"the clip ends: {choice['reason']}",
                    **_repeat_fields(choice), **_asset_info(pick)})
    return out


def sentence_spans(spans: list[dict], plan_beats: dict[str, dict],
                   duration: float) -> list[dict]:
    """The plan's sentences on THIS language's timeline, with the
    entities each names — placed by the fraction of the beat at which
    each sentence starts (the same anchors the shot shares use)."""
    out = []
    for i, span in enumerate(spans):
        w_start = 0.0 if i == 0 else span["start"]
        w_end = spans[i + 1]["start"] if i + 1 < len(spans) else duration
        sents = (plan_beats.get(span["beat_id"]) or {}).get("sentences") or []
        for k, sn in enumerate(sents):
            nxt = sents[k + 1]["at"] if k + 1 < len(sents) else 1.0
            out.append({"beat_id": span["beat_id"], "n": sn.get("n", k),
                        "start": round(w_start + (w_end - w_start) * sn.get("at", 0.0), 3),
                        "end": round(w_start + (w_end - w_start) * nxt, 3),
                        "entities": list(sn.get("entities") or [])})
    return out


def status_overlays(case_status: str | None, production_type: str | None,
                    language: str, duration: float) -> list[dict]:
    """The localized status card (youtube_metadata.status_labels): an
    UNSOLVED case shows it during status_card_seconds and again in the
    last status_card_end_seconds; a follow-up opens with "case now
    solved". Every other film has none."""
    ym = ai_config.youtube_metadata
    labels = ym.status_labels.get(language) or ym.status_labels.get("en") or {}
    window = list(ym.status_card_seconds or []) + [1.5, 7.5]
    a, b = float(window[0]), float(window[1])
    if production_type == "follow_up":
        text = labels.get("follow_up")
        return [{"kind": "status", "text": text, "start": a, "end": min(b, duration)}] \
            if text and duration > a else []
    if str(case_status or "").upper() != "UNSOLVED":
        return []
    text = labels.get("UNSOLVED")
    if not text or duration <= a:
        return []
    out = [{"kind": "status", "text": text, "start": a, "end": min(b, duration)}]
    tail = float(ym.status_card_end_seconds or 0.0)
    start = duration - tail
    if tail > 0 and start > b + 1.0:
        out.append({"kind": "status", "text": text, "start": round(start, 3),
                    "end": round(duration - STATUS_END_MARGIN_SECONDS, 3)})
    return out


# ---------------------------------------------------------------------------
# cards: chapters, the film title, the running timeline
# ---------------------------------------------------------------------------

CARD_KINDS = ("chapter", "title", "timeline")


def _ordinal(date: str) -> float:
    from datetime import date as _date

    from app.documentary.chapters import parse_date_text

    p = parse_date_text(date)
    if not p:
        return 0.0
    y, m, d = p
    try:
        return float(_date(y, m or 7, d or (15 if m else 1)).toordinal())
    except ValueError:
        return float(_date(y, 7, 1).toordinal())


_SHORT_MONTHS = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "de": ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sep.", "Okt.",
           "Nov.", "Dez."],
}
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _num(n: int, language: str) -> str:
    return str(n).translate(_FA_DIGITS) if language == "fa" else str(n)


def _short_date(p: tuple, language: str) -> str:
    """'10 Mar' / '10. März' / '۱۰ مارس' — a tick label inside one year."""
    from app.documentary.visuals.generated import MONTHS

    _, m, d = p
    if not m:
        return ""
    month = (_SHORT_MONTHS.get(language) or MONTHS.get(language) or MONTHS["en"])[m - 1]
    if not d:
        return month
    day = _num(d, language) + ("." if language == "de" else "")
    return f"{day} {month}"


def _spread(values: list[float], lo: float = 0.0, hi: float = 1.0) -> list[float]:
    """Half time scale, half order: close dates stay apart, far ones far."""
    m = len(values)
    if m == 1:
        return [(lo + hi) / 2]
    span = (max(values) - min(values)) or 1.0
    return [lo + (hi - lo) * (0.5 * (v - min(values)) / span + 0.5 * i / (m - 1))
            for i, v in enumerate(values)]


def timeline_layout(window: list[dict], language: str) -> tuple[dict[str, float], list[dict]]:
    """The points on the line and their labels — every point carries its
    label right under it, no point without one. Several years: ONE point
    per year (labelled with the year). All in one year: one point per day
    (labelled day and month). Time runs left to right in every language;
    positions are half time scale, half order. Returns (x of every event
    = the x of its point, points [{text, x, ids}])."""
    from app.documentary.chapters import parse_date_text

    dates = [parse_date_text(e["date"]) or (0, None, None) for e in window]
    one_year = len({d[0] for d in dates}) == 1
    groups: dict[str, dict] = {}
    for e, d in zip(window, dates, strict=True):
        key = (_short_date(d, language) or _num(d[0], language)) if one_year \
            else _num(d[0], language)
        g = groups.setdefault(key, {"text": key, "ids": [], "at": _ordinal(e["date"])})
        g["ids"].append(e["id"])
    points = list(groups.values())
    for g, x in zip(points, _spread([g["at"] for g in points]), strict=True):
        g["x"] = x
    xs = {i: g["x"] for g in points for i in g["ids"]}
    return xs, [{"text": g["text"], "x": g["x"], "ids": g["ids"]} for g in points]


def timeline_card(cards: dict | None, beat_id: str, event_id: str | None,
                  previous: str | None = None, language: str = "en") -> dict | None:
    """The running timeline at this beat, moving to `event_id`: only the
    events the story has told by this beat (never a later one), at most
    chapters.max_timeline_events around the current one, laid out by
    timeline_layout (left to right, one labelled point per year or day);
    the marker slides
    from the previous card's date (or the previous event) to the current
    one. None when the event is not told yet or unknown — the caller
    shows the date over the picture instead."""
    cards = cards or {}
    order = cards.get("beat_order") or []
    pos = {b: i for i, b in enumerate(order)}
    if beat_id not in pos or not event_id:
        return None
    told = sorted((e for e in cards.get("events") or []
                   if pos.get(e["first_beat"], 10 ** 9) <= pos[beat_id]),
                  key=lambda e: (e["date"], e["id"]))
    cur = next((e for e in told if e["id"] == event_id), None)
    if cur is None:
        return None
    # what this same beat tells only later (a later date) is not shown yet
    told = [e for e in told if pos[e["first_beat"]] < pos[beat_id]
            or e["id"] == cur["id"] or e["date"] <= cur["date"]]
    n = ai_config.chapters.max_timeline_events
    k = told.index(cur)
    lo = max(0, min(k - n // 2, len(told) - n))
    window = told[lo:lo + n]
    xs, points = timeline_layout(window, language)
    i = window.index(cur)
    prev = next((e for e in window if e["id"] == previous and e["id"] != cur["id"]), None) \
        or (window[i - 1] if i > 0 else None)
    return {"event": cur["id"], "date": cur.get("date_text") or cur["date"],
            "label": cur.get("label"),
            # one ball per point (the current event's where it is), its label under it
            "events": [{"id": cur["id"] if cur["id"] in pt["ids"] else pt["ids"][0],
                        "x": round(pt["x"], 4), "current": cur["id"] in pt["ids"]}
                       for pt in points],
            "labels": [{"text": pt["text"], "x": round(pt["x"], 4),
                        "current": cur["id"] in pt["ids"]} for pt in points],
            "from_x": round(xs[prev["id"]], 4) if prev else 0.0,
            "to_x": round(xs[cur["id"]], 4)}


def insert_chapter_cards(shots: list[dict], cards: dict | None, spans: list[dict],
                         overlays: list[dict]) -> tuple[list[dict], dict | None]:
    """The channel intro and the film title (after a cold open) and each
    chapter's card at the END of the gap before the chapter (a
    chapter_break in the audio plan): the first picture of the new chapter
    comes chapters.lead_out_seconds before its first word. Pictures under
    a card are cut back (a clip split by a card plays on after it); text
    over a card is dropped. When a gap is too short, the film title goes
    first, then the chapter card, the intro last.

    Returns (what could not be placed — reported, where the intro plays:
    {"mode": "gap", "start", "seconds"} | {"mode": "prepend", "seconds"}
    (before the first word, when the film has no cold open) | None)."""
    cfg = ai_config.chapters
    cards = cards or {}
    chapters = cards.get("chapters") or []
    intro_s = cards.get("intro_seconds")
    intro = None
    if not spans:
        return [], ({"mode": "prepend", "seconds": intro_s} if intro_s else None)
    by = {sp["beat_id"]: sp for sp in spans}
    ids = [sp["beat_id"] for sp in spans]
    order = cards.get("beat_order") or ids

    def chapter_item(ch):
        return {"kind": "chapter", "command": "CHAPTER_CARD", "chapter": ch["number"],
                "card": {"label": ch["label"], "title": ch.get("title")}, "drop": 1}

    skipped: list[dict] = []
    todo: list[tuple[float, float, list[dict], str, int | None]] = []
    for ch in chapters:
        first = ch["first_beat"]
        if first not in by:
            continue
        k = ids.index(first)
        if k == 0:
            continue  # the film starts with this chapter: no gap before it
        prev = ids[k - 1]
        if order.index(prev) != order.index(first) - 1:
            continue  # (a pilot film: the beat before is not in it)
        todo.append((by[prev]["end"], by[first]["start"], [chapter_item(ch)], first,
                     ch["number"]))
    cold = (cards.get("cold_open") and len(ids) > 1 and order and ids[0] == order[0])
    if cold:
        items = []
        if intro_s:
            items.append({"kind": "intro", "command": "CHANNEL_INTRO", "fixed": intro_s,
                          "drop": 2})
        if cards.get("film_title"):
            items.append({"kind": "title", "command": "TITLE_CARD",
                          "card": {"title": cards["film_title"]}, "drop": 0})
        if chapters and chapters[0]["first_beat"] == ids[0]:
            items.append(chapter_item(chapters[0]))
        if items:
            todo.append((by[ids[0]]["end"], by[ids[1]]["start"], items, ids[1],
                         chapters[0]["number"] if chapters else None))
    placed: list[tuple[float, float]] = []
    for gap_from, gap_to, items, beat, number in sorted(todo, key=lambda x: x[0]):
        end = gap_to - cfg.lead_out_seconds
        room = end - gap_from - 0.5

        def need(its):
            return sum(it.get("fixed") or cfg.min_card_seconds for it in its)

        while items and need(items) > room:
            items = [it for it in items if it is not min(items, key=lambda x: x["drop"])]
        if not items:
            skipped.append({"chapter": number, "at": round(gap_from, 2),
                            "why": f"gap of {gap_to - gap_from:.1f}s is too short for a card"})
            continue
        fixed = sum(it.get("fixed") or 0 for it in items)
        flex = [it for it in items if not it.get("fixed")]
        length = min(cfg.card_seconds, (room - fixed) / len(flex)) if flex else 0.0
        t = end - fixed - length * len(flex)
        for item in items:
            dur = item.get("fixed") or length
            card = {k: v for k, v in item.items() if k not in ("fixed", "drop")}
            card.update({"beat_id": beat, "start": round(t, 3), "end": round(t + dur, 3),
                         "motion": "NONE", "speed": 1.0,
                         # the intro brings its own fade from black
                         "transition_in": "NONE" if item["kind"] == "intro" else "FADE_BLACK",
                         "why": {"chapter": "chapter card in the chapter break",
                                 "title": "the film's title after the cold open",
                                 "intro": "the channel intro after the cold open"}[item["kind"]]})
            if item["kind"] == "intro":
                card["intro"] = {"seconds": dur}
                intro = {"mode": "gap", "start": card["start"], "seconds": dur}
            _carve(shots, card)
            placed.append((card["start"], card["end"]))
            t += dur
    if placed:
        overlays[:] = [o for o in overlays
                       if o["kind"] not in ("date", "place", "quote", "status")
                       or not any(o["start"] < b and o["end"] > a for a, b in placed)]
    if intro_s and intro is None:
        intro = {"mode": "prepend", "seconds": intro_s}
    return skipped, intro


def _carve(shots: list[dict], card: dict) -> None:
    """Put `card` on the timeline, cutting back what it covers."""
    a, b = card["start"], card["end"]
    out: list[dict] = []
    for sh in shots:
        if sh["end"] <= a or sh["start"] >= b:
            out.append(sh)
            continue
        if sh["start"] < a:
            head = dict(sh)
            head["end"] = round(a, 3)
            out.append(head)
        if sh["end"] > b and sh.get("kind") not in CARD_KINDS:
            tail = dict(sh)
            if tail.get("kind") == "video" and tail.get("clip_start") is not None:
                tail["clip_start"] = round(float(tail["clip_start"])
                                           + max(b - float(sh["start"]), 0.0), 3)
            tail["start"] = round(b, 3)
            tail["transition_in"] = "CROSSFADE"
            out.append(tail)
    out.append(card)
    out.sort(key=lambda x: (x["start"], x["end"]))
    # no flash fragments next to a card: before it they give their time to
    # the card; after it the next picture (the new chapter's) comes early —
    # the old picture never flashes up again after the card
    final: list[dict] = []
    for k, sh in enumerate(out):
        if sh is card:
            final.append(sh)
            continue
        short = sh["end"] - sh["start"] < 1.5
        if short and sh["end"] <= a + 1e-6 and sh["end"] - sh["start"] < 0.8:
            card["start"] = min(card["start"], sh["start"])
            continue
        nxt = out[k + 1] if k + 1 < len(out) else None
        if (short and abs(sh["start"] - b) < 1e-6 and nxt is not None
                and nxt.get("kind") not in CARD_KINDS):
            nxt["start"] = sh["start"]
            continue
        final.append(sh)
    shots[:] = final


def compose(manifest: dict, plan: dict, assets: dict[str, VisualAsset],
            texts: dict[str, str], language: str, incident_beat: str | None = None, *,
            case_status: str | None = None, production_type: str | None = "original",
            opening_strategy: str | None = None, arrest_beat: str | None = None,
            reveal_blocks: dict[str, list[str]] | None = None,
            cards: dict | None = None) -> dict:
    """Pure function: manifest (audio timeline) + visual plan + localized
    texts -> production timeline. `incident_beat`: where the story's first
    incident happens (voice performance arc) — no investigation pictures
    before it. `case_status` / `production_type`: the status card;
    `opening_strategy` travels with the script (cross-film variety)."""
    motion_cfg = ai_config.motion
    att = ai_config.attention
    duration = float(manifest.get("duration_seconds") or 0.0)
    spans = sorted(manifest["timeline"].get("beats") or [], key=lambda b: b["start"])
    plan_beats = {b["beat_id"]: b for b in plan.get("beats") or []}
    words = manifest["timeline"].get("words") or []
    cands = plan.get("candidates") or {}
    facts = _facts(assets)
    tracker = UsageTracker(facts)
    named_sents = sentence_spans(spans, plan_beats, duration)
    # pictures the director shows somewhere in the film: a replacement
    # prefers one the film does not plan to show anyway
    planned = {s.get("asset_id") for pb in plan_beats.values() for s in pb.get("shots") or []
               if s.get("asset_id")}
    shots: list[dict] = []
    overlays: list[dict] = []
    credits: set[str] = set()
    current: dict | None = None  # last image shot
    last_event: str | None = None  # where the running timeline stands

    def add_overlay(kind, text, start, end):
        if not text or end - start <= 0.5:
            return
        if kind in ("date", "place") and any(
                o["kind"] == kind and o["text"] == text
                and start - o["start"] < att.repeat_overlay_seconds for o in overlays):
            return  # the viewer has just read it
        overlays.append({"kind": kind, "text": text, "start": round(start, 3),
                         "end": round(min(end, duration), 3)})

    def extend_last(end: float):
        shots[-1]["end"] = round(end, 3)
        if shots[-1].get("asset_id") and shots[-1].get("kind") in ("image", "video", "map",
                                                                     "document"):
            tracker.add(shots[-1]["asset_id"], shots[-1]["start"], shots[-1]["end"])

    def pool(beat_id: str, exclude: set[str], test=None) -> list[str]:
        return [c for c in cands.get(beat_id, []) if c in assets and c not in exclude
                and _still_usable(assets[c]) and assets[c].asset_type in ("photo", "video")
                and (test is None or test(assets[c]))]

    order = [s["beat_id"] for s in spans]
    before_incident = SP.beats_before(order, incident_beat)
    # what the director's own pictures may not show (incident, arrest)
    # (and the claim firewall: no picture showing a fact the story reveals
    # only later — reveal_blocks: beat -> fact ids)
    planfw = SP.Firewall(before_incident, SP.beats_before(order, arrest_beat), reveal_blocks)

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
            timeline = None
            if cmd == "SHOW_TIMELINE":
                timeline = timeline_card(cards, span["beat_id"], s.get("event"), last_event,
                                         language)
                if timeline is None:
                    cmd = "SHOW_DATE"  # the date over the current picture instead
            if cmd == "SHOW_DATE":
                said = _spoken_at(words, s.get("overlay", {}).get("text_en"), w_start, w_end,
                                  manifest["timeline"].get("sentences"))
                at = said - 0.2 if said is not None else start + 0.4
                add_overlay("date", texts.get(text_key), at, at + 4.6)
            if cmd in ("KEEP_CURRENT_IMAGE", "NO_VISUAL_CHANGE", "SHOW_DATE") and shots:
                extend_last(end)
                continue
            shot = {"beat_id": span["beat_id"], "start": round(start, 3), "end": round(end, 3),
                    "command": cmd, "motion": s.get("motion", "NONE"),
                    "speed": s.get("speed", 1.0),
                    "transition_in": s.get("transition_in", "CROSSFADE")}
            if s.get("why"):
                shot["why"] = s["why"]
            if cmd in ("NEW_IMAGE", "ATMOSPHERIC_BROLL", "SHOW_DOCUMENT", "SHOW_CLIP"):
                a = assets.get(s.get("asset_id") or "")
                named = named_between(named_sents, start, end)
                recent = {x.get("asset_id") for x in shots[-2:]}
                fill_reason, repeat = None, None
                beat = span["beat_id"]

                def open_(x, beat=beat):
                    return not planfw.blocks(beat, x)

                spoiler = planfw.why(beat, a) if a is not None else None
                if spoiler:
                    # a search/police or a custody/court picture would give
                    # the story away here
                    choice = tracker.pick(pool(beat, recent, open_), start, end, named,
                                          reserved=planned)
                    if choice is None and shots:
                        extend_last(end)
                        continue
                    a = assets[choice["asset_id"]] if choice else None
                    if choice:
                        fill_reason = f"the planned picture {spoiler}: {choice['reason']}"
                        repeat = choice
                if a is not None and not _still_usable(a):
                    # reviewed after planning (rejected / rights changed):
                    # another candidate of the beat, else keep the picture
                    choice = tracker.pick(pool(beat, recent, open_), start, end, named,
                                          reserved=planned)
                    if choice is None and shots:
                        extend_last(end)
                        continue
                    a = assets[choice["asset_id"]] if choice else None
                    if choice:
                        fill_reason = f"the planned picture became unusable: {choice['reason']}"
                        repeat = choice
                if (a is not None and cmd != "SHOW_DOCUMENT" and fill_reason is None
                        and tracker.appearances(a.asset_code)
                        and not tracker.continues(a.asset_code, start)):
                    # the director shows this picture again: an unused
                    # alternative first (of the same person/thing when the
                    # sentence names it), a reuse only when justified
                    code = a.asset_code
                    ok, why_not = tracker.allows(code, start, end)
                    who = tracker.named(code, named)
                    alts = pool(beat, recent | {code},
                                (lambda x: open_(x) and bool(set(asset_facts(x)["entities"])
                                                             & set(who)))
                                if who else open_)
                    alt = tracker.pick(alts, start, end, named, reserved=planned - {code},
                                       allow_repeat=False)
                    if alt is not None:
                        a = assets[alt["asset_id"]]
                        fill_reason = (f"{code} was already shown "
                                       f"{tracker.appearances(code)}x: {alt['reason']}")
                    elif ok:
                        repeat = {"repeat": True, "repeat_justified": True,
                                  "repeat_reason": (f"named in the sentence being spoken "
                                                    f"({', '.join(who)})" if who else
                                                    "nothing else exists for this moment")}
                    elif shots:
                        extend_last(end)  # not allowed again: hold what is on screen
                        shots[-1].setdefault("held", []).append(
                            {"at": round(start, 3), "instead_of": code, "why": why_not})
                        continue
                    else:
                        a = None
                if a is None:
                    shot.update({"command": "BLACK_SCREEN", "kind": "black", "motion": "NONE"})
                else:
                    kind = ("document" if cmd == "SHOW_DOCUMENT" else
                            "video" if a.asset_type == "video" else "image")
                    shot.update({"kind": kind, **_asset_info(a)})
                    if kind == "video":
                        shot.update(_clip_info(a, s if a.asset_code == s.get("asset_id") else None))
                    elif cmd == "SHOW_CLIP":  # a photo took the place of the clip
                        shot.update({"command": "NEW_IMAGE", "motion": "SLOW_PUSH"})
                    if kind != "document":
                        shot["alternatives"] = [
                            _asset_info(assets[c]) for c in cands.get(span["beat_id"], [])
                            if c in assets and c != a.asset_code
                            and assets[c].asset_type == "photo"][:4]
                    if fill_reason:
                        shot["fill_reason"] = fill_reason
                    if repeat:
                        shot.update(_repeat_fields(repeat))
                    if R.needs_attribution(a.rights_status) and a.credit:
                        credits.add(a.credit)
                        add_overlay("credit", a.credit, start, end)
                    if a.asset_role == "illustration":
                        add_overlay("label", LABELS["illustration"].get(language, ""), start, end)
                    tracker.add(a.asset_code, start, end)
                    current = shot
            elif cmd in ("CROP_EXISTING", "ZOOM_EXISTING") and current:
                shot.update({k: current[k] for k in current
                             if k not in ("start", "end", "command", "motion", "speed",
                                          "transition_in", "beat_id", "why", "fill_reason",
                                          "repeat_reason", "repeat_justified")})
                if shot.get("kind") == "video":
                    # the clip plays on from where the previous shot left
                    # it — the same footage is never played twice
                    played = float(current["end"]) - float(current["start"])
                    lo = float(current.get("clip_start") or 0.0)
                    hi = current.get("clip_end")
                    at = lo + played
                    if hi is not None:
                        at = min(at, max(float(hi) - 0.05, lo))
                    shot.update({"command": "SHOW_CLIP", "motion": "NONE",
                                 "clip_start": round(at, 3)})
                    current = shot
                else:
                    shot["motion"] = "CROP_FOCUS"
                if shot.get("asset_id"):
                    tracker.add(shot["asset_id"], shot["start"], shot["end"])
            elif cmd == "SHOW_MAP":
                maps = [assets[c] for c in s.get("map_assets") or [] if c in assets]
                if not maps:
                    shot.update({"command": "BLACK_SCREEN", "kind": "black", "motion": "NONE"})
                else:
                    shot.update({"kind": "map", "map_paths": [m.local_path for m in maps],
                                 "marker": json.loads(maps[-1].spec_json or "{}").get("marker"),
                                 "asset_id": maps[-1].asset_code, "path": maps[-1].local_path,
                                 "type": "map", "tier": facts[maps[-1].asset_code]["tier"],
                                 "place": (s.get("map_info") or {}).get("place")
                                 or s.get("map_place")})
                    credits.add(ai_config.maps.attribution)
                    add_overlay("credit", ai_config.maps.attribution, start, end)
                    add_overlay("place", texts.get(text_key), start + 1.0, min(start + 6.0, end - 0.5))
                    tracker.add(maps[-1].asset_code, start, end)
                    current = shot
            elif cmd == "SHOW_QUOTE":
                shot.update({"kind": "black", "motion": "NONE"})
                add_overlay("quote", texts.get(text_key), start + 0.3, end - 0.3)
                current = None
            elif cmd == "SHOW_TIMELINE" and timeline is not None:
                shot.update({"kind": "timeline", "motion": "NONE", "timeline": timeline,
                             "event": timeline["event"]})
                last_event = timeline["event"]
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
    guard = SP.Firewall(before_incident | ({incident_beat} if incident_beat in order else set()),
                        planfw.custody, reveal_blocks)
    # the whole film at once: a fill never takes a picture shown later
    tracker = UsageTracker.from_shots(shots, facts)
    shots = _clip_overruns(shots, plan, assets, order, guard, tracker, named_sents, starts)
    shots = _cap_black(shots, plan, assets, order, guard, att.max_black_seconds,
                       starts, tracker, named_sents)
    final = _vary_long_holds(shots, plan, assets, order, guard, starts, tracker, named_sents)
    cards_left_out, intro = insert_chapter_cards(final, cards, spans, overlays)
    for n, sh in enumerate(final):
        sh["index"] = n
        if n == 0:
            sh["transition_in"] = "FADE_BLACK"
    annotate(final, facts, named_sents)
    cred = credit_overlays(final, duration)
    labels = label_overlays(final, language, duration)
    overlays[:] = [o for o in overlays if o["kind"] not in ("credit", "label")] + cred + labels
    credits |= {o["text"] for o in cred}
    for o in status_overlays(case_status, production_type, language, duration):
        add_overlay(o["kind"], o["text"], o["start"], o["end"])
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
        "case_status": case_status,
        "production_type": production_type or "original",
        "opening_strategy": opening_strategy,
        "beats": [{"beat_id": s["beat_id"], "start": s["start"], "end": s["end"]} for s in spans],
        "voice": [{"block_id": b["block_id"], "start": b["start"], "end": b["end"]} for b in blocks],
        "silences": silences,
        "shots": final,
        "overlays": sorted(overlays, key=lambda o: o["start"]),
        "music": [{**{k: p.get(k) for k in ("role", "cue_id", "mood", "start", "duration",
                                            "level_db")},
                   "track_code": p.get("track_code"), "why": p.get("why"),
                   "selection_reason": p.get("selection_reason")}
                  for p in (manifest.get("mix") or {}).get("placements", [])],
        "subtitles": (sentence_subtitles(manifest["timeline"]["sentences"],
                                         manifest["timeline"].get("words") or [])
                      if manifest["timeline"].get("sentences")
                      else _subtitles(manifest["timeline"].get("words") or [], language)),
        "credits": sorted(credits),
        # what fills and critic fixes may use, and what each sentence names
        "candidates": cands,
        "sentence_entities": named_sents,
        # beats where investigation / custody pictures would give the story away
        "firewall": guard.as_json(),
        # chapter / title cards that found no room (the gap was too short)
        "cards_left_out": cards_left_out,
        # the channel intro: in the cold open's break, or before the first word
        "intro": intro,
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


def film_key_of(plan_row: VisualPlan, manifest: dict) -> str:
    """One film across its languages: the music mix's key, else the
    blueprint's (music.film_key_for names films the same way)."""
    return (manifest.get("mix") or {}).get("film_key") or f"bp{plan_row.blueprint_id}"


def link_music_usage(db: Session, film_key: str, language: str, production_script_id: int) -> int:
    """The film/language's MusicUsage rows belong to this production script."""
    n = (db.query(MusicUsage)
         .filter(MusicUsage.film_key == film_key, MusicUsage.language == language)
         .update({"production_script_id": production_script_id}, synchronize_session=False))
    db.commit()
    return n


async def build_production_script(db: Session, version: StoryVersion, plan_row: VisualPlan,
                                  manifest: dict, mode: str = "pilot",
                                  production_type: str = "original") -> ProductionScript:
    language = version.language or "en"
    texts = await localized_plan_texts(db, version, plan_row)
    plan = json.loads(plan_row.plan_json or "{}")
    assets = {a.asset_code: a for a in db.query(VisualAsset).filter(
        VisualAsset.case_id == version.case_id).all()}
    from app.documentary.openings import opening_of_blueprint
    from app.documentary.voice_performance import latest_performance

    perf = latest_performance(db, version.id)
    incident = json.loads(perf.performance_json or "{}").get("incident_beat") if perf else None
    case = db.get(Case, version.case_id)
    bp_row = db.get(EditorialBlueprint, plan_row.blueprint_id) if plan_row.blueprint_id else None
    opening = (opening_of_blueprint(db, bp_row).get("strategy")
               or (plan.get("opening") or {}).get("strategy"))
    blueprint = json.loads(bp_row.blueprint_json or "{}") if bp_row else {}
    from app.documentary.chapters import cards_for, latest_chapter_plan

    from app.documentary.intros import intro_seconds

    cards = cards_for(latest_chapter_plan(db, plan_row.blueprint_id), language)
    cards["intro_seconds"] = intro_seconds(language)
    from app.identity.titles import approved_title

    approved = approved_title(db, version.case_id, language)
    if approved and (cards.get("film_title") is not None or cards.get("cold_open")):
        cards["film_title"] = approved      # one title: the editor-approved one
    script = compose(manifest, plan, assets, texts, language, incident_beat=incident,
                     cards=cards,
                     arrest_beat=SP.arrest_beat(blueprint),
                     reveal_blocks=SP.reveal_blocks(blueprint),
                     case_status=getattr(case, "resolution_status", None),
                     production_type=production_type or "original", opening_strategy=opening)
    film_key = film_key_of(plan_row, manifest)
    script["film_key"] = film_key
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
    # production memory: every picture's appearance and every music cue
    # of this film/language point at this script
    record_media_usage(db, row, script, film_key)
    link_music_usage(db, film_key, language, row.id)
    out = storage.production_dir(version.case_id, language) / f"production_v{version.id}_{row.version}.json"
    Path(out).write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
    return row
