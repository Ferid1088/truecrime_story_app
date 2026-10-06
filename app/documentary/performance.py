"""Performance script: how each voice block is performed and paced.

Turns an editorial blueprint into voice blocks that know their style and
the pause that follows them:

* A beat's audio intent maps to a voice style (config
  performance.style_for_intent). The TTS engine applies one style per
  request, so a style change is a hard block boundary.
* A dramatic pause or silence can only sit BETWEEN blocks (inside a block
  the voice engine owns timing), so a beat ending in one is a hard block
  boundary too.
* Consecutive beats with the same style and no long pause form one
  SEGMENT; the sentence-safe voice-block planner then cuts segments into
  30–90 s blocks as usual.

The silence director keeps long pauses meaningful: never two in a row
(unless both are protected reveals/chapter ends), at most a configured
share of all beats, and each one slightly different in length so the
film never pauses with machine regularity.

Without a blueprint (or for a version whose beats are not mapped) the
script falls back to one segment per act in the default style.
"""

from __future__ import annotations

import hashlib
import math

from app.agents.story import _paragraphs
from app.core.ai_config import PerformanceConfig, ai_config
from app.documentary.voice_blocks import plan_voice_blocks

LONG = ("dramatic", "silence")
_LEVEL = {"low": 0, "medium": 1, "high": 2}


def _jitter(key: str, amount: float) -> float:
    """Deterministic factor in [1-amount, 1+amount] from a stable key."""
    h = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)
    return 1.0 + amount * (2.0 * (h / 0xFFFFFFFF) - 1.0)


def direct_pauses(beats: list[dict], cfg: PerformanceConfig | None = None
                  ) -> tuple[dict[str, str], list[dict]]:
    """Silence director: final pause class per beat + a log of changes."""
    cfg = cfg or ai_config.performance
    pauses = {b["id"]: b.get("pause_after", "none") for b in beats}
    log: list[dict] = []
    protected = {b["id"] for b in beats if b.get("purpose") in cfg.protected_purposes}

    # 1. never two long pauses in a row (the second one loses its weight)
    for prev, cur in zip(beats, beats[1:]):
        if pauses[prev["id"]] in LONG and pauses[cur["id"]] in LONG:
            if cur["id"] in protected and prev["id"] in protected:
                continue
            victim = prev["id"] if cur["id"] in protected else cur["id"]
            log.append({"beat": victim, "from": pauses[victim], "to": "short",
                        "reason": "consecutive_long_pauses"})
            pauses[victim] = "short"

    # 2. long pauses are rare: cap their share, keep the most meaningful
    longs = [b for b in beats if pauses[b["id"]] in LONG]
    allowed = max(1, math.floor(cfg.max_long_pause_share * len(beats)))
    if len(longs) > allowed:
        ranked = sorted(
            longs,
            key=lambda b: (
                b["id"] in protected,
                pauses[b["id"]] == "silence",
                _LEVEL.get(b.get("emotional_load"), 1)
                + _LEVEL.get(b.get("mystery_intensity"), 1),
            ),
            reverse=True,
        )
        for b in ranked[allowed:]:
            if b["id"] in protected:
                continue
            log.append({"beat": b["id"], "from": pauses[b["id"]], "to": "short",
                        "reason": "long_pause_share_cap"})
            pauses[b["id"]] = "short"
    return pauses, log


def _pause_ms(kind: str, key: str, cfg: PerformanceConfig) -> int:
    base = cfg.pause_ms[kind]
    if kind in LONG:
        base = base * _jitter(key, cfg.pause_jitter)
    return int(round(base))


def build_performance(
    sections: list[dict], language: str, blueprint: dict | None = None,
    words_per_minute: int | None = None,
) -> dict:
    """Performance script for one language version.

    sections: the version's act sections ([{"id","text"}]) — the same
    acts the blueprint's beats refer to (paragraph ranges per act)."""
    perf = ai_config.performance
    voice = ai_config.voice
    default_style = voice.default_style
    beats = (blueprint or {}).get("beats") or []
    act_paras = {s["id"]: [" ".join(p.split()) for p in _paragraphs(s["text"])]
                 for s in sections}

    mapped = bool(beats) and all(
        b.get("act_id") in act_paras
        and 1 <= b["paragraphs"][0] <= b["paragraphs"][1] <= len(act_paras[b["act_id"]])
        for b in beats
    ) and sum(b["paragraphs"][1] - b["paragraphs"][0] + 1 for b in beats) == sum(
        len(v) for v in act_paras.values()
    )

    segments: list[dict] = []
    adjustments: list[dict] = []
    if not mapped:
        for s in sections:
            segments.append({
                # same ids as the plain voice-block plan (stable block ids)
                "id": s["id"], "act_id": s["id"], "beats": [],
                "style": default_style, "paragraphs": act_paras[s["id"]],
                "pause_kind": None,
            })
    else:
        pauses, adjustments = direct_pauses(beats, perf)
        cur: dict | None = None
        counter: dict[str, int] = {}
        for b in beats:
            style = perf.style_for_intent.get(b.get("audio_intent"), default_style)
            first, last = b["paragraphs"]
            paras = act_paras[b["act_id"]][first - 1:last]
            new_segment = (
                cur is None or cur["act_id"] != b["act_id"]
                or cur["style"] != style or cur["pause_kind"] in LONG
            )
            if new_segment:
                counter[b["act_id"]] = counter.get(b["act_id"], 0) + 1
                cur = {
                    "id": f"{b['act_id']}.s{counter[b['act_id']]:02d}",
                    "act_id": b["act_id"], "beats": [], "style": style,
                    "paragraphs": [], "pause_kind": None,
                }
                segments.append(cur)
            start_para = len(cur["paragraphs"])
            cur["paragraphs"].extend(paras)
            cur["beats"].append({
                "beat_id": b["id"], "para_start": start_para,
                "para_end": len(cur["paragraphs"]),
            })
            cur["pause_kind"] = pauses[b["id"]]

    plan = plan_voice_blocks(
        [{"id": seg["id"], "text": "\n\n".join(seg["paragraphs"])} for seg in segments],
        language, words_per_minute=words_per_minute,
    )
    by_segment: dict[str, list[dict]] = {}
    for blk in plan["blocks"]:
        by_segment.setdefault(blk["section_id"], []).append(blk)

    blocks: list[dict] = []
    for si, seg in enumerate(segments):
        seg_text = "\n\n".join(seg["paragraphs"])
        # character range of every beat inside the segment text
        para_offsets, pos = [], 0
        for p in seg["paragraphs"]:
            para_offsets.append((pos, pos + len(p)))
            pos += len(p) + 2
        beat_ranges = [
            (bt["beat_id"], para_offsets[bt["para_start"]][0],
             para_offsets[bt["para_end"] - 1][1])
            for bt in seg["beats"]
        ]
        seg_blocks = by_segment.get(seg["id"], [])
        cursor = 0
        next_act = segments[si + 1]["act_id"] if si + 1 < len(segments) else None
        for bi, blk in enumerate(seg_blocks):
            start = seg_text.find(blk["text"], cursor)
            if start < 0:  # should not happen: blocks are segment substrings
                start = cursor
            end = start + len(blk["text"])
            cursor = end
            blk_beats = [
                {"beat_id": bid, "start_char": max(bs, start) - start,
                 "end_char": min(be, end) - start}
                for bid, bs, be in beat_ranges if bs < end and be > start
            ]
            last_in_segment = bi == len(seg_blocks) - 1
            if not last_in_segment:
                kind, ms = "block", voice.between_blocks_ms
            elif next_act is None:
                kind, ms = "end", 0
            else:
                kind = seg["pause_kind"] if seg["pause_kind"] in LONG else (
                    "act" if next_act != seg["act_id"] else "short")
                if kind in LONG:
                    ms = _pause_ms(kind, seg["beats"][-1]["beat_id"], perf)
                    if next_act != seg["act_id"]:
                        ms = max(ms, voice.between_sections_ms)
                elif kind == "act":
                    ms = voice.between_sections_ms
                else:
                    ms = perf.pause_ms["short"]
            blocks.append({
                **blk,
                "act_id": seg["act_id"], "segment_id": seg["id"],
                "style": seg["style"], "beats": blk_beats,
                "pause_after_kind": kind, "pause_after_ms": int(ms),
            })

    return {
        "language": language,
        "beats_mapped": mapped,
        "blueprint_used": bool(beats) and mapped,
        "segments": [
            {"id": s["id"], "act_id": s["act_id"], "style": s["style"],
             "beat_ids": [b["beat_id"] for b in s["beats"]],
             "pause_after": s["pause_kind"]}
            for s in segments
        ],
        "pause_adjustments": adjustments,
        "long_pauses": sum(1 for b in blocks if b["pause_after_kind"] in LONG),
        "block_count": len(blocks),
        "total_est_seconds": plan["total_est_seconds"],
        "oversize_block_ids": plan["oversize_block_ids"],
        "words_per_minute": plan["words_per_minute"],
        "blocks": blocks,
    }


def _paragraph_groups(paras: list[str], wpm: int, min_seconds: float) -> list[list[str]]:
    """Paragraphs of one beat as breath groups: a very short paragraph
    joins the next one (a breath after two lines sounds mechanical)."""
    groups: list[list[str]] = []
    cur: list[str] = []
    for p in paras:
        cur.append(p)
        if sum(len(x.split()) for x in cur) / wpm * 60 >= min_seconds:
            groups.append(cur)
            cur = []
    if cur:
        if groups:
            groups[-1].extend(cur)
        else:
            groups.append(cur)
    return groups


def build_directed_performance(
    sections: list[dict], language: str, blueprint: dict, audio_plan: dict,
    words_per_minute: int | None = None,
) -> dict:
    """Performance script driven by the audio director's plan: a breath
    between paragraphs, and after every beat the planned transition
    (breath, music bridge, emotional moment, sting, silence, chapter
    break). Each beat and each breath group is its own voice block run,
    so every planned pause is an exact app-level gap."""
    perf = ai_config.performance
    voice = ai_config.voice
    direction = ai_config.audio_direction
    wpm = words_per_minute or ai_config.words_per_minute_for(language)
    act_paras = {s["id"]: [" ".join(p.split()) for p in _paragraphs(s["text"])]
                 for s in sections}
    plan_by_beat = {pb["beat_id"]: pb for pb in audio_plan.get("beats") or []}
    beats = blueprint.get("beats") or []

    groups: list[dict] = []   # voice sections
    for b in beats:
        first, last = b["paragraphs"]
        paras = act_paras[b["act_id"]][first - 1:last]
        pb = plan_by_beat.get(b["id"], {})
        style = perf.style_for_intent.get(b.get("audio_intent"), voice.default_style)
        parts = _paragraph_groups(paras, wpm, min_seconds=8.0)
        for gi, part in enumerate(parts, 1):
            groups.append({
                "id": f"{b['id']}.g{gi:02d}", "beat_id": b["id"],
                "act_id": b["act_id"], "style": style,
                "text": "\n\n".join(part),
                "last_in_beat": gi == len(parts),
                "breath": pb.get("paragraph_breath", "normal"),
                "after": pb.get("after") or {"type": "breath", "seconds": 1.0},
            })

    plan = plan_voice_blocks(
        [{"id": g["id"], "text": g["text"]} for g in groups], language,
        words_per_minute=words_per_minute,
    )
    by_group: dict[str, list[dict]] = {}
    for blk in plan["blocks"]:
        by_group.setdefault(blk["section_id"], []).append(blk)

    blocks: list[dict] = []
    for gi, g in enumerate(groups):
        g_blocks = by_group.get(g["id"], [])
        for bi, blk in enumerate(g_blocks):
            last_block = bi == len(g_blocks) - 1
            final = gi == len(groups) - 1 and last_block
            if final:
                kind, ms, after = "end", 0, {"type": "end", "seconds": 0.0}
            elif not last_block:
                kind, ms, after = "block", voice.between_blocks_ms, None
            elif not g["last_in_beat"]:
                base = direction.paragraph_breath_ms[g["breath"]]
                kind = "paragraph"
                ms = int(round(base * _jitter(g["id"], perf.pause_jitter)))
                after = None
            else:
                after = dict(g["after"])
                kind = after.get("type") or "breath"
                secs = float(after.get("seconds") or 0)
                rng = direction.transitions.get(kind)
                if rng:  # current guard-rails apply to older plans too
                    secs = min(max(secs, rng[0]), rng[1])
                    after["seconds"] = secs
                ms = int(round(secs * 1000))
                if kind == "breath":
                    ms = int(round(ms * _jitter(g["beat_id"], perf.pause_jitter)))
            blocks.append({
                **blk, "act_id": g["act_id"], "segment_id": g["id"],
                "style": g["style"],
                "beats": [{"beat_id": g["beat_id"], "start_char": 0,
                           "end_char": len(blk["text"])}],
                "pause_after_kind": kind, "pause_after_ms": int(ms),
                "transition": after if kind not in ("block", "paragraph") else None,
            })

    return {
        "language": language,
        "beats_mapped": True, "blueprint_used": True, "directed": True,
        "segments": [{"id": g["id"], "act_id": g["act_id"], "style": g["style"],
                      "beat_ids": [g["beat_id"]], "pause_after": g["after"]["type"]
                      if g["last_in_beat"] else "paragraph"} for g in groups],
        "beat_audio": {
            pb["beat_id"]: {"bed": pb.get("bed", "none"),
                            "bed_level": pb.get("bed_level", "very_low"),
                            "after": pb.get("after")}
            for pb in audio_plan.get("beats") or []
        },
        "audio_notes": audio_plan.get("notes"),
        "pause_adjustments": [],
        "long_pauses": sum(1 for b in blocks if b["pause_after_kind"] not in
                           ("block", "paragraph", "breath", "end")),
        "block_count": len(blocks),
        "total_est_seconds": plan["total_est_seconds"],
        "planned_pause_seconds": round(sum(b["pause_after_ms"] for b in blocks) / 1000, 1),
        "oversize_block_ids": plan["oversize_block_ids"],
        "words_per_minute": plan["words_per_minute"],
        "blocks": blocks,
    }


def blueprint_and_plan_for_version(db, version) -> tuple[dict | None, dict | None]:
    """(blueprint usable for this version's text, audio plan or None).
    Spoken versions use their beat sections; other versions their acts."""
    import json as _json

    from app.documentary.audio_director import latest_audio_plan
    from app.documentary.blueprint import latest_blueprint, usable_blueprint
    from app.documentary.spoken import spoken_blueprint

    if version.kind == "spoken":
        blueprint = spoken_blueprint(db, version)
        bp_id = (blueprint or {}).get("blueprint_id")
    else:
        blueprint = usable_blueprint(db, version)
        row = latest_blueprint(db, version.id) if blueprint else None
        bp_id = row.id if row else None
    plan_row = latest_audio_plan(db, bp_id) if bp_id else None
    plan = _json.loads(plan_row.plan_json) if plan_row else None
    return blueprint, plan


def performance_for_version(db, version) -> dict:
    """Performance script for a stored StoryVersion: directed by the
    audio plan when one exists, else blueprint styles/pauses, else
    default style with act-level pacing."""
    from app.documentary.blueprint import version_sections

    blueprint, audio_plan = blueprint_and_plan_for_version(db, version)
    language = version.language or "en"
    if blueprint and audio_plan:
        script = build_directed_performance(
            version_sections(version), language, blueprint, audio_plan)
    else:
        script = build_performance(version_sections(version), language, blueprint)
        script["directed"] = False
    script["story_version_id"] = version.id
    if blueprint is None:
        script["note"] = (
            "No usable blueprint for this exact text — default style, "
            "act-level pacing."
        )
    elif audio_plan is None:
        script["note"] = "No audio plan yet — blueprint pauses only, no music."
    return script
