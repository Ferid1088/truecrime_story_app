"""Section F — resolve Detective-toolkit release anchors against real timing, and keep a
dispatched visual from appearing before the words that support it are actually spoken.

Two precision tiers, per the doc's "use word alignment where reliable and sentence-level timing
where it is not":

- word-level (`resolve_word_level`): reuses `app.documentary.voice_render.beat_times`'s own
  exact-match/proportional-fallback logic against a real voice take's per-character alignment,
  when the caller has one (e.g. during voice rendering, where the take's `alignment`/`text`/`t0`
  are already in hand). Gives the precise real second the anchor's words were actually spoken.
- sentence/beat-level (`anchor_fraction_within_span`): a proportional position of the anchor's
  own character span within its narration unit's text, interpolated into a beat's REAL current
  span (start/end seconds from the compiled production script). Needs no raw alignment data, so
  it is always available once a beat has a compiled span — this is what `dispatch.py` uses at
  carve time, since it only ever sees the compiled timeline, not a voice take's alignment.

Either tier can persist onto `release_anchors.resolved_start_seconds/resolved_end_seconds` (word
level: real resolved seconds; sentence level: the clamp point must still be computed against the
*current* compiled span, so it is not persisted - a stored cut-timeline-dependent timestamp would
go stale across reruns). `release_anchors.timing_exact` records which tier produced the last
persisted resolution.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import ReleaseAnchor


def resolve_word_level(
    unit_char_start_in_block: int, anchor_char_start: int, anchor_char_end: int,
    block_text: str, alignment: dict, t0: float,
) -> tuple[float, float, bool]:
    """Real resolved seconds for one anchor from a voice take's own character alignment.
    `unit_char_start_in_block` is the narration unit's own text's offset within `block_text`
    (the full TTS request text for that take) - the caller knows this because it assembled the
    block from narration units in order. Returns (start, end, exact) in the take's own
    block-local, post-trim time base (same base as `beat_times`/`sentence_times`)."""
    from app.documentary.voice_render import beat_times

    exact = "".join(alignment["characters"]) == block_text
    item = {
        "beat_id": "_anchor",
        "start_char": unit_char_start_in_block + anchor_char_start,
        "end_char": unit_char_start_in_block + anchor_char_end,
    }
    [resolved] = beat_times([item], block_text, alignment, t0)
    return resolved["start"], resolved["end"], exact


def anchor_fraction_within_span(
    anchor_char_start: int, anchor_char_end: int, unit_text_len: int,
    span_start: float, span_end: float,
) -> tuple[float, float]:
    """Sentence/beat-level fallback: where the anchor's own span sits, proportionally, between
    `span_start` and `span_end` - the only timing a dispatcher sees once a beat is compiled,
    with no voice-take alignment in hand. `unit_text_len` is the full length of the anchor's
    narration unit's own text (the anchor's char_start/char_end are offsets into it)."""
    n = max(unit_text_len, 1)
    width = max(span_end - span_start, 0.0)
    start = span_start + width * min(max(anchor_char_start, 0), n) / n
    end = span_start + width * min(max(anchor_char_end, 0), n) / n
    return round(start, 3), round(end, 3)


def apply_resolved_timing(db: Session, anchor: ReleaseAnchor, start: float, end: float,
                          exact: bool) -> None:
    """Persist a word-level resolution onto the anchor row itself (see module docstring for why
    the sentence-level, compiled-span-dependent tier is never persisted this way)."""
    anchor.resolved_start_seconds = start
    anchor.resolved_end_seconds = end
    anchor.timing_exact = exact
    db.flush()
