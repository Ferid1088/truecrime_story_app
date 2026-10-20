"""Per-platform publish manifest for a host-led reel: what is automatic, what needs a manual
step, and the film it is cut from. Verified-as-of 2026-10-20 (see docs/shorts_host_led_checklist.md
for sources, re-verify against the official pages before relying on this in production):

  - YouTube Shorts: a "related video" link below the channel handle is an owner-set field in
    YouTube Studio (Content -> the Short -> Details -> Related video), restricted to a public or
    unlisted video of the SAME channel. No YouTube Data API field for it was found in the
    `videos` resource or its revision history, so it cannot be automated; the manifest emits a
    checklist item instead (S6).
  - Instagram Reels: no clickable link in the video or caption (any account type); the caption
    carries a text call-to-action plus "link in bio" guidance.
  - TikTok: the caption is plain text (not clickable); only the profile's Website field, set
    once in the app, is clickable. The manifest never promises a caption link.
  - Facebook Reels: secondary sources (not Meta's own docs) say the caption CAN hold a clickable
    link, unlike Instagram/TikTok, but this is unconfirmed against an official source — the
    manifest marks it a checklist item to verify manually, not an automatic claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.core.ai_config import ai_config
from app.shortform.host_led_disclosure import check_host_led_disclosure

# Where each platform's own "AI-generated / altered content" toggle lives today (2026-10-20):
# every one of these is a manual, per-upload toggle in that platform's own app/studio, not a
# field this codebase's publish-manifest stub can set through any public API.
_DISCLOSURE_TOGGLE_LOCATION = {
    "youtube_short": "YouTube Studio upload flow: 'Altered or synthetic content' toggle",
    "instagram_reel": "Instagram app: post settings -> 'AI info' / 'Label as AI-generated'",
    "tiktok_video": "TikTok app: post settings -> 'AI-generated content' toggle",
    "facebook_reel": "Facebook app: post settings -> 'AI info' label",
}


@dataclass(frozen=True)
class PublishManifest:
    platform: str
    film_id: int
    channel: str
    language: str
    caption: str
    automatic: dict = field(default_factory=dict)
    manual_steps: list[dict] = field(default_factory=list)
    music_attribution: str | None = None
    disclosure: dict = field(default_factory=dict)
    closing: dict = field(default_factory=dict)


def _caption_with_cta(language: str, channel: str) -> str:
    cta = {
        "en": f"Full documentary on YouTube - {channel}",
        "de": f"Die ganze Doku auf YouTube - {channel}",
        "fa": f"مستند کامل در یوتیوب - {channel}",
        "ar": f"الوثائقي الكامل على يوتيوب - {channel}",
    }.get(language, f"Full documentary on YouTube - {channel}")
    return cta


def _with_music_attribution(manifest: PublishManifest, language: str,
                            uses_generated_music: bool) -> PublishManifest:
    """Whenever a reel ever uses generated music, the provider's required attribution line
    (config shorts_host_led.music_attribution_text) rides along in the caption and the
    manifest's own field — never only in one place, so a platform that strips one still shows
    the other where possible."""
    if not uses_generated_music:
        return manifest
    text = ai_config.shorts_host_led.music_attribution_text.get(language)
    if not text:
        raise ValueError(f"no music_attribution_text configured for language {language!r}, "
                         "but uses_generated_music=True")
    return replace(manifest, caption=f"{manifest.caption} — {text}", music_attribution=text)


def build_publish_manifest(platform: str, *, film_id: int, short_id: str, language: str,
                           uses_generated_music: bool = False,
                           closing: dict | None = None) -> PublishManifest:
    channel = ai_config.shorts_host_led.channel_name.get(language, "")
    caption = _caption_with_cta(language, channel)
    if platform == "youtube_short":
        manifest = PublishManifest(
            platform=platform, film_id=film_id, channel=channel, language=language,
            caption=caption, automatic={"upload": True, "captions_burned_in": True},
            manual_steps=[{
                "action": "set_related_video", "where": "YouTube Studio: Content -> the Short -> "
                "Details -> Related video", "short_id": short_id, "film_id": film_id,
                "film_title": channel, "reason": "no YouTube Data API field found for the Shorts "
                "related-video link (checked videos.update field list and quota docs, "
                "2026-10-20); must be set by hand, once per Short",
            }],
        )
    elif platform == "instagram_reel":
        manifest = PublishManifest(
            platform=platform, film_id=film_id, channel=channel, language=language,
            caption=caption, automatic={"upload": True, "caption": True},
            manual_steps=[{
                "action": "optional_story_share_link", "where": "Instagram Stories link sticker",
                "reason": "Reels themselves never carry a tappable URL (caption or video); "
                "the only link option is a manual Story share",
            }],
        )
    elif platform == "tiktok_video":
        manifest = PublishManifest(
            platform=platform, film_id=film_id, channel=channel, language=language,
            caption=caption, automatic={"upload": True, "caption": True},
            manual_steps=[{
                "action": "confirm_profile_link", "where": "TikTok profile Website field",
                "reason": "caption/comment links are plain text, never clickable; only the "
                "profile's own Website field is, and it is set once, not per video",
            }],
        )
    elif platform == "facebook_reel":
        manifest = PublishManifest(
            platform=platform, film_id=film_id, channel=channel, language=language,
            caption=caption, automatic={"upload": True, "caption": True},
            manual_steps=[{
                "action": "verify_caption_link_clickable", "where": "Facebook app, after posting",
                "reason": "secondary sources claim Facebook Reel captions (unlike Instagram/"
                "TikTok) accept a clickable link, but this is not confirmed against Meta's own "
                "documentation — verify on one real post before relying on it",
            }],
        )
    else:
        raise ValueError(f"unknown platform {platform!r}")
    manifest = _with_music_attribution(manifest, language, uses_generated_music)
    gate = check_host_led_disclosure(
        platform=platform, disclosure_flags={}, uses_generated_music=uses_generated_music)
    return replace(
        manifest,
        manual_steps=[*manifest.manual_steps, {
            "action": "set_ai_disclosure_toggle",
            "where": _DISCLOSURE_TOGGLE_LOCATION[platform],
            "reason": "the avatar's voice and video are always synthetic" + (
                " and this reel uses generated music" if uses_generated_music else "") +
                "; every platform listed here only exposes this as a manual per-upload toggle, "
                "not a public API field, so DisclosureChecker flags it here rather than "
                "claiming it was set automatically",
        }],
        disclosure={
            "synthetic_avatar_voice_and_video": True,
            "synthetic_music": uses_generated_music,
            "disclosed_via_automatic_call": gate.passed,
            "requires_disclosure": gate.metadata.get("requires_disclosure", True),
        },
        closing=closing or {},
    )
