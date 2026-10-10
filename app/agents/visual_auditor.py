"""The VisualAuditor agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.visuals.auditor."""

from __future__ import annotations

from app.agents.runner import run_agent
import json
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.models import Case, VisualAsset, VisualAudit
from app.documentary.visuals import images as IM
from app.documentary.visuals import rights as R
from app.documentary.visuals import spoilers as SP
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.documentary.visuals.auditor import (
    AUDITOR_SYSTEM,
    _loads,
    _picture,
    audit_key,
    decide,
    log,
)


class VisualAuditor:
    def __init__(self, gen=None, verify=None, video=None):
        self.gen = gen or get_generation_provider()
        # verify(db, case, [assets]) — vision-check unverified assets first
        self._verify = verify
        self._video = video  # the video auditor (frame by frame) for clips

    async def verdict(self, db: Session, case: Case, asset: VisualAsset,
                      sentences: list[str], story: dict | None = None) -> VisualAudit:
        key = audit_key(asset, sentences, story)
        row = db.query(VisualAudit).filter(VisualAudit.key == key).first()
        if row is not None:
            return row
        if asset.asset_type == "video":
            # a clip is judged as video — every frame, in order
            from app.documentary.visuals.video_auditor import VideoAuditor

            self._video = self._video or VideoAuditor(gen=self.gen)
            v, res = await self._video.placement(db, case, asset, sentences, story)
            return self._store(db, case, asset, key, sentences, v, res)
        thumb = _picture(asset)
        payload = {
            "case": case.canonical_title,
            "case_status": getattr(case, "resolution_status", None) or "UNKNOWN",
            "media": "photo",
            "narration_while_on_screen": sentences,
            "story": story or {},
            "what_the_picture_is_claimed_to_be": {
                "title": asset.title, "caption": asset.caption,
                "found_for": asset.found_for, "source": asset.source_name,
                "verifier_saw": asset.description, "role": asset.asset_role,
            },
        }
        with track_run(db, case.id, "Visual Auditor", input_summary=asset.asset_code) as run:
            v, res = await run_agent("visuals.audit", self.gen, json.dumps(payload, ensure_ascii=False), system=AUDITOR_SYSTEM, images=[IM.data_url(thumb)])
            stamp_run(run, res, "visual_auditor")
        return self._store(db, case, asset, key, sentences, v, res)

    def _store(self, db: Session, case: Case, asset: VisualAsset, key: str,
               sentences: list[str], v, res) -> VisualAudit:
        v = v if isinstance(v, dict) else {}
        verdict, shown_as, reasons = decide(v)
        row = VisualAudit(case_id=case.id, asset_code=asset.asset_code, key=key,
                          sentences_json=json.dumps(sentences, ensure_ascii=False),
                          verdict=verdict, shown_as=shown_as,
                          reasons_json=json.dumps(reasons, ensure_ascii=False),
                          detail_json=json.dumps(v, ensure_ascii=False),
                          model=getattr(res, "model", None))
        db.add(row)
        try:
            db.commit()
        except IntegrityError:  # another language audited it at the same time
            db.rollback()
            row = db.query(VisualAudit).filter(VisualAudit.key == key).one()
        return row

    async def check(self, db: Session, case: Case, asset: VisualAsset,
                    sentences: list[str], story: dict | None = None,
                    firewall: SP.Firewall | None = None
                    ) -> tuple[str, str | None, list[str]]:
        """(verdict, shown_as, reasons) for one placement at one point of
        the story — asset verification first, the deterministic spoiler
        firewall, then the auditor."""
        if asset.verification_status == "unverified" and self._verify is not None:
            try:
                await self._verify(db, case, [asset])
                db.refresh(asset)
            except Exception as e:  # noqa: BLE001 — stays unverified -> rejected below
                log.warning("verify %s before audit failed: %s", asset.asset_code, e)
        if asset.verification_status != "verified":
            return "rejected", None, [f"picture not verified ({asset.verification_status})"]
        if not R.allowed(asset.rights_status):
            return "rejected", None, [f"rights do not allow it ({asset.rights_status})"]
        early = firewall.why((story or {}).get("beat"), asset) if firewall else None
        if early:
            return "rejected", None, [early]
        if not sentences and not (story or {}).get("told_later"):
            # nothing is spoken over it and nothing is still to be revealed
            return "approved", ("symbolic" if asset.asset_role == "illustration"
                                else asset.asset_role), ["no words over this shot"]
        # (a pause before a later reveal is still checked for spoilers)
        row = await self.verdict(db, case, asset, sentences, story)
        return row.verdict, row.shown_as, _loads(row.reasons_json, [])
