"""Canonical request fingerprints for the four-language provider fakes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if isinstance(value, Path):
        return str(value)
    if callable(value):
        return {"callable": f"{value.__module__}.{value.__qualname__}"}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in vars(value).items() if not k.startswith("_")}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}"}


def request_hash(kind: str, **payload: Any) -> str:
    """Hash the complete fake request, including configured test inputs."""
    encoded = json.dumps({"kind": kind, **_jsonable(payload)}, sort_keys=True,
                         ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def config_hash_payload(config: Any) -> Any:
    """Return the complete typed configuration for inclusion in a fake request."""
    return _jsonable(config)
