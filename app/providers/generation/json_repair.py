"""Free, local repairs of a model's almost-JSON answer, tried before a paid retry.

Fixes only what cannot change the meaning: markdown fences and prose around the object, trailing commas and Python
literals (True/False/None), both only outside strings. An answer that is cut off, or whose structure is wrong, is
not repaired: closing it would silently keep a partial answer as if it were whole."""

from __future__ import annotations

import json
import re

PYTHON_LITERALS = {"True": "true", "False": "false", "None": "null"}


def _fixed_outside_strings(text: str) -> str:
    out, i, in_string = [], 0, False
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif ch == "," and re.match(r",\s*[}\]]", text[i:]):
            pass                                              # a trailing comma
        else:
            word = re.match(r"True|False|None", text[i:]) if ch in "TFN" else None
            if word and not (out and (out[-1].isalnum() or out[-1] == "_")):
                out.append(PYTHON_LITERALS[word.group()])
                i += len(word.group())
                continue
            out.append(ch)
        i += 1
    return "".join(out)


def repair(text: str) -> dict | None:
    """The object the text almost is, or None when it cannot be repaired without guessing."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start = cleaned.find("{")
    if start == -1:
        return None
    try:
        value, _end = json.JSONDecoder().raw_decode(_fixed_outside_strings(cleaned[start:]))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
