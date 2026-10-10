"""Social publisher boundary.

Only ExportPackagePublisher performs work. Platform classes are intentionally
non-posting stubs until credentials, API contracts, and separate integration
tests exist for each platform.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.shortform.operations import ExportPackagePublisher, PlatformExport


@dataclass(frozen=True)
class PublisherConfigReference:
    platform: str
    credential_env: str
    status: str = "stub"


PUBLISHER_CONFIG = {
    "youtube_short": PublisherConfigReference("youtube_short", "YOUTUBE_SHORTS_API_KEY"),
    "instagram_reel": PublisherConfigReference("instagram_reel", "INSTAGRAM_GRAPH_API_TOKEN"),
    "facebook_reel": PublisherConfigReference("facebook_reel", "META_GRAPH_API_TOKEN"),
    "tiktok_video": PublisherConfigReference("tiktok_video", "TIKTOK_CONTENT_API_TOKEN"),
}


class PlatformPublisherStub:
    config: PublisherConfigReference

    def __init__(self, config: PublisherConfigReference):
        self.config = config

    def publish(self, export: PlatformExport, approved: bool) -> str:
        raise NotImplementedError(
            f"{self.config.platform} publisher is a stub; no platform API call is implemented"
        )


class YouTubeShortsPublisher(PlatformPublisherStub):
    def __init__(self):
        super().__init__(PUBLISHER_CONFIG["youtube_short"])


class InstagramReelsPublisher(PlatformPublisherStub):
    def __init__(self):
        super().__init__(PUBLISHER_CONFIG["instagram_reel"])


class FacebookReelsPublisher(PlatformPublisherStub):
    def __init__(self):
        super().__init__(PUBLISHER_CONFIG["facebook_reel"])


class TikTokPublisher(PlatformPublisherStub):
    def __init__(self):
        super().__init__(PUBLISHER_CONFIG["tiktok_video"])
