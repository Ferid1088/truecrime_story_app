"""Two kinds of video: research only, or a candidate for our film.

Every video the research finds is read for its transcript and never shown unless it belongs to an
allowed source class, with the evidence that put it there (stored with the video):
  open_archive / public_archive / public_broadcaster  a listed host with an open licence or mark
  official_body    police, courts, prosecutors, government: by channel kind or name, or the host
  news_raw         a news agency's or broadcaster's raw source footage (its channel AND a raw marker)
  witness_family   a witness's or family's own video, by the video's own words
  creative_commons a YouTube Creative Commons video of a channel that is not a commentator
Other creators' edited films (documentary, narration, analysis) and re-uploaders are always research
only; doubt means research only. Config: `video_rights`."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from app.core.ai_config import ai_config
from app.documentary.visuals import rights
from app.utils import utc_now

RESEARCH_ONLY, FILM_CANDIDATE = "research_only", "film_candidate"
TRUE_CRIME_CHANNEL = "true_crime_channel"      # the source class of the owner's allowance for other channels' films


@dataclass(frozen=True)
class VideoUse:
    use: str
    rights_status: str
    reason: str
    license_evidence_url: str | None = None
    attribution_text: str | None = None
    source_class: str | None = None
    class_evidence: str | None = None

    def fields(self) -> dict:
        """The columns to store on a video."""
        return {"use": self.use, "rights_status": self.rights_status,
                "use_reason": self.reason, "license_evidence_url": self.license_evidence_url,
                "license_checked_at": utc_now() if self.license_evidence_url else None,
                "attribution_text": self.attribution_text,
                "source_class": self.source_class, "class_evidence": self.class_evidence}


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

        archive = _listed(host, hosts.open_archives)
        public = _listed(host, hosts.public_archives)
        if archive or public:
            name = archive or public
            if not evidence or not self._open(license_text, status):
                return self._research_only("no_license_evidence", status)
            return VideoUse(FILM_CANDIDATE, status, f"{name}: {license_text or 'open license'}",
                            evidence, f"{candidate.get('title') or 'Video'} / {name}",
                            "open_archive" if archive else "public_archive", f"host {host}")
        broadcaster = _listed(host, hosts.public_broadcasters)
        if broadcaster:
            if not evidence:
                return self._research_only("broadcaster_without_license", status)
            return VideoUse(FILM_CANDIDATE, "licensed", f"{broadcaster}: stored license page",
                            evidence, f"{candidate.get('title') or 'Video'} / {broadcaster}",
                            "public_broadcaster", f"host {host}")
        if host in ("youtube.com", "youtu.be", "m.youtube.com"):
            return self._youtube(candidate, evidence)
        official = self.cfg.source_classes.get("official_body")
        if official and any(host == h or host.endswith("." + h) for h in official.hosts):
            return VideoUse(FILM_CANDIDATE, "editorial_review_required", f"official body: host {host}",
                            None, f"{candidate.get('title') or 'Video'} / {host}", "official_body",
                            f"direct file on the official host {host}")
        return self._research_only("unknown_host")

    def _open(self, license_text: str, status: str) -> bool:
        return status in ("public_domain", "creative_commons") or any(
            marker in license_text.lower() for marker in self.cfg.open_license_markers)

    def _candidate(self, candidate: dict, cls: str, why: str, evidence: str | None) -> VideoUse:
        """A film candidate of a class: rights from the licence when it is open, else the public
        material of an official, news or witness source (no licence is claimed)."""
        cc = candidate.get("license") == self.cfg.youtube.film_candidate_license and evidence
        status = "creative_commons" if cc else "editorial_review_required"
        reason = f"{cls.replace('_', ' ')}: {why}"
        channel = candidate.get("channel_name")
        return VideoUse(FILM_CANDIDATE, status, reason, evidence if cc else None,
                        f"{candidate.get('title') or 'Video'} / {channel}", cls, why)

    def _youtube(self, candidate: dict, evidence: str | None) -> VideoUse:
        cfg, classes = self.cfg.youtube, self.cfg.source_classes
        kind = candidate.get("classification") or "unknown"
        channel = str(candidate.get("channel_name") or "").lower()
        title = " ".join(str(candidate.get(k) or "") for k in ("title", "description")).lower()
        if kind in self.cfg.research_only_classifications:
            if cfg.true_crime_allowance.enabled:
                return self._candidate(candidate, TRUE_CRIME_CHANNEL,
                                       f"the owner allows {cfg.true_crime_allowance.max_videos_per_case} short clips "
                                       "of true-crime channels", None)
            return self._research_only("other_creator_edited")
        if any(p in channel for p in cfg.reuploader_channel_patterns):
            return self._research_only("reuploader")
        # P2: a channel automatically proven an official body (stored proof with a domain
        # link) is an official source regardless of whether its name carries a marker word.
        proof = candidate.get("channel_proof")
        if isinstance(proof, dict) and proof.get("status") == "proven":
            checks = [k for k, v in (proof.get("checks") or {}).items() if v]
            return self._candidate(candidate, "official_body",
                                   "the channel is proven official: " + ", ".join(checks),
                                   evidence)
        official = classes.get("official_body")
        if official:
            marker = next((m for m in official.channel_markers if m in channel), None)
            if marker:        # the channel's own name decides: the kind alone is a guess ("Court TV Podcast")
                how = [f"channel kind {kind}"] if kind in official.classifications else []
                how.append(f"channel '{candidate.get('channel_name')}' names the official marker '{marker}'")
                return self._candidate(candidate, "official_body", "; ".join(how), evidence)
        raw = classes.get("news_raw")
        if raw and kind in raw.classifications:
            agency = next((m for m in raw.channel_markers if m in channel), None)
            marker = next((m for m in raw.title_markers if m in title), None)
            if agency and marker:
                return self._candidate(candidate, "news_raw",
                                       f"channel '{candidate.get('channel_name')}' and raw marker '{marker}' in the title",
                                       evidence)
        witness = classes.get("witness_family")
        if witness:
            marker = next((m for m in witness.title_markers if m in title), None)
            blocked = next((m for m in witness.blocked_channel_markers if m in channel), None)
            if marker and not blocked:
                return self._candidate(candidate, "witness_family", f"the video's own words: '{marker}'", evidence)
        cc = classes.get("creative_commons")
        if cc and kind in cc.classifications and candidate.get("license") == cfg.film_candidate_license:
            if not evidence:
                return self._research_only("no_license_evidence")
            return self._candidate(candidate, "creative_commons",
                                   f"YouTube Data API license {cfg.film_candidate_license}", evidence)
        return self._research_only("no_class")


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
        has_license = bool(video.license_evidence_url and video.license_checked_at is not None)
        if not has_license and not getattr(video, "class_evidence", None):
            return "no stored license evidence (and no source-class evidence)"
        return None

    def require(self, video, profile: str | None = None) -> None:
        reason = self.check(video, profile)
        if reason is not None:
            raise RightsRefused(reason)


def reclassify_case(db, case_id: int) -> dict:
    """Classify the case's stored videos again under the current source classes (a video found
    before the classes existed has none). A video the YouTube Data API licence was never stored for
    is judged without it. Returns the counts."""
    from collections import Counter

    from app.db.models import VideoSource

    classifier = VideoUseClassifier()
    uses: Counter = Counter()
    classes: Counter = Counter()
    for video in db.query(VideoSource).filter(VideoSource.case_id == case_id):
        use = classifier.classify({"url": video.url, "title": video.title, "description": video.description,
                                   "channel_name": video.channel_name, "classification": video.classification,
                                   "license_url": video.license_evidence_url,
                                   "license": video.youtube_license})
        for column, value in use.fields().items():
            setattr(video, column, value)
        uses[use.use] += 1
        if use.source_class:
            classes[use.source_class] += 1
    db.commit()
    return {**dict(uses), "by_class": dict(classes)}
