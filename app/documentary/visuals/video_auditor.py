"""Video auditor: video pieces are judged as VIDEO, frame by frame.

A still (or three stills) cannot tell what a clip shows a few seconds
later: a caption that fades in, a logo bug, a face that turns to the
camera, an injury in the last second, a cut to an unrelated scene. The
video auditor (role video_auditor, a vision model independent of the
visual director) gets the piece's frames in order — video_audit.
frames_per_second, at most max_frames — and two jobs:

  describe(): when a piece enters the library — its name and
      description (what, who, where, when, mood), the case entities it
      shows, and the checks of the picture verifier applied to EVERY
      frame (watermark/burned-in text, graphic content, wrong period,
      tone). The result is the piece's verification.
  placement(): before render — may THIS piece play while THESE words
      are spoken? Same strict rules as the picture auditor, every frame.
"""

from __future__ import annotations

import base64
import json

from sqlalchemy.orm import Session

from app.db.models import Case, VisualAsset
from app.documentary.visuals import rights as R
from app.documentary.visuals import tiers as T
from app.documentary.visuals.pieces import piece_frames
from app.documentary.visuals.verification import decide as verify_decide
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

DESCRIBE_SYSTEM = """
You audit one VIDEO piece for a serious, factual true-crime documentary.
You get its frames IN ORDER (first to last, about one per second). Judge
the whole piece: a single bad frame fails it.

Return JSON only:
{"name": "short title of what the piece shows (max 60 chars)",
 "description": "one or two neutral sentences: what is shown, who or what,
   where and when (period, day/night, season if visible), camera (aerial,
   street, interior, handheld), mood",
 "subject_type": "person|place|building|vehicle|object|document|map|landscape|event|other",
 "matches_claim": "yes|stand_in|no|unclear",
 "role": "evidence|context|illustration",
 "entities": [], "reveals": [], "period_ok": "yes|no|unclear",
 "cuts_inside": false,
 "text_or_logo_in_any_frame": false,
 "graphic_or_sensitive_in_any_frame": false,
 "unidentified_faces": false,
 "tone_ok": true, "quality": 0.7, "confidence": 0.8,
 "problem_frames": [], "reasons": []}

matches_claim: does the piece show what it is claimed to show (title,
caption, what it was found for)? stand_in only for the SAME SPECIFIC kind
(a police dog only by a working police/K9 dog, never a pet) — never for
a person. text_or_logo_in_any_frame: burned-in captions, TV station bugs,
lower thirds, watermarks, subtitles in ANY frame. tone_ok false for
sentimental, funny, staged stock or advertising footage. problem_frames:
indices (0-based) of frames that cause a "no" or a flag.
"""

PLACEMENT_SYSTEM = """
You are the strict video auditor of a serious, factual true-crime
documentary. You see the frames of ONE video piece IN ORDER (first to
last) and the narration sentences spoken while it plays. Decide whether
playing exactly this piece during exactly these words is right. Every
frame counts. When in doubt, reject.

Reject when: any frame shows something the words are not about, or a
different / more general kind of thing (a pet for "the police dog", a
city for "the forest track"); a person appears while the words talk
about a named person and it is not clearly that person; the piece cuts
to an unrelated scene; text, captions or logos appear in any frame; the
look clashes with the seriousness (sentimental, funny, stock, advert);
it contradicts the words (night vs day, season, place, period, number of
people); gore or injuries in any frame.

Approve with "as": "evidence" (the case's own footage), "context" (the
real place / real related event), "symbolic" (an accurate, serious
depiction of exactly the kind of thing named — it will be labelled).

Return JSON only:
{"verdict": "approved", "as": "context", "fits_words": 0.9,
 "specific_kind_ok": true, "tone_ok": true, "person_ok": true,
 "every_frame_ok": true, "problem_frames": [], "reasons": ["..."]}
"""


def _urls(frames: list[bytes]) -> list[str]:
    return ["data:image/jpeg;base64," + base64.b64encode(f).decode() for f in frames]


def describe_decide(v: dict) -> tuple[str, float, str | None]:
    """Verification status of a piece: the picture verifier's rules, with
    every-frame flags (text/logo, gore) and tone."""
    flags = dict(v)
    flags["watermark"] = bool(v.get("text_or_logo_in_any_frame") or v.get("watermark"))
    flags["graphic_or_sensitive"] = bool(v.get("graphic_or_sensitive_in_any_frame")
                                         or v.get("graphic_or_sensitive"))
    return verify_decide(flags)


class VideoAuditor:
    def __init__(self, gen=None, frames=None):
        self.gen = gen or get_generation_provider()
        self._frames = frames or piece_frames

    async def describe(self, db: Session, case: Case, asset: VisualAsset,
                       entities: list[dict], facts: list[dict]) -> VisualAsset:
        """Name, describe and check a piece — its verification."""
        if not asset.thumbnail_path:  # the library's still of the clip
            from app.documentary.visuals.verification import _keyframe_thumb

            _keyframe_thumb(asset)
        frames = self._frames(asset)
        previous = json.loads(asset.verification_json or "{}") if asset.verification_json else {}
        provisional = previous.get("provisional_tier") or asset.relevance_tier
        payload = {
            "case": case.canonical_title,
            "frames": f"{len(frames)} frames in order, first to last",
            "claimed": {"title": asset.title, "caption": asset.caption,
                        "found_for": asset.found_for, "source": asset.source_name},
            "entity_list": [{"key": e["key"], "name": e["name"], "type": e["type"],
                             "period": e.get("period")} for e in entities],
            "facts": [{"id": f["id"], "claim": f["claim"]} for f in facts],
        }
        with track_run(db, case.id, "Video Auditor (describe)",
                       input_summary=asset.asset_code) as run:
            v, res = await self.gen.generate_structured(
                "video_auditor", DESCRIBE_SYSTEM, json.dumps(payload, ensure_ascii=False),
                images=_urls(frames))
            stamp_run(run, res, "video_auditor")
        v = v if isinstance(v, dict) else {}
        status, conf, reason = describe_decide(v)
        tier, tier_reason = T.verified_why(asset, {**v, "depicts": v.get("description")},
                                           provisional)
        asset.verification_status = status
        asset.verification_confidence = round(conf, 3)
        asset.relevance_tier = tier
        asset.case_relevance = T.tier_label(tier)
        name = str(v.get("name") or "").strip()
        if name:
            asset.title = name[:500]
        asset.description = str(v.get("description") or asset.description or "")[:1000] or None
        if v.get("subject_type"):
            asset.subject_type = str(v["subject_type"])[:20]
        if v.get("role") in ("evidence", "context", "illustration"):
            asset.asset_role = v["role"]
        if v.get("matches_claim") == "stand_in":
            asset.asset_role = "illustration"
        known_ents = {e["key"] for e in entities}
        ents = [e for e in v.get("entities") or [] if e in known_ents]
        if ents:
            claimed = json.loads(asset.entities_json or "[]")
            asset.entities_json = json.dumps(sorted(set(ents) | set(claimed)), ensure_ascii=False)
        known_facts = {f["id"] for f in facts}
        asset.reveals_json = json.dumps([r for r in v.get("reveals") or [] if r in known_facts])
        try:
            asset.quality_score = round(max(0.0, min(1.0, float(v.get("quality")))), 3)
        except (TypeError, ValueError):
            pass
        if v.get("text_or_logo_in_any_frame"):
            asset.rights_status, asset.rights_reason = R.classify(
                asset.provider, asset.license, asset.source_url, asset.page_url,
                watermark=True)
        asset.verification_json = json.dumps(
            {**v, "reason": reason, "frames": len(frames), "auditor": "video",
             "model": getattr(res, "model", None), "provisional_tier": provisional,
             "tier": tier, "tier_reason": tier_reason}, ensure_ascii=False)
        db.commit()
        return asset

    async def placement(self, db: Session, case: Case, asset: VisualAsset,
                        sentences: list[str]) -> tuple[dict, object]:
        """The raw verdict for one piece under these words (the caller
        decides and stores it — same rules as the picture auditor)."""
        frames = self._frames(asset)
        payload = {
            "case": case.canonical_title,
            "frames": f"{len(frames)} frames in order, first to last",
            "narration_while_on_screen": sentences,
            "what_the_piece_is_claimed_to_be": {
                "title": asset.title, "description": asset.description,
                "found_for": asset.found_for, "role": asset.asset_role},
        }
        with track_run(db, case.id, "Video Auditor (placement)",
                       input_summary=asset.asset_code) as run:
            v, res = await self.gen.generate_structured(
                "video_auditor", PLACEMENT_SYSTEM, json.dumps(payload, ensure_ascii=False),
                images=_urls(frames))
            stamp_run(run, res, "video_auditor")
        v = v if isinstance(v, dict) else {}
        if v.get("every_frame_ok") is False and v.get("verdict") == "approved":
            v = {**v, "verdict": "rejected",
                 "reasons": list(v.get("reasons") or []) + ["a frame fails the words"]}
        return v, res
