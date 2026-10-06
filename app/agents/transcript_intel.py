"""Transcript Intelligence Agent (Master_Prompt Part 6/7).

Extracts structured evidence claims AND narrative insights from transcript
chunks — two separate output streams. Claims carry full provenance
(video, segment ids, timestamps, speaker); narrative insights are explicitly
NOT evidence and are stored in a separate table.

A creator's narration is never treated as a verified quote: quote claims
must be classified (source_document_quote / interview_quote /
reported_quote / creator_narration / uncertain), and creator narration is
never promoted into approved evidence quotes downstream.
"""

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    Case,
    NarrativeInsight,
    TranscriptClaim,
    VideoSource,
)
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.services.transcripts import transcript_chunks

_CLAIM_TYPES = (
    "fact|timeline_event|human_detail|scene_detail|investigation_detail|"
    "physical_evidence|legal|location|person|relationship|quote|"
    "reported_statement|theory|disputed|unverified|question|"
    "contradiction|historical_context"
)

_INSIGHT_TYPES = (
    "important_question|mystery_element|emotional_context|turning_point|"
    "reveal_candidate|frequently_emphasized_detail|commonly_omitted_detail|"
    "viewer_context|narrative_transition_topic"
)


def _claim_nv(claim_type: str) -> str | None:
    """Map claim_type onto the Fact.narrative_value vocabulary used by the
    capacity estimator and evidence pack."""
    return {
        "human_detail": "human_detail",
        "scene_detail": "scene_detail",
        "investigation_detail": "investigation_detail",
        "physical_evidence": "physical_evidence",
        "legal": "legal",
        "historical_context": "historical_context",
        "quote": "quote",
        "timeline_event": "timeline",
    }.get(claim_type)


class TranscriptIntelligenceAgent:
    def __init__(self):
        self.gen = get_generation_provider()

    async def extract(
        self, db: Session, case: Case, video: VideoSource
    ) -> dict:
        """Run extraction over all chunks of one video's transcript.

        Idempotent: replaces the video's claims/insights when re-run.
        Returns counts for observability."""
        db.query(TranscriptClaim).filter(
            TranscriptClaim.video_source_id == video.id
        ).delete()
        db.query(NarrativeInsight).filter(
            NarrativeInsight.video_source_id == video.id
        ).delete()
        db.flush()

        n_claims = n_insights = 0
        for chunk in transcript_chunks(video):
            claims, insights = await self._extract_chunk(
                db, case, video, chunk
            )
            n_claims += claims
            n_insights += insights
        db.flush()
        return {"claims": n_claims, "insights": n_insights}

    async def _extract_chunk(
        self, db: Session, case: Case, video: VideoSource, chunk: dict
    ) -> tuple[int, int]:
        system = f"""You are a transcript intelligence analyst for a true-crime
documentary newsroom. You receive one timed excerpt of ONE video's
transcript. Extract structured intelligence — nothing else.

RULES:
- Extract factual claims ONLY when the excerpt actually states them. Never
  invent, infer or embellish.
- A creator narrating a claim is NOT evidence of it: mark certainty
  "unverified" unless the excerpt cites an identifiable primary/official
  source.
- For quotes: classify quote_classification as source_document_quote |
  interview_quote | reported_quote | creator_narration | uncertain. The
  narrator's own phrasing is creator_narration — never a historical quote.
- canonical_claim_en is ALWAYS English (the canonical analysis language).
  Keep original_claim in the transcript language.
- Preserve provenance: every claim must carry the segment indices it came
  from. Use ONLY segment indices from the supplied list.
- Narrative insights capture WHY the video is interesting (questions it
  raises, framing choices, emphasized/omitted details) as abstract
  information — never copy the creator's wording, hooks or phrasing.
- Timestamps are attached automatically via segment indices — do not
  invent times.

CLAIM TYPES: {_CLAIM_TYPES}
INSIGHT TYPES: {_INSIGHT_TYPES}

Return JSON only."""
        user = json.dumps(
            {
                "case": case.canonical_title,
                "video": {
                    "id": video.id,
                    "title": video.title,
                    "channel": video.channel_name,
                    "language": video.language,
                },
                "chunk": {
                    "segment_indices": [
                        i for i, s in enumerate(chunk["segment_ids"])
                    ],
                    "segment_db_ids": chunk["segment_ids"],
                    "start_seconds": chunk["start_seconds"],
                    "end_seconds": chunk["end_seconds"],
                    "text": chunk["text"],
                },
                "output_schema": {
                    "claims": [
                        {
                            "canonical_claim_en": "...",
                            "original_claim": "transcript language or null",
                            "claim_type": "one of the claim types",
                            "confidence": 0.0,
                            "certainty": "unverified|supported|disputed|...",
                            "speaker": "or null",
                            "quote_classification": "or null",
                            "segment_indices": [0],
                        }
                    ],
                    "narrative_insights": [
                        {
                            "type": "one of the insight types",
                            "canonical_text_en": "abstract insight, English",
                            "segment_indices": [0],
                        }
                    ],
                },
            },
            ensure_ascii=False,
        )
        with track_run(
            db, case.id, "Transcript Intelligence",
            input_summary=(
                f"video={video.id} chunk={chunk['start_seconds']:.0f}-"
                f"{chunk['end_seconds']:.0f}s"
            ),
        ) as run:
            data, res = await self.gen.generate_structured(
                "transcript_intelligence_extractor", system, user
            )
            stamp_run(run, res, "transcript_intelligence_extractor")

            seg_ids = chunk["segment_ids"]
            n_claims = 0
            for c in data.get("claims") or []:
                claim_en = (c.get("canonical_claim_en") or "").strip()
                if not claim_en:
                    continue
                idx = [
                    seg_ids[i] for i in (c.get("segment_indices") or [])
                    if isinstance(i, int) and 0 <= i < len(seg_ids)
                ]
                segs = [s for s in video.segments if s.id in set(idx)]
                db.add(
                    TranscriptClaim(
                        case_id=case.id,
                        video_source_id=video.id,
                        segment_ids_json=json.dumps(idx),
                        original_language=video.language or "en",
                        original_claim=(c.get("original_claim") or None),
                        canonical_claim_en=claim_en,
                        claim_type=(c.get("claim_type") or "fact")[:60],
                        confidence=min(max(float(c.get("confidence", 0.5)), 0.0), 1.0),
                        certainty=(c.get("certainty") or "unverified")[:40],
                        speaker=(c.get("speaker") or None),
                        quote_classification=(
                            c.get("quote_classification") or None
                        ),
                        timestamp_start=(
                            min((s.start_seconds for s in segs), default=None)
                        ),
                        timestamp_end=(
                            max((s.end_seconds for s in segs), default=None)
                        ),
                    )
                )
                n_claims += 1
            n_insights = 0
            for ins in data.get("narrative_insights") or []:
                text_en = (ins.get("canonical_text_en") or "").strip()
                if not text_en:
                    continue
                idx = [
                    seg_ids[i] for i in (ins.get("segment_indices") or [])
                    if isinstance(i, int) and 0 <= i < len(seg_ids)
                ]
                db.add(
                    NarrativeInsight(
                        case_id=case.id,
                        video_source_id=video.id,
                        insight_type=(ins.get("type") or "important_question")[:60],
                        canonical_text_en=text_en,
                        segment_ids_json=json.dumps(idx),
                        language=video.language or "unknown",
                    )
                )
                n_insights += 1
            run.output_summary = f"claims={n_claims} insights={n_insights}"
        return n_claims, n_insights
