"""The check and the trace of the pronunciation dictionary of a block.

A dictionary rule acts only when its string is found in the exact text a block is sent with. The dictionary of a film is made from the prepared
text; the text sent later is changed again (vowel marks, the reading loop's IPA, scrubbed tags), so a rule can silently stop matching. Before every
take each rule is checked against the exact final text, the result is recorded, and everything a take was sent with is kept:

- matched      the rule's string is in the text as written (whole word, outside audio tags)
- reconciled   the rule's word is in the text in another spelling of the same letters (Arabic yeh/kaf, a half-space for a space, another Unicode
               form); the rule is kept for the form the text has. The text is never changed to make a rule match.
- unresolved   the word was in the text the dictionary was made from and cannot be found now, or it is in the text with other vowel marks than
               the rule's (a mark is a sound: the rule is not moved onto it): the rule is left out and the reason kept
- conflict     two rules for one word say different things: both are left out
- shadowed     every place of the rule is inside the place of a rule that wins over it (one written exactly as the text has it, or longer)
- invalid      not a rule ElevenLabs takes
- absent       the word is not in this block (nothing to do)

In mode "trace" the request is unchanged and the result is only recorded. In mode "enforce" the block is sent a dictionary made for its final text."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from app.core.ai_config import ai_config
from app.documentary import storage
from app.providers.voice.base import VoiceProviderError

log = logging.getLogger(__name__)
TAG = re.compile(r"\[[^\[\]]*\]")
TYPES = {"alias", "phoneme"}
STATUSES = ("matched", "reconciled", "unresolved", "conflict", "shadowed", "invalid", "duplicate", "absent")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fingerprint(rules: list[dict]) -> str:
    return sha(json.dumps(sorted(rules, key=lambda r: (r["string_to_replace"], r["type"])), sort_keys=True, ensure_ascii=False))


# --- spelling variants -----------------------------------------------------------------------------------------------------------------

def clusters(text: str) -> list[tuple[int, int, str]]:
    """The letters of a text with the marks that belong to them: (start, end, letter+marks)."""
    out: list[list] = []
    for i, ch in enumerate(text):
        if out and unicodedata.category(ch).startswith("M"):
            out[-1][1], out[-1][2] = i + 1, out[-1][2] + ch
        else:
            out.append([i, i + 1, ch])
    return [(a, b, s) for a, b, s in out]


def fold(text: str, drop_marks: bool = True) -> tuple[str, list[tuple[int, int]]]:
    """The text with one spelling of each letter (one Unicode form, Persian yeh/kaf and digits, a space for a half-space), without its vowel marks
    when `drop_marks`, and where each folded character is in the original. Only variants of spelling: it never changes a letter into another sound."""
    cfg = ai_config.pronunciation_trace
    marks, letters = (set(cfg.harakat) if drop_marks else set()), {**cfg.yeh_kaf, **cfg.digits}
    folded: list[str] = []
    where: list[tuple[int, int]] = []
    for start, end, cluster in clusters(text):
        unit = "".join(ch for ch in unicodedata.normalize("NFC", cluster) if ch not in marks)
        unit = "".join(letters.get(ch, ch) for ch in unit).replace(cfg.joiner, " ")
        for ch in unit:
            folded.append(ch)
            where.append((start, end))
    return "".join(folded), where


def inside_word(ch: str) -> bool:
    return ch.isalnum() or unicodedata.category(ch).startswith("M")


def boundary(text: str, start: int, end: int) -> str | None:
    """"ok" when the place is a whole word, "zwnj" when a half-space touches it (ElevenLabs does not say if that is a boundary), None when letters touch it."""
    joiner, flag = ai_config.pronunciation_trace.joiner, "ok"
    for ch in (text[start - 1] if start else "", text[end] if end < len(text) else ""):
        if ch == joiner:
            flag = "zwnj"
        elif ch and inside_word(ch):
            return None
    return flag


def marks_of(text: str) -> str:
    return "".join(ch for ch in text if ch in ai_config.pronunciation_trace.harakat)


def tag_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in TAG.finditer(text)]


def occurrences(text: str, needle: str, *, variant: bool, whole: bool = True, ignore_marks: bool = False) -> list[dict]:
    """Where the needle is in the text: exact, or as another spelling of the same letters (Unicode form, Persian yeh/kaf and digits, a half-space
    for a space). Vowel marks are sounds: they are ignored only with `ignore_marks`, to tell that a word is there with other marks than the rule's.
    Audio tags are not spoken text and never match."""
    if not needle:
        return []
    tags, found = tag_spans(text), []
    if variant:
        hay, where = fold(text, drop_marks=ignore_marks)
        want, _ = fold(needle, drop_marks=ignore_marks)
        spans = [(where[i][0], where[i + len(want) - 1][1]) for i in _finds(hay, want)] if want else []
    else:
        spans = [(i, i + len(needle)) for i in _finds(text, needle)]
    for start, end in spans:
        flag = boundary(text, start, end) if whole else "ok"
        if flag is None or any(a < end and start < b for a, b in tags):
            continue
        found.append({"start": start, "end": end, "text": text[start:end], "boundary": flag})
    return found


def _finds(hay: str, needle: str) -> list[int]:
    out, i = [], hay.find(needle)
    while i != -1:
        out.append(i)
        i = hay.find(needle, i + len(needle))
    return out


# --- the check of one block ------------------------------------------------------------------------------------------------------------

@dataclass
class RuleCheck:
    index: int
    string: str
    type: str
    replacement: str
    status: str
    reason: str = ""
    occurrences: list[dict] = field(default_factory=list)
    forms: list[str] = field(default_factory=list)            # for a reconciled rule: the spellings the text has
    other: str = ""                                           # the rule it conflicts with or is shadowed by

    @property
    def boundary_uncertain(self) -> bool:
        return any(o["boundary"] == "zwnj" for o in self.occurrences)


@dataclass
class Validation:
    block_id: str
    text_sha: str
    prepared_sha: str | None
    changed_after_dictionary: bool
    checks: list[RuleCheck]
    effective: list[dict]                                     # the rules that act on this text (matched, and reconciled to the text's spelling)

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in STATUSES}
        for c in self.checks:
            out[c.status] += 1
        return out

    def applicable(self) -> list[RuleCheck]:
        return [c for c in self.checks if c.status != "absent"]


def replacement_of(rule: dict) -> str:
    return rule.get("alias") if rule.get("type") == "alias" else rule.get("phoneme") or ""


def invalid_reason(rule: dict) -> str:
    if rule.get("type") not in TYPES:
        return f"type {rule.get('type')!r} is not alias or phoneme"
    if not isinstance(rule.get("string_to_replace"), str) or not rule["string_to_replace"].strip():
        return "string_to_replace is empty"
    if not (replacement_of(rule) or "").strip():
        return "the replacement is empty"
    if rule["type"] == "phoneme" and rule.get("alphabet") != "ipa":
        return "a phoneme rule needs alphabet ipa"
    return ""


def validate(block_id: str, text: str, rules: list[dict], prepared_text: str | None = None) -> Validation:
    """Checks every rule against the exact text. `prepared_text` is the text the dictionary was made from: a rule whose word was in it and is not
    in `text` any more has been lost by a change after the dictionary was made."""
    checks = [RuleCheck(i, r.get("string_to_replace") or "", r.get("type") or "", replacement_of(r), "absent") for i, r in enumerate(rules)]
    seen: dict[str, int] = {}
    for check, rule in zip(checks, rules, strict=True):
        problem = invalid_reason(rule)
        if problem:
            check.status, check.reason = "invalid", problem
            continue
        key = unicodedata.normalize("NFC", check.string)             # the same string, not the same letters: a word with and without an ezafe mark are two rules
        if key not in seen:
            seen[key] = check.index
            continue
        first = checks[seen[key]]
        if (first.type, first.replacement) == (check.type, check.replacement):
            check.status, check.reason, check.other = "duplicate", "the same rule is already in the dictionary", first.string
        else:
            for c in (first, check):
                c.status, c.reason, c.other = "conflict", "two rules for the same word say different things", (check if c is first else first).string
    candidates: list[tuple[RuleCheck, dict]] = []
    pending: list[RuleCheck] = []
    for check, rule in zip(checks, rules, strict=True):
        if check.status != "absent":
            continue
        whole = rule.get("word_boundaries", True)
        exact = occurrences(text, check.string, variant=False, whole=whole)
        if exact:
            check.status, check.occurrences = "matched", exact
            candidates.append((check, rule))
            continue
        variant = occurrences(text, check.string, variant=True, whole=whole)
        if variant:
            check.status, check.occurrences = "reconciled", variant
            check.forms = list(dict.fromkeys(o["text"] for o in variant))
            check.reason = "the text spells the word differently: " + ", ".join(check.forms)
            candidates.append((check, rule))
        else:
            other = [o for o in occurrences(text, check.string, variant=True, whole=whole, ignore_marks=True) if marks_of(o["text"]) not in ("", marks_of(check.string))]
            if other:
                check.occurrences = other
                pending.append(check)               # decided below: another rule may already stand on these places
            elif prepared_text is not None and occurrences(prepared_text, check.string, variant=True, whole=whole, ignore_marks=True):
                check.status = "unresolved"
                check.reason = "the word was in the text the dictionary was made from and is not in the text sent"
    accepted: list[tuple[int, int]] = []
    effective: list[dict] = []
    for check, rule in sorted(candidates, key=lambda c: (c[0].status != "matched", -len(c[0].string), c[0].index)):
        free = [o for o in check.occurrences if not any(a < o["end"] and o["start"] < b for a, b in accepted)]
        if not free:
            owner = next(c.string for c, _ in candidates if c is not check and any(
                a["start"] < o["end"] and o["start"] < a["end"] for a in c.occurrences for o in check.occurrences))
            check.status, check.reason, check.other = "shadowed", "every place of this rule is inside a longer rule", owner
            continue
        accepted += [(o["start"], o["end"]) for o in free]
        for form in check.forms or [check.string]:
            effective.append({**rule, "string_to_replace": form})
    for check in pending:
        owner = next((c.string for c, _ in candidates if any(a["start"] < o["end"] and o["start"] < a["end"] for a in c.occurrences for o in check.occurrences)), "")
        covered = all(any(a < o["end"] and o["start"] < b for a, b in accepted) for o in check.occurrences)
        if covered:
            check.status, check.reason, check.other = "shadowed", "the word is there with other vowel marks and another rule already stands on it", owner
        else:
            check.status = "unresolved"
            check.reason = ("the word is in the text with other vowel marks than the rule's: a mark is a sound, so the rule is not moved onto it: "
                            + ", ".join(dict.fromkeys(o["text"] for o in check.occurrences)))
    prepared_sha = sha(prepared_text) if prepared_text is not None else None
    changed = prepared_text is not None and prepared_text != text
    return Validation(block_id, sha(text), prepared_sha, changed, checks, sorted(effective, key=lambda r: (r["string_to_replace"], r["type"])))


def film_summary(validations: list[Validation], rules: list[dict]) -> dict:
    """The rules of a film over all its blocks: a rule is matched when its word is in some block as written, reconciled when only in another
    spelling, unresolved when it was meant for a block and is lost or is in no block at all."""
    per_rule: list[str] = []
    for i in range(len(rules)):
        seen = {v.checks[i].status for v in validations}
        if "invalid" in seen:
            per_rule.append("invalid")
        elif "conflict" in seen:
            per_rule.append("conflict")
        elif "matched" in seen:
            per_rule.append("matched")
        elif "reconciled" in seen:
            per_rule.append("reconciled")
        elif "shadowed" in seen:
            per_rule.append("shadowed")
        elif "duplicate" in seen:
            per_rule.append("duplicate")
        else:
            per_rule.append("unresolved")
    counts = {s: per_rule.count(s) for s in STATUSES if s != "absent"}
    return {"blocks": len(validations), "rules": len(rules), "by_status": counts, "per_rule": per_rule,
            "blocks_changed_after_dictionary": sum(1 for v in validations if v.changed_after_dictionary)}


# --- the dictionary of one block --------------------------------------------------------------------------------------------------------

@dataclass
class Resolution:
    locators: list[dict]
    validation: Validation | None
    mode: str
    rules_sent: list[dict]
    dictionary: dict | None = None
    notes: list[str] = field(default_factory=list)


class BlockDictionaries:
    """The dictionary a block is sent with. Rules are checked against the block's final text; in enforce mode a dictionary of just the rules that
    act on it is made (a dictionary is never changed after it is made: each set of rules has its own, found by the fingerprint of the rules, so
    blocks that run at the same time cannot touch each other's rules)."""

    def __init__(self, provider, rules: list[dict], film_locators: list[dict], *, language: str, case_id: int, version_id: int, folder: Path | None,
                 mode: str | None = None):
        self.provider, self.rules, self.film_locators = provider, rules, film_locators
        self.language, self.case_id, self.version_id = language, case_id, version_id
        self.mode = mode or ai_config.pronunciation_trace.mode
        self.path = folder / ai_config.pronunciation_trace.block_dictionaries_file if folder else None
        self.index: dict[str, dict] = self.load()
        self.locks: dict[str, asyncio.Lock] = {}
        self.index_lock = asyncio.Lock()

    def load(self) -> dict[str, dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path else {}
        except (OSError, ValueError):
            return {}

    async def dictionary_for(self, rules: list[dict]) -> dict:
        cfg, key = ai_config.pronunciation_trace, fingerprint(rules)
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self.index:
                return self.index[key]
            name = cfg.block_dictionary_name.format(language=self.language, case_id=self.case_id, version_id=self.version_id, fingerprint=key)
            for attempt in range(cfg.create_retries + 1):
                try:
                    made = await self.provider.create_pronunciation_dictionary(name, rules)
                    break
                except VoiceProviderError as e:
                    if e.kind not in cfg.retryable_errors or attempt == cfg.create_retries:
                        raise
                    await asyncio.sleep(cfg.retry_pause_s)
            entry = {"id": made["id"], "version_id": made["version_id"], "name": name, "fingerprint": key, "rules": rules}
            async with self.index_lock:
                self.index[key] = entry
                if self.path:
                    storage.write_atomic(self.path, json.dumps(self.index, ensure_ascii=False))
            return entry

    async def resolve(self, block_id: str, text: str, prepared_text: str | None) -> Resolution:
        if not self.film_locators:
            return Resolution([], None, self.mode, [])
        if not self.rules:
            return Resolution(list(self.film_locators), None, self.mode, [], notes=["the plan carries no rules: the dictionary cannot be checked"])
        check = validate(block_id, text, self.rules, prepared_text)
        if self.mode != "enforce":
            return Resolution(list(self.film_locators), check, "trace", list(self.rules))
        if not check.effective:
            return Resolution([], check, "enforce", [], notes=["no rule acts on this text: no dictionary"])
        try:
            entry = await self.dictionary_for(check.effective)
        except VoiceProviderError as e:
            log.warning("block dictionary for %s failed (%s): the film dictionary is used", block_id, e)
            return Resolution(list(self.film_locators), check, "enforce", list(self.rules), notes=[f"block dictionary failed: {e}"])
        return Resolution([{"pronunciation_dictionary_id": entry["id"], "version_id": entry["version_id"]}], check, "enforce", check.effective, entry)


class Trace:
    """Everything each take of a render was sent with, in one file next to the narration."""

    def __init__(self, dictionaries: BlockDictionaries | None):
        self.dictionaries, self.entries = dictionaries, []

    def record(self, *, block_id: str, attempt: int, text: str, request, resolution: Resolution, take: dict) -> None:
        check = resolution.validation
        self.entries.append({
            "block_id": block_id, "attempt": attempt, "mode": resolution.mode,
            "text": text, "text_sha": sha(text), "prepared_sha": check.prepared_sha if check else None,
            "changed_after_dictionary": check.changed_after_dictionary if check else None,
            "voice_id": request.voice_id, "model_id": request.model_id, "settings": request.settings, "seed": request.seed,
            "language_code": request.language_code, "previous_text": request.previous_text, "next_text": request.next_text,
            "dictionary_locators": resolution.locators, "rules_sent": len(resolution.rules_sent),
            "dictionary_fingerprint": fingerprint(resolution.rules_sent) if resolution.rules_sent else None,
            "request_id": take.get("request_id"), "cache_key": take.get("cache_key"), "cache_hit": take.get("cache_hit"),
            "notes": resolution.notes,
            "rules": [{"string": c.string, "type": c.type, "replacement": c.replacement, "status": c.status, "reason": c.reason,
                       "occurrences": c.occurrences, "forms": c.forms, "other": c.other, "boundary_uncertain": c.boundary_uncertain}
                      for c in (check.applicable() if check else [])],
        })

    def summary(self) -> dict:
        statuses: dict[str, int] = {}
        for e in self.entries:
            for r in e["rules"]:
                statuses[r["status"]] = statuses.get(r["status"], 0) + 1
        return {"takes": len(self.entries), "rule_checks_by_status": statuses,
                "takes_without_request_id": sum(1 for e in self.entries if not e["request_id"]),
                "takes_changed_after_dictionary": sum(1 for e in self.entries if e["changed_after_dictionary"])}

    def save(self, folder: Path) -> dict:
        film = self.dictionaries
        data = {"mode": film.mode if film else None, "film_locators": film.film_locators if film else [],
                "film_rules": film.rules if film else [], "block_dictionaries": film.index if film else {},
                "summary": self.summary(), "entries": self.entries}
        path = folder / ai_config.pronunciation_trace.trace_file
        storage.write_atomic(path, json.dumps(data, ensure_ascii=False, indent=1))
        return {"file": path.name, **data["summary"]}
