"""Test-only transparent config proxies used by real soundness replays."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ConfigReadProxy:
    def __init__(self, value: Any, path: str, reads: set[str]) -> None:
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_reads", reads)

    def __getattr__(self, name: str) -> Any:
        path = f"{self._path}.{name}"
        self._reads.add(path)
        value = getattr(self._value, name)
        if hasattr(value, "model_fields"):
            return ConfigReadProxy(value, path, self._reads)
        return value

    def __getitem__(self, key: Any) -> Any:
        self._reads.add(f"{self._path}[{key!r}]")
        return self._value[key]

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._value, name, value)

    def __iter__(self):
        return iter(self._value)

    def __bool__(self) -> bool:
        return bool(self._value)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self._reads.add(f"{self._path}()")
        return self._value(*args, **kwargs)

    def model_dump(self, *args: Any, **kwargs: Any) -> Any:
        self._reads.add(f"{self._path}.*")
        return self._value.model_dump(*args, **kwargs)


def install(ai_config: Any, path: Path) -> tuple[set[str], callable]:
    reads: set[str] = set()
    originals: dict[str, Any] = {}
    for name in ai_config.model_fields:
        value = getattr(ai_config, name, None)
        if hasattr(value, "model_fields"):
            originals[name] = value
            object.__setattr__(ai_config, name, ConfigReadProxy(value, name, reads))

    def restore() -> None:
        for name, value in originals.items():
            object.__setattr__(ai_config, name, value)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(reads), indent=2) + "\n")

    return reads, restore
