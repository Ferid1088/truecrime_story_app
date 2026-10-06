"""Deep-source chunking.

Splits retrieved source text into provenance-preserving chunks sized from
`source_chunking` config. Chunks dedupe by normalized-content hash and stay
within [min_tokens, max_tokens] — no tiny fragments, no giant blobs.

Token counts are a word-count estimate (words * 1.3), deliberately simple:
the chunks exist to bound LLM context, not to bill.
"""

import hashlib
import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Source, SourceChunk

_WORD = re.compile(r"\S+")
_SENT_BOUNDARY = re.compile(r"(?<=[.!?؟۔])\s+|\n{2,}")


def _approx_tokens(text: str) -> int:
    return max(1, int(len(_WORD.findall(text)) * 1.3))


def _norm_text(text: str) -> str:
    return " ".join((text or "").lower().split())


def _hash(text: str) -> str:
    return hashlib.sha256(_norm_text(text).encode("utf-8")).hexdigest()


def split_chunks(text: str) -> list[str]:
    """Split `text` into chunk strings respecting config bounds.

    Splits on sentence/paragraph boundaries. A lone trailing fragment below
    min_tokens is merged into the previous chunk; an oversize chunk is
    hard-split at sentence boundaries, and as a last resort at max_tokens
    worth of characters.
    """
    cfg = ai_config.source_chunking
    sentences = [s.strip() for s in _SENT_BOUNDARY.split(text or "") if s.strip()]
    chunks: list[str] = []
    cur: list[str] = []

    def flush():
        if cur:
            chunks.append(" ".join(cur))
            cur.clear()

    for sent in sentences:
        merged = " ".join(cur + [sent])
        if _approx_tokens(merged) > cfg.max_tokens and cur:
            flush()
        cur.append(sent)
        if _approx_tokens(" ".join(cur)) >= cfg.target_tokens:
            flush()
    flush()

    # Merge runt tail into its predecessor.
    if len(chunks) >= 2 and _approx_tokens(chunks[-1]) < cfg.min_tokens:
        tail = chunks.pop()
        chunks[-1] = f"{chunks[-1]} {tail}"

    # Last-resort hard split for pathological single sentences.
    out: list[str] = []
    char_cap = max(2000, cfg.max_tokens * 8)
    for c in chunks:
        if _approx_tokens(c) <= cfg.max_tokens * 1.5:
            out.append(c)
        else:
            for i in range(0, len(c), char_cap):
                out.append(c[i : i + char_cap])
    return [c for c in out if _approx_tokens(c) >= max(50, cfg.min_tokens // 4)]


def chunk_source(db: Session, source: Source) -> int:
    """(Re)build SourceChunk rows for `source` from its retrieved text.

    Only retrieves-text sources are chunked — metadata/summary sources have
    no content to chunk. Returns the number of new chunks created.
    """
    text = (source.raw_text or "").strip()
    if not text or source.content_status in ("metadata_only", "unavailable"):
        return 0
    db.query(SourceChunk).filter(SourceChunk.source_id == source.id).delete()
    pieces = split_chunks(text)
    existing = {
        c.content_hash
        for c in db.query(SourceChunk)
        .join(Source, SourceChunk.source_id == Source.id)
        .filter(Source.case_id == source.case_id)
        .all()
    }
    created = 0
    for i, piece in enumerate(pieces):
        h = _hash(piece)
        if h in existing:
            continue
        existing.add(h)
        db.add(
            SourceChunk(
                source_id=source.id,
                chunk_index=i,
                text=piece,
                token_count=_approx_tokens(piece),
                content_hash=h,
                language=source.language or "unknown",
            )
        )
        created += 1
    db.flush()
    return created
