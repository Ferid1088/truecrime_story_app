"""Video auditor: video pieces are judged as VIDEO, frame by frame.

A still (or three stills) cannot tell what a clip shows a few seconds
later: a caption that fades in, a logo bug, a face that turns to the
camera, an injury in the last second, a cut to an unrelated scene. The
video auditor (role video_auditor, a vision model independent of the
visual director) gets the piece's frames in order — video_audit.
frames_per_second, at most max_frames — and two jobs:

  describe(): when a piece enters the library — is the CUT right (a
      complete meaningful moment), are its NAME and DESCRIPTION right
      (exactly what is visible), and the checks of the picture verifier
      on EVERY frame (burned-in text, graphic content, period, tone).
      Corrections (a better cut, a correct description) are applied and
      checked again; the result is the piece's verification.
  placement(): before render — may THIS piece play while THESE words
      are spoken? Same strict rules as the picture auditor, every frame.
"""

from __future__ import annotations


from app.core.prompts import prompt

import base64
import json


from app.db.models import VisualAsset
from app.documentary.visuals.verification import decide as verify_decide

DESCRIBE_SYSTEM = prompt("documentary/visuals/video_auditor/describe_system")

PLACEMENT_SYSTEM = prompt("documentary/visuals/video_auditor/placement_system")


def _urls(frames: list[bytes]) -> list[str]:
    return ["data:image/jpeg;base64," + base64.b64encode(f).decode() for f in frames]


def piece_parent(asset: VisualAsset) -> str | None:
    spec = json.loads(asset.spec_json or "{}") if asset.spec_json else {}
    return spec.get("parent")


def _source_length(asset: VisualAsset) -> float:
    try:
        from app.documentary import storage
        from app.documentary.visuals.footage import probe_video

        info = probe_video(storage.resolve(asset.local_path))
        return float((info or {}).get("duration") or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _limits(asset: VisualAsset, slack: float = 5.0) -> tuple[float, float]:
    """How far the auditor may move a piece's cut (seconds of the video)."""
    a, b = float(asset.clip_start or 0.0), float(asset.clip_end or 0.0)
    total = _source_length(asset) or b
    return round(max(0.0, a - slack), 3), round(min(total, b + slack), 3)


def _piece_times(asset: VisualAsset, n: int) -> list[float]:
    a, b = float(asset.clip_start or 0.0), float(asset.clip_end or 0.0)
    length = max(b - a, 0.1)
    return [a + length * (i + 0.5) / n for i in range(n)]


def describe_decide(v: dict) -> tuple[str, float, str | None]:
    """Verification status of a piece: the picture verifier's rules, with
    every-frame flags (text/logo, gore) and tone."""
    flags = dict(v)
    flags["watermark"] = bool(v.get("text_or_logo_in_any_frame") or v.get("watermark"))
    flags["graphic_or_sensitive"] = bool(v.get("graphic_or_sensitive_in_any_frame")
                                         or v.get("graphic_or_sensitive"))
    return verify_decide(flags)


def __getattr__(name: str):
    # the agent class lives in app/agents/video_auditor.py (imported when first asked for)
    if name == "VideoAuditor":
        from app.agents.video_auditor import VideoAuditor

        return VideoAuditor
    raise AttributeError(name)
