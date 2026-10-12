"""Stable fingerprints for addressable documentary inputs.

The canonical form is deliberately independent of dictionary insertion order,
Unicode normalization form, wall-clock metadata, and machine-local paths.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from typing import Any

_DROP = object()
FINGERPRINT_VOLATILE_FIELDS = frozenset({
    "created_at", "updated_at", "timestamp", "generated_at", "completed_at",
    "started_at", "finished_at", "run_at", "fetched_at", "expires_at",
})
_ABSOLUTE_PATH = re.compile(r"^(?:/|[A-Za-z]:[\\/])")


def _normalise(value: Any, *, key: str | None = None) -> Any:
    if key and key.casefold() in FINGERPRINT_VOLATILE_FIELDS:
        return _DROP
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            normal_key = unicodedata.normalize("NFC", str(raw_key))
            normal_value = _normalise(raw_value, key=normal_key)
            if normal_value is not _DROP:
                out[normal_key] = normal_value
        return out
    if isinstance(value, (list, tuple)):
        return [item for item in (_normalise(v) for v in value) if item is not _DROP]
    if isinstance(value, str):
        if _ABSOLUTE_PATH.match(value):
            return _DROP
        return unicodedata.normalize("NFC", value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("fingerprint inputs cannot contain NaN or infinity")
        # Round-trip precision is fixed so equivalent binary float spellings
        # produce one JSON representation, including -0.0.
        return float(format(value, ".15g")) or 0.0
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if hasattr(value, "__fspath__"):
        path = str(value)
        return _DROP if _ABSOLUTE_PATH.match(path) else path
    raise TypeError(f"unsupported fingerprint input type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return deterministic UTF-8 JSON with volatile data omitted."""
    normal = _normalise(value)
    if normal is _DROP:
        normal = None
    return json.dumps(normal, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def input_fingerprint(artifact_type: str, inputs: Any, algo_version: str = "1") -> str:
    """Hash artifact type, algorithm version, and canonical inputs.

    Callers producing AI artifacts must include model name, prompt version,
    and relevant configuration in ``inputs``; changing any of them changes
    the returned digest.
    """
    payload = {
        "artifact_type": artifact_type,
        "algo_version": algo_version,
        "inputs": inputs,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
