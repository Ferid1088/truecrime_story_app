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
        return _jsonable(value.model_dump(mode="json"))
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in vars(value).items() if not k.startswith("_")}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, str):
        run_root = os.environ.get("SOUNDNESS_RUN_ROOT")
        if run_root:
            raw_root = str(Path(run_root))
            resolved_root = str(Path(run_root).resolve())
            value = value.replace(raw_root, "<PATH>")
            value = value.replace(resolved_root, "<PATH>")
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
                   kind: str, payload: dict[str, Any], *, identity: str | None = None) -> None:
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
        "request_identity": identity,
    }
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")) + "\n")


def record_adapter_call(provider: str, stage: str, language: str | None) -> None:
    """Count one invocation at the mock-provider adapter boundary.

    This is deliberately separate from the application's provider_calls
    ledger: provider fakes do not need to manufacture production telemetry.
    """
    path = os.environ.get("SOUNDNESS_CALL_COUNTER")
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "provider": provider, "stage": stage, "language": language,
        }, sort_keys=True, separators=(",", ":")) + "\n")


def provider_request(provider: str, stage: str, language_context: str | None,
                     kind: str, *, recording_identity: str | None = None, **payload: Any) -> str:
    """Record and fingerprint exactly the same complete fake request."""
    record_adapter_call(provider, stage, language_context)
    record_request(provider, stage, language_context, kind, payload, identity=recording_identity)
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


def generation_request_payload(role: str, system: str, user: str,
                               images: list[str] | None = None) -> dict[str, Any]:
    """Build the exact JSON body sent by the OpenAI-compatible adapter."""
    from app.core.ai_config import ai_config

    gen = ai_config.generation[role]
    content: Any = user
    if images:
        content = [{"type": "text", "text": user}] + [
            {"type": "image_url", "image_url": {"url": url}} for url in images
        ]
    body: dict[str, Any] = {
        "model": ai_config.model_for(role),
        "temperature": gen.temperature,
        "max_tokens": gen.max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
    }
    if gen.seed is not None:
        body["seed"] = gen.seed
    return body


def tts_request_payload(req: Any) -> dict[str, Any]:
    """The ElevenLabs URL parameters, path identity, and JSON body."""
    from app.core.ai_config import ai_config

    cfg = ai_config.voice.elevenlabs
    body: dict[str, Any] = {
        "text": req.text, "model_id": req.model_id,
        "voice_settings": dict(req.settings),
    }
    if req.previous_text and req.model_id not in cfg.no_context_models:
        body["previous_text"] = req.previous_text
    if req.next_text and req.model_id not in cfg.no_context_models:
        body["next_text"] = req.next_text
    if req.seed is not None:
        body["seed"] = int(req.seed)
    if req.language_code and req.model_id in cfg.language_code_models:
        body["language_code"] = req.language_code
    return {"path_voice_id": req.voice_id,
            "params": {"output_format": cfg.output_format}, "json": body}


def asr_request_payload(audio: bytes, language: str, *, model: str,
                        compute_type: str) -> dict[str, Any]:
    """The local Whisper adapter inputs and fixed transcription options."""
    return {"audio": audio, "language": language, "model": model,
            "compute_type": compute_type, "word_timestamps": True,
            "vad_filter": False}


def music_request_payload(prompt: str, seconds: float, loop: bool,
                          influence: float) -> dict[str, Any]:
    """The ElevenLabs sound-generation query/body after its builder runs."""
    body: dict[str, Any] = {"text": prompt, "duration_seconds": float(seconds),
                            "prompt_influence": float(influence)}
    if loop:
        body["loop"] = True
        body["model_id"] = "eleven_text_to_sound_v2"
    from app.core.ai_config import ai_config
    return {"params": {"output_format": ai_config.voice.elevenlabs.output_format},
            "json": body}
