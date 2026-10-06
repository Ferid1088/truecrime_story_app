"""YouTube transcript provider via youtube-transcript-api.

Fetches only captions that YouTube publicly exposes for a video (creator
uploaded or platform auto-generated) — the library performs no auth bypass.
All blocking I/O is pushed onto a worker thread so the event loop stays
responsive while several videos are processed.
"""

import asyncio
import logging

from app.core.ai_config import ai_config
from app.providers.transcript.base import (
    TranscriptProvider,
    TranscriptResult,
    TranscriptSegmentData,
)

log = logging.getLogger(__name__)


class YouTubeTranscriptProvider(TranscriptProvider):
    name = "youtube_transcript_api"

    def _fetch_sync(
        self, video_id: str, preferred: list[str]
    ) -> TranscriptResult:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()
        transcripts = api.list(video_id)

        # Prefer manually-created captions in a preferred language, then any
        # manual transcript, then generated captions in a preferred language,
        # then any generated transcript. Never translate here — canonical
        # English normalization happens downstream as a separate stage.
        chosen = None
        chosen_type = None
        manual = [t for t in transcripts if not t.is_generated]
        generated = [t for t in transcripts if t.is_generated]
        for pool, ttype in ((manual, "manual"), (generated, "auto_caption")):
            for lang in preferred:
                for t in pool:
                    if t.language_code == lang or t.language_code.startswith(lang):
                        chosen, chosen_type = t, ttype
                        break
                if chosen:
                    break
            if chosen:
                break
        if chosen is None and (manual or generated):
            pool = manual or generated
            chosen = pool[0]
            chosen_type = "manual" if not chosen.is_generated else "auto_caption"
        if chosen is None:
            return TranscriptResult(
                available=False,
                transcript_type="unavailable",
                retrieval_method=self.name,
                reason="no captions exposed for this video",
            )

        fetched = chosen.fetch()
        cfg = ai_config.transcript_ingestion
        segments = []
        for i, snip in enumerate(fetched.snippets):
            if i >= cfg.max_segments_per_video:
                break
            segments.append(
                TranscriptSegmentData(
                    start_seconds=float(snip.start),
                    end_seconds=float(snip.start) + float(snip.duration),
                    text=snip.text,
                )
            )
        if segments and segments[-1].end_seconds > cfg.max_transcript_seconds:
            return TranscriptResult(
                available=False,
                transcript_type=chosen_type,
                retrieval_method=self.name,
                reason=(
                    f"transcript exceeds max duration "
                    f"({int(segments[-1].end_seconds)}s > {cfg.max_transcript_seconds}s)"
                ),
            )
        return TranscriptResult(
            available=True,
            language=chosen.language_code,
            transcript_type=chosen_type,
            segments=segments,
            retrieval_method=self.name,
            meta={"is_translatable": chosen.is_translatable},
        )

    async def get_transcript(
        self, video_id: str, preferred_languages: list[str] | None = None
    ) -> TranscriptResult:
        preferred = preferred_languages or ai_config.transcript_ingestion.preferred_languages
        try:
            return await asyncio.to_thread(self._fetch_sync, video_id, preferred)
        except Exception as e:
            # Disabled captions, private/removed video, IP blocks, malformed
            # responses — all mean "not legitimately obtainable right now".
            log.info("transcript unavailable for %s: %s", video_id, e)
            return TranscriptResult(
                available=False,
                transcript_type="unavailable",
                retrieval_method=self.name,
                reason=f"{type(e).__name__}: {str(e)[:200]}",
            )
