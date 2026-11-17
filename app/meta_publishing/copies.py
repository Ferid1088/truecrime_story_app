"""Where the prepared Reel copy of a clip lives: beside the original, in the configured folder (config/meta_publishing.json: instagram.prepared_copy_dir). Instagram and Facebook use the
copy when it exists; the original is never replaced."""

from __future__ import annotations

from pathlib import Path

from app.meta_publishing.config import meta_config


def prepared_path(original: Path) -> Path:
    return original.parent / meta_config().instagram.prepared_copy_dir / original.name


def prepared_copy_of(original: Path | None) -> Path | None:
    """The prepared copy of a clip file, when one has been made."""
    if original is None:
        return None
    copy = prepared_path(original)
    return copy if copy.is_file() else None


def sidecar_path(copy: Path) -> Path:
    return copy.with_name(copy.name + ".prepared.json")
