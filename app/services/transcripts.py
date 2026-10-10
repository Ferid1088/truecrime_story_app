"""Transcript cleaning, storage and canonical normalization.

Deterministic cleanup only (Master_Prompt Part 4): caption-overlap
deduplication, whitespace normalization, filler stripping — never stylistic
rewriting. Original text + timestamps are always preserved; canonical
English is stored *alongside* in canonical_text_en.
"""

from app.core.prompts import prompt
import hashlib
import json
import re
import unicodedata

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Source, TranscriptSegment, VideoSource
from app.providers.transcript.base import TranscriptResult
from app.services.tracking import stamp_run, track_run

_WS = re.compile(r"\s+")
_FILLER = re.compile(
    r"^\s*[\[(♪]+\s*(music|musik|applause|applaus|laughter|gelächter|"
    r"noise|silence|موسيقى|تصفيق|موسیقی|صدای|иностран)\s*[\])♪]*\s*$",
    re.IGNORECASE,
)
# Caption decorators like [Music] __ or >> SPEAKER: prefixes worth dropping.
_DECORATOR = re.compile(r"\[(?:music|musik|applause|applaus|noise)\]", re.IGNORECASE)
_SPEAKER_LABEL = re.compile(r"^\s*(?:>>|--|[A-ZÄÖÜ][A-ZÄÖÜ ]{2,20}:)\s*")


def _norm(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", text or "")).strip()


def _hash(text: str) -> str:
    return hashlib.sha256(
        _norm(text).lower().encode("utf-8")
    ).hexdigest()


def clean_segments(segments: list[dict]) -> list[dict]:
    """Deterministically clean raw caption segments.

    - drop pure-filler segments ([Music], applause markers)
    - strip speaker-label noise and caption decorators
    - collapse whitespace
    - drop rolling-caption duplicates: YouTube auto-captions repeat the
      tail of the previous line inside the next; a segment whose normalized
      text is fully contained in its predecessor (or vice versa) is a
      caption artifact, not new content.
    """
    out: list[dict] = []
    prev_norm = ""
    for seg in segments:
        text = _DECORATOR.sub("", seg.get("text") or "")
        text = _SPEAKER_LABEL.sub("", text)
        text = _norm(text)
        if not text or _FILLER.match(seg.get("text") or ""):
            continue
        ntext = text.lower()
        if ntext and prev_norm and (
            ntext in prev_norm or prev_norm in ntext
        ):
            # Timing duplicate / rolling window — keep the longer text only.
            if len(ntext) > len(prev_norm) and out:
                out[-1]["text"] = text
                prev_norm = ntext
            continue
        out.append(
            {
                "start_seconds": float(seg.get("start_seconds") or 0.0),
                "end_seconds": float(
                    seg.get("end_seconds") or seg.get("start_seconds") or 0.0
                ),
                "text": text,
            }
        )
        prev_norm = ntext
    return out


def store_transcript(
    db: Session, video: VideoSource, result: TranscriptResult
) -> int:
    """Persist cleaned segments for a video. Idempotent: re-storing replaces
    the segment set; cross-video duplicate text within a case is still
    hash-deduped per segment so identical caption chunks stored twice do not
    inflate the corpus."""
    if not result.available:
        video.transcript_status = "unavailable"
        video.transcript_type = result.transcript_type or "unavailable"
        if video.source_id:
            src = db.get(Source, video.source_id)
            if src:
                src.content_status = "unavailable"
                src.retrieval_notes = (result.reason or "transcript unavailable")[:2000]
        db.flush()
        return 0

    cleaned = clean_segments(
        [
            {"start_seconds": s.start_seconds, "end_seconds": s.end_seconds,
             "text": s.text}
            for s in result.segments
        ]
    )
    db.query(TranscriptSegment).filter(
        TranscriptSegment.video_source_id == video.id
    ).delete()
    seen: set[str] = set()
    created = 0
    for i, seg in enumerate(cleaned):
        h = _hash(seg["text"])
        if h in seen:
            continue
        seen.add(h)
        db.add(
            TranscriptSegment(
                video_source_id=video.id,
                start_seconds=seg["start_seconds"],
                end_seconds=seg["end_seconds"],
                segment_index=i,
                text_original=seg["text"],
                language=result.language or video.language or "unknown",
                canonical_text_en=(
                    seg["text"] if (result.language or "en") == "en" else None
                ),
                content_hash=h,
            )
        )
        created += 1

    video.transcript_status = "available"
    video.transcript_type = result.transcript_type
    video.language = result.language or video.language
    if video.source_id:
        src = db.get(Source, video.source_id)
        if src:
            src.content_status = "full_text"
            src.is_authorized_text = True
            src.retrieval_method = result.retrieval_method
            src.language = result.language or src.language
            src.raw_text = None  # segments carry the text, with timestamps
    db.flush()
    return created


def transcript_chunks(video: VideoSource, canonical_only: bool = True) -> list[dict]:
    """Group a video's segments into provenance-preserving extraction chunks.

    Each chunk carries its segment ids + start/end timestamps — downstream
    extraction can always trace a claim back to a point in the video.
    """
    cfg = ai_config.transcript_chunking
    chunks: list[dict] = []
    cur: list[TranscriptSegment] = []
    cur_words = 0
    for seg in video.segments:
        text = (
            seg.canonical_text_en if canonical_only else seg.text_original
        ) or seg.text_original
        w = max(1, int(len(text.split()) * 1.3))
        if cur and cur_words + w > cfg.target_tokens:
            chunks.append(cur)
            cur = []
            cur_words = 0
        cur.append(seg)
        cur_words += w
    if cur:
        chunks.append(cur)
    out = []
    for group in chunks:
        texts = [
            (s.canonical_text_en if canonical_only else s.text_original)
            or s.text_original
            for s in group
        ]
        out.append(
            {
                "segment_ids": [s.id for s in group],
                "start_seconds": group[0].start_seconds,
                "end_seconds": group[-1].end_seconds,
                "language": group[0].language,
                "text": " ".join(t for t in texts if t),
            }
        )
    return [c for c in out if c["text"].strip()]


async def normalize_transcript(db: Session, video: VideoSource) -> int:
    """Fill canonical_text_en for non-English segments (Part 5).

    Batched by chunk; original text is untouched. Returns segments updated.
    """
    canonical = ai_config.multilingual.canonical_language
    pending = [
        s for s in video.segments
        if s.language != canonical and s.canonical_text_en is None
    ]
    if not pending:
        return 0
    from app.providers.generation import get_generation_provider

    gen = get_generation_provider()
    updated = 0
    # Translate in segment batches (each batch = one chunk group) so ids map
    # back deterministically — no freeform text alignment needed.
    by_chunk = transcript_chunks(video, canonical_only=False)
    for chunk in by_chunk:
        segs = [s for s in pending if s.id in set(chunk["segment_ids"])]
        if not segs:
            continue
        system = (
            prompt("services/transcripts/normalize_transcript")
        )
        user = json.dumps(
            {
                "items": [
                    {"id": s.id, "language": s.language, "text": s.text_original}
                    for s in segs
                ],
                "output_schema": {
                    "translations": [{"id": 1, "text_en": "..."}]
                },
            },
            ensure_ascii=False,
        )
        with track_run(
            db, video.case_id, "Transcript Normalizer",
            input_summary=f"video={video.id} segments={len(segs)}",
        ) as run:
            data, res = await gen.generate_structured(
                "transcript_normalizer", system, user
            )
            stamp_run(run, res, "transcript_normalizer")
            by_id = {s.id: s for s in segs}
            done = 0
            for item in data.get("translations", []):
                seg = by_id.get(item.get("id"))
                if seg and item.get("text_en"):
                    seg.canonical_text_en = item["text_en"]
                    done += 1
            updated += done
            run.output_summary = f"normalized={done}"
    db.flush()
    return updated
