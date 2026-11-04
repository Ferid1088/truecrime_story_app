"""The film table: how every person, place and foreign word of the film is read, the same way everywhere. The reader gives the PARTS of a name
(title, first name, last name) and code builds every variant of it, so the rules of a name are applied by code and not remembered by an agent:
title + name takes the ezafe, first name + last name does not."""

from __future__ import annotations

from dataclasses import dataclass

from app.documentary.reading import scheme
from app.documentary.reading.words import Word, key_of


@dataclass(frozen=True)
class Entry:
    """One way a name is written in the text and how it is read, word by word."""

    persian: str                   # as the text writes it
    keys: tuple[str, ...]          # the Persian words (compared by `key`)
    readings: tuple[str, ...]      # one reading per Persian word
    kind: str                      # person | place | foreign
    owner: str                     # the person (or place) it belongs to, for the pause between two different persons


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    entry: Entry


class FilmTableError(ValueError):
    pass


def entry(persian: str, latin: str, kind: str, owner: str) -> Entry:
    keys = tuple(key_of(w) for w in persian.split())
    readings = tuple(latin.split()) if len(keys) > 1 else (latin.strip(),)      # a single written word may be read as several (a name with a half-space)
    if not keys or len(keys) != len(readings):
        raise FilmTableError(f"{persian!r} has {len(keys)} words but its reading {latin!r} has {len(readings)}")
    return Entry(persian.strip(), keys, readings, kind, owner)


def person_entries(person: dict) -> list[Entry]:
    """Every variant of one person: the full name, each part alone, and each with each of the person's titles (the title takes the ezafe)."""
    owner = str(person.get("id") or person["first"]["fa"])
    first, last = person.get("first"), person.get("last")
    names: list[tuple[str, str]] = []
    if first and last:
        names.append((f"{first['fa']} {last['fa']}", f"{first['latin']} {last['latin']}"))
    names += [(p["fa"], p["latin"]) for p in (first, last) if p]
    entries = [entry(fa, latin, "person", owner) for fa, latin in names]
    for title in person.get("titles") or []:
        for fa, latin in names:
            entries.append(entry(f"{title['fa']} {fa}", f"{scheme.ezafe_form(title['latin'])} {latin}", "person", owner))
    return entries


def hyphenated(plain: str, reading: str) -> str:
    """A table reading with an ezafe written without its hyphen given its hyphen: -ye after a vowel the word ends in, -e that no letter of the word
    writes (reading/ezafe.unwritten_e)."""
    from app.documentary.reading.ezafe import final_sound, unwritten_e

    if scheme.split_ezafe(reading)[1] or len(reading) < 3:
        return reading
    if reading.endswith("ye") and reading[-3] in scheme.VOWELS and (plain.endswith(("ی", "ا", "و")) or plain.endswith("ه")):
        return f"{reading[:-2]}-ye"
    if reading.endswith("e") and final_sound(reading[:-1]) in scheme_consonants() and unwritten_e(plain, reading[:-1]):
        return f"{reading[:-1]}-e"
    return reading


def scheme_consonants() -> frozenset[str]:
    from app.documentary.reading.spelling import CONSONANTS

    return frozenset(CONSONANTS)


def with_words(e: Entry, words: dict[str, str]) -> Entry:
    """The entry with the owner's reading of each word it contains (قاضی پرت keeps the title and takes the approved پرت), each part keeping the ezafe
    the entry gave it."""
    if not any(k in words for k in e.keys):
        return e
    parts = tuple(scheme.ezafe_form(words[k]) if k in words and scheme.split_ezafe(r)[1] else words.get(k, r) for k, r in zip(e.keys, e.readings, strict=True))
    return Entry(e.persian, e.keys, parts, e.kind, e.owner)


class FilmTable:
    def __init__(self, entries: list[Entry]):
        self.entries = sorted(entries, key=lambda e: -len(e.keys))        # the longest variant is tried first
        self.by_first: dict[str, list[Entry]] = {}
        for e in self.entries:
            self.by_first.setdefault(e.keys[0], []).append(e)

    @classmethod
    def build(cls, reader: dict, approvals: list[dict] | None = None) -> FilmTable:
        """The table of the reader's answer ({"people": [...], "places": [{"fa", "latin"}], "foreign": [...]}). An approval of the owner
        ({"surface", "latin", "kind"}: a name, or a spoken form read this way everywhere) replaces the reader's reading of the same words; a reading
        resolution of the case (kind "resolved", reading/resolution.py) does too, below the owner's approval of the same word."""
        entries: list[Entry] = []
        for person in reader.get("people") or []:
            entries += person_entries(person)
        for kind in ("places", "foreign"):
            entries += [entry(p["fa"], p["latin"], kind[:-1] if kind == "places" else kind, p["fa"]) for p in reader.get(kind) or []]
        latest = {key_of(a["surface"]): a for a in approvals or [] if a.get("kind") in ("person", "place", "foreign", "spoken_form")}
        owners = list(latest.values())                   # one approval per word: the owner's newest (the approvals come oldest first)
        owned = {key_of(a["surface"]) for a in owners}
        resolved = [a for a in approvals or [] if a.get("kind") in ("resolved", "resolved_name") and key_of(a["surface"]) not in owned]   # below the owner
        approved = [entry(a["surface"], a["latin"], a.get("kind") or "foreign", a["surface"]) for a in owners] + \
            [entry(a["surface"], a["latin"], "person" if a["kind"] == "resolved_name" else "resolved", a["surface"]) for a in resolved]
        taken = {e.keys for e in approved}
        words = {e.keys[0]: e.readings[0] for e in approved if len(e.keys) == 1}
        return cls(approved + [with_words(e, words) for e in entries if e.keys not in taken])

    def describe(self, words: list[Word]) -> list[dict]:
        """The names of the table that stand in these words, as the agents are given them: every written form with its reading."""
        return [{"fa": e.persian, "latin": " ".join(e.readings), "kind": e.kind} for e in dict.fromkeys(m.entry for m in self.matches(words))]

    def matches(self, words: list[Word]) -> list[Match]:
        """Where the names of the table stand in the words, the longest first, without overlap."""
        found: list[Match] = []
        i = 0
        while i < len(words):
            hit = next((e for e in self.by_first.get(words[i].key, [])
                        if tuple(w.key for w in words[i:i + len(e.keys)]) == e.keys), None)
            if hit is None:
                i += 1
                continue
            found.append(Match(i, i + len(hit.keys) - 1, hit))
            i += len(hit.keys)
        return found

    def fixed_readings(self, words: list[Word]) -> dict[int, str]:
        """The table's reading of every word a name covers, its ezafe in the scheme's notation (the reader may write an ezafe without its hyphen inside a
        long name: dādgāhe omumiye shahrestāne miāmi)."""
        return {m.start + k: hyphenated(words[m.start + k].plain, r) for m in self.matches(words) for k, r in enumerate(m.entry.readings)}

    def pauses(self, words: list[Word]) -> set[int]:
        """The words after which a pause is said: two different persons side by side with no punctuation between them (a subject name and an
        object name) would otherwise run into one name."""
        found = self.matches(words)
        return {a.end for a, b in zip(found, found[1:], strict=False)
                if b.start == a.end + 1 and a.entry.kind == b.entry.kind == "person" and a.entry.owner != b.entry.owner and not words[a.end].after}
