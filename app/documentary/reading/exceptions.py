"""The verified exceptions: readings a person has confirmed (config/reading_exceptions.json). They are few, never written by a model, and each is
also a permanent regression test. A word with an exception has exactly the confirmed readings; the lexical evidence is not asked."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.core.ai_config import ai_config
from app.documentary.reading.words import key_of

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Exception_:
    surface: str
    right: tuple[str, ...]
    wrong: tuple[str, ...]
    confirmed: str


def load() -> dict[str, Exception_]:
    path = Path(ai_config.reading.exceptions_file)
    data = json.loads((path if path.is_absolute() else ROOT / path).read_text(encoding="utf-8"))
    return {key_of(e["surface"]): Exception_(e["surface"], tuple(e["right"]), tuple(e.get("wrong", ())), e["confirmed"]) for e in data["exceptions"]}
