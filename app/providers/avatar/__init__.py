from app.core.ai_config import ai_config
from app.providers.avatar.base import AvatarProvider, AvatarProviderError


def get_avatar_provider(key_env: str | None = None) -> AvatarProvider:
    if ai_config.avatar.provider == "heygen":
        from app.providers.avatar.heygen import HeyGenAvatarProvider

        return HeyGenAvatarProvider(key_env=key_env)
    raise AvatarProviderError("invalid_request",
                              f"unknown avatar provider {ai_config.avatar.provider!r}")


__all__ = ["AvatarProvider", "AvatarProviderError", "get_avatar_provider"]
