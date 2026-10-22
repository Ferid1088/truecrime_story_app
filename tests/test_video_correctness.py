import asyncio
import json
import uuid

from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary.visuals import pieces
from app.documentary.visuals.video_auditor import VideoProbeError, _source_length, describe_decide


def _v(**kw):
    return {"matches_claim": "yes", "period_ok": "yes", "confidence": 0.5, "subject_type": "building",
            "tone_ok": True, "text_or_logo_in_any_frame": False,
            "graphic_or_sensitive_in_any_frame": False, "cut_ok": True, "description_ok": True, **kw}


def test_a_video_piece_uses_the_same_thresholds_as_a_picture():
    """Owner decision 2026-10-22: the visual_verification thresholds apply to video pieces too,
    through describe_decide() -> verify_decide() (app/documentary/visuals/verification.decide)."""
    cfg = ai_config.visual_verification
    assert describe_decide(_v(confidence=cfg.verified_min_confidence))[0] == "verified"
    assert describe_decide(_v(confidence=cfg.verified_min_confidence - 0.01))[0] == "needs_review"
    assert describe_decide(_v(confidence=0.49))[0] == "needs_review"
    assert describe_decide(_v(confidence=0.5))[0] == "verified"


def test_a_watermarked_video_frame_stays_rejected_whatever_the_confidence():
    out = describe_decide(_v(confidence=0.99, text_or_logo_in_any_frame=True))
    assert out[0] == "rejected" and out[2] == "watermark"


def test_duration_probe_failure_is_explicit(monkeypatch, db_session):
    case = Case(canonical_title=f"Probe {uuid.uuid4().hex[:6]}",
                slug=f"probe-{uuid.uuid4().hex[:6]}", language="en")
    db_session.add(case)
    db_session.commit()
    asset = VisualAsset(case_id=case.id, asset_code="V1", asset_type="video",
                        local_path="missing.mp4", clip_start=0, clip_end=5)
    db_session.add(asset)
    db_session.commit()
    monkeypatch.setattr("app.documentary.visuals.footage.probe_video", lambda _path: None)
    try:
        _source_length(asset)
    except VideoProbeError as exc:
        assert "duration probe" in str(exc)
    else:
        raise AssertionError("duration probe failure was silently converted to zero")


def test_video_auditor_rejects_piece_when_duration_probe_fails(monkeypatch, db_session):
    from app.agents.video_auditor import VideoAuditor

    case = Case(canonical_title=f"Reject {uuid.uuid4().hex[:6]}",
                slug=f"reject-{uuid.uuid4().hex[:6]}", language="en")
    db_session.add(case)
    db_session.commit()
    asset = VisualAsset(case_id=case.id, asset_code="V2", asset_type="video",
                        local_path="missing.mp4", thumbnail_path="thumb.jpg",
                        clip_start=0, clip_end=5, spec_json=json.dumps({"parent": "V0"}))
    db_session.add(asset)
    db_session.commit()
    monkeypatch.setattr("app.documentary.visuals.footage.probe_video", lambda _path: None)
    got = asyncio.run(VideoAuditor(gen=object(), frames=lambda _asset: [b"frame"]).describe(
        db_session, case, asset, [], []))
    assert got.verification_status == "rejected"
    assert json.loads(got.verification_json)["reason"] == "duration_probe_failed"


def test_automatic_cut_has_two_corrections_but_manual_cut_is_allowed(db_session, monkeypatch):
    case = Case(canonical_title=f"Cut {uuid.uuid4().hex[:6]}",
                slug=f"cut-{uuid.uuid4().hex[:6]}", language="en")
    db_session.add(case)
    db_session.commit()
    source = VisualAsset(case_id=case.id, asset_code="V1", asset_type="video_source",
                         local_path="missing.mp4", duration_seconds=10,
                         spec_json=json.dumps({"scene_changes": []}))
    db_session.add(source)
    db_session.commit()

    class FailingSegmenter:
        def __init__(self):
            self.calls = 0

        async def cut(self, *_args):
            self.calls += 1
            raise RuntimeError("segmenter unavailable")

    segmenter = FailingSegmenter()
    monkeypatch.setattr(ai_config.documentary, "max_redos", 2)
    for _ in range(3):
        assert asyncio.run(pieces.cut_video(db_session, case, source, segmenter)) == []
    spec = json.loads(source.spec_json)
    assert segmenter.calls == 3
    assert spec["segment_status"] == "rejected" and spec["segment_left_out"] is True
    assert [a["mode"] for a in spec["segment_history"]] == ["automatic"] * 3

    assert asyncio.run(pieces.cut_video(db_session, case, source, segmenter, manual=True)) == []
    spec = json.loads(source.spec_json)
    assert segmenter.calls == 4
    assert spec["segment_history"][-1]["mode"] == "manual"
