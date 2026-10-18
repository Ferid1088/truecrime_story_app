"""Two kinds of video: research only, or a candidate for our film.

Every video the research finds is read for its transcript and never shown. Only a video from
an open archive or a public body with a stated open license or public-domain mark, or a
Creative Commons video from a non-true-crime channel that is not a re-uploader, can be a film
candidate. The videos of other true-crime channels are always research only; doubt means
research only. Config: `video_rights`."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from app.core.ai_config import ai_config
from app.documentary.visuals import rights
from app.utils import utc_now

RESEARCH_ONLY, FILM_CANDIDATE = "research_only", "film_candidate"


@dataclass(frozen=True)
class VideoUse:
    use: str
    rights_status: str
    reason: str
    license_evidence_url: str | None = None
    attribution_text: str | None = None

    def fields(self) -> dict:
        """The columns to store on a video."""
        return {"use": self.use, "rights_status": self.rights_status,
                "use_reason": self.reason, "license_evidence_url": self.license_evidence_url,
                "license_checked_at": utc_now() if self.license_evidence_url else None,
                "attribution_text": self.attribution_text}


def _host(url: str | None) -> str:
    try:
        return (urlparse(url or "").hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _listed(host: str, hosts: dict[str, str]) -> str | None:
    """The display name of the listed host this host is (or is a sub-domain of)."""
    return next((name for h, name in hosts.items() if host == h or host.endswith("." + h)), None)


class VideoUseClassifier:
    def __init__(self):
        self.cfg = ai_config.video_rights

    def _research_only(self, key: str, status: str = "unknown") -> VideoUse:
        return VideoUse(RESEARCH_ONLY, status, self.cfg.reasons[key])

    def classify(self, candidate: dict) -> VideoUse:
        hosts = self.cfg.film_candidate_hosts
        host = _host(candidate.get("url"))
        license_text = str(candidate.get("license") or "")
        evidence = next((candidate.get(k) for k in self.cfg.evidence_url_fields
                         if candidate.get(k)), None)
        status, _ = rights.classify(
            "open_archive" if _listed(host, hosts.open_archives) else "web", license_text,
            candidate.get("url"))

        archive = _listed(host, hosts.open_archives) or _listed(host, hosts.public_archives)
        if archive:
            if not evidence or not self._open(license_text, status):
                return self._research_only("no_license_evidence", status)
            return VideoUse(FILM_CANDIDATE, status, f"{archive}: {license_text or 'open license'}",
                            evidence, f"{candidate.get('title') or 'Video'} / {archive}")
        broadcaster = _listed(host, hosts.public_broadcasters)
        if broadcaster:
            if not evidence:
                return self._research_only("broadcaster_without_license", status)
            return VideoUse(FILM_CANDIDATE, "licensed", f"{broadcaster}: stored license page",
                            evidence, f"{candidate.get('title') or 'Video'} / {broadcaster}")
        if host in ("youtube.com", "youtu.be", "m.youtube.com"):
            return self._youtube(candidate, evidence)
        return self._research_only("unknown_host")

    def _open(self, license_text: str, status: str) -> bool:
        return status in ("public_domain", "creative_commons") or any(
            marker in license_text.lower() for marker in self.cfg.open_license_markers)

    def _youtube(self, candidate: dict, evidence: str | None) -> VideoUse:
        cfg = self.cfg.youtube
        if (candidate.get("classification") or "unknown") not in cfg.film_candidate_classifications:
            return self._research_only("other_channel")
        channel = str(candidate.get("channel_name") or "").lower()
        if any(p in channel for p in cfg.reuploader_channel_patterns):
            return self._research_only("reuploader")
        if candidate.get("license") != cfg.film_candidate_license:
            return self._research_only("youtube_not_cc")
        if not evidence:
            return self._research_only("no_license_evidence")
        return VideoUse(FILM_CANDIDATE, "creative_commons",
                        f"YouTube Data API license {cfg.film_candidate_license}", evidence,
                        f"{candidate.get('title') or 'Video'} / {candidate.get('channel_name')}")


class RightsRefused(RuntimeError):
    """A video may not be downloaded for rendering; the message says why."""


class VideoRightsGate:
    """Nothing is downloaded for rendering unless the video is a film candidate, its rights are
    allowed for the render profile and its license evidence is stored. A research-only video is
    never downloaded; only its transcript is read."""

    def check(self, video, profile: str | None = None) -> str | None:
        """None when the video may be downloaded, else the reason it may not."""
        if getattr(video, "use", RESEARCH_ONLY) != FILM_CANDIDATE:
            return "research only: " + (getattr(video, "use_reason", None) or "not a film candidate")
        if not rights.allowed(video.rights_status, profile):
            return f"rights status {video.rights_status!r} is not allowed for this render profile"
        if not video.license_evidence_url or video.license_checked_at is None:
            return "no stored license evidence"
        return None

    def require(self, video, profile: str | None = None) -> None:
        reason = self.check(video, profile)
        if reason is not None:
            raise RightsRefused(reason)
