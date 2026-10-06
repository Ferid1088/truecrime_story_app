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


def performance_for_version(db, version) -> dict:
    """Performance script for a stored StoryVersion, using its newest
    usable blueprint when one exists for exactly this text."""
    from app.documentary.blueprint import usable_blueprint, version_sections

    blueprint = usable_blueprint(db, version)
    script = build_performance(
        version_sections(version), version.language or "en", blueprint
    )
    script["story_version_id"] = version.id
    if blueprint is None:
        script["note"] = (
            "No usable blueprint for this exact text — default style, "
            "act-level pacing."
        )
    return script
