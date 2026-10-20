"""S10: the existing disclosure gate (``app/shortform/gates.py:disclosure_checker``), applied
unchanged, to the two things a host-led reel can show that a human did not actually do: the
synthetic avatar's voice and video (always, every reel — Fereidoun is always an AI avatar), and
generated music (only when one is actually used, which is never true while S7 stays blocked)."""

from __future__ import annotations

from app.shortform.gates import AssetUse, GateResult, disclosure_checker


def host_led_assets(*, uses_generated_music: bool) -> list[AssetUse]:
    assets = [AssetUse(asset_id="avatar_video", media_kind="visual",
                       requires_ai_disclosure=True)]
    if uses_generated_music:
        assets.append(AssetUse(asset_id="generated_music", media_kind="audio",
                               requires_ai_disclosure=True))
    return assets


def check_host_led_disclosure(*, platform: str, disclosure_flags: dict[str, bool],
                              uses_generated_music: bool = False) -> GateResult:
    """``passed`` is False until the caller's ``disclosure_flags`` actually marks ``platform``
    as disclosed — the avatar is ALWAYS synthetic, so this gate can never be satisfied by
    omission."""
    return disclosure_checker(
        assets=host_led_assets(uses_generated_music=uses_generated_music),
        synthetic_voice=True, disclosure_flags=disclosure_flags, platform=platform,
    )
