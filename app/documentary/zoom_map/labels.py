"""Place labels of the map in the film's language: name:<lang> with a configured fallback chain.

A label is always ONE name from the chain (never "Name (نام)"), so one label never mixes scripts."""

from __future__ import annotations

from app.core.ai_config import ZoomMapConfig, ai_config


def chain(language: str, cfg: ZoomMapConfig | None = None) -> list[str]:
    cfg = cfg or ai_config.zoom()
    chains = cfg.labels.chains
    return list(chains.get(language) or chains["en"])


def text_field(language: str, cfg: ZoomMapConfig | None = None) -> list:
    """The MapLibre expression for the label: the first name of the chain a feature has."""
    return ["coalesce", *[["get", key] for key in chain(language, cfg)]]


def is_rtl(language: str, cfg: ZoomMapConfig | None = None) -> bool:
    cfg = cfg or ai_config.zoom()
    return language in cfg.labels.rtl_languages


def pick(properties: dict, language: str, cfg: ZoomMapConfig | None = None) -> str | None:
    """What the expression shows for a feature (used by tests and to check what a map labels)."""
    for key in chain(language, cfg):
        value = properties.get(key)
        if value:
            return str(value)
    return None
