"""Canonical request fingerprints for the four-language provider fakes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if isinstance(value, Path):
        path = value.resolve()
        run_root = os.environ.get("SOUNDNESS_RUN_ROOT")
        if run_root:
            root = Path(run_root).resolve()
            try:
                return "<PATH>/" + path.relative_to(root).as_posix()
            except ValueError:
                pass
        # Independent runs use different pytest temporary directories.  Keep
        # the useful path identity while excluding that harness prefix.
        if path.is_absolute():
            return "<PATH>/" + path.name
        return str(path)
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
    if isinstance(value, str):
        run_root = os.environ.get("SOUNDNESS_RUN_ROOT")
        if run_root:
            value = value.replace(str(Path(run_root).resolve()), "<PATH>")
        # Several fake requests are JSON strings.  Their object insertion
        # order can follow concurrent language completion order, but that is
        # not a semantic request-field change and must not perturb a fake.
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return value
        if isinstance(parsed, (dict, list)):
            return json.dumps(_jsonable(parsed), ensure_ascii=False,
                              sort_keys=True, separators=(",", ":"))
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}"}


def request_hash(kind: str, **payload: Any) -> str:
    """Hash the complete fake request, including configured test inputs."""
    encoded = json.dumps({"kind": kind, **_jsonable(payload)}, sort_keys=True,
                         ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def record_request(provider: str, stage: str, language: str | None,
                   kind: str, payload: dict[str, Any]) -> None:
    """Record one complete provider request for independent-run diagnosis."""
    log_path = os.environ.get("SOUNDNESS_PAYLOAD_LOG")
    if not log_path:
        run_root = os.environ.get("SOUNDNESS_RUN_ROOT")
        if not run_root:
            return
        log_path = str(Path(run_root) / "mock-payloads.jsonl")
    entry = {
        "provider": provider,
        "stage": stage,
        "language": language,
        "kind": kind,
        "payload": _jsonable(payload),
    }
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")) + "\n")


def provider_request(provider: str, stage: str, language_context: str | None,
                     kind: str, **payload: Any) -> str:
    """Record and fingerprint exactly the same complete fake request."""
    record_request(provider, stage, language_context, kind, payload)
    return request_hash(kind, **payload)


def request_language(user: Any, system: Any = None) -> str | None:
    """Extract the language context for recorder metadata only."""
    if not isinstance(user, str):
        return None
    candidate = user.split("INPUT:\n", 1)[-1]
    try:
        parsed = json.loads(candidate)
    except ValueError:
        return None
    if isinstance(parsed, dict):
        language = parsed.get("language") or parsed.get("spoken_language")
        if language:
            return language
    if isinstance(system, str):
        for phrase, language in (("English", "en"), ("German", "de"),
                                 ("Persian (Farsi)", "fa"), ("Arabic", "ar")):
            if phrase in system:
                return language
    return None


def config_hash_payload(config: Any) -> Any:
    """Return the complete typed configuration for inclusion in a fake request."""
    payload = _jsonable(config)
    # Incremental policy controls reuse, not provider generation.  Including
    # it in a fake provider request made mode=off look like a generation
    # input even though the pipeline output must be unchanged.
    if isinstance(payload, dict):
        policies = payload.get("production_policies")
        if isinstance(policies, dict):
            policies.pop("incremental", None)
    return payload
