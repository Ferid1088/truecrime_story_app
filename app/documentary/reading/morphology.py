"""The morphology of a written word: a word is its stem in the lexicon plus suffixes (plural, indefinite, possessive and object clitics, spoken
forms), up to a few of them. Each analysis that finds its stem in the lexical evidence gives the readings the word can have, every reading with
the stem entry it came from. Words without a stem in the evidence (names, verbs, new words) have no readings here: they are reported as having no
evidence, never guessed."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.ai_config import ai_config
from app.documentary.reading import exceptions as EX
from app.documentary.reading.evidence import Entry, Lexicon
from app.documentary.reading.transcription import join
from app.documentary.reading.words import key_of


@dataclass(frozen=True)
class Analysis:
    stem: str                                  # the written stem as found in the lexicon
    suffixes: tuple[tuple[str, str], ...] = ()  # (written suffix, its kind), in order
    spoken_as: str = ""                         # the colloquial rule that turned the written word into this stem

    def describe(self) -> str:
        parts = [self.stem, *(f"+{s} ({k})" for s, k in self.suffixes)]
        return " ".join(parts) + (f" [{self.spoken_as}]" if self.spoken_as else "")


@dataclass
class Option:
    """One reading a word can have, and its evidence."""

    latin: str
    analysis: Analysis
    entries: list[Entry] = field(default_factory=list)
    kinds: tuple[str, ...] = ()                 # what supports it: "romanization", "ipa", "tehrani" (a Tehrani-tagged sound), "rule", "verb", "exception"
    basis: str = "lexicon"                      # "lexicon": the stem is in the evidence; "rule": a grammar rule of a host word and its clitic;
                                                # "verb": a past stem of the lexicon with the grammar of the endings; "exception": confirmed by a person


class Morphology:
    def __init__(self, lexicon: Lexicon, exceptions: dict | None = None):
        self.lexicon = lexicon
        self.exceptions = EX.load() if exceptions is None else exceptions
        self.cfg = ai_config.reading.morphology
        self.suffixes = sorted(self.cfg.suffixes, key=lambda s: -len(s["surface"]))

    def analyses(self, key: str) -> list[Analysis]:
        found: list[Analysis] = [Analysis(key)]
        spoken = [(key, "")]
        for rule in self.cfg.colloquial_stem_rules:
            if rule["colloquial"] in key:
                spoken.append((key.replace(rule["colloquial"], rule["formal"]), rule["kind"]))
        for base, how in spoken:
            if how:
                found.append(Analysis(base, (), how))
            self.strip(base, (), how, found)
        return list(dict.fromkeys(found))

    def strip(self, key: str, done: tuple, how: str, found: list[Analysis]) -> None:
        if len(done) >= self.cfg.max_suffixes:
            return
        for suffix in self.suffixes:
            surface = key_of(suffix["surface"])
            stem = key[: -len(surface)]
            if key.endswith(surface) and len(stem) >= self.cfg.min_stem_letters:
                chain = ((suffix["surface"], suffix["kind"]), *done)
                found.append(Analysis(stem, chain, how))
                self.strip(stem, chain, how, found)

    def compose(self, stem: str, suffixes: tuple[tuple[str, str], ...]) -> list[str]:
        """The readings of the stem with its suffixes (the first suffix written is the innermost)."""
        rules = {(s["surface"], s["kind"]): s for s in self.suffixes}
        readings = [stem]
        for surface, kind in suffixes:
            rule, nxt = rules[(surface, kind)], []
            for reading in readings:
                final_vowel = reading[-1:] in self.cfg.vowel_letters
                forms = rule["after_vowel"] if final_vowel else rule["after_consonant"]
                if reading.endswith("e") and self.cfg.e_final_vowel_like:
                    forms = [*rule["after_vowel"], *rule["after_consonant"]]
                nxt.extend(reading + form if form.startswith("-") else join(f"{reading}-{form}") for form in forms)
            readings = nxt
        return readings

    def spoken(self, analysis: Analysis, latin: str) -> list[str]:
        """The readings of a stem that was found in its formal spelling: the spoken form of the stem is the written one (خونه for خانه)."""
        if not analysis.spoken_as:
            return [latin]
        rule = next(r for r in self.cfg.colloquial_stem_rules if r["kind"] == analysis.spoken_as)
        head, sep, tail = latin.rpartition(rule["reading_formal"])
        return [head + rule["reading_spoken"] + tail] if sep else []

    def hosted(self, key: str) -> list[Option]:
        """A host word with a clitic (بهش, ازم, باهاش): the grammar rule gives its readings; there is no lexicon entry for these."""
        out = []
        clitics = {key_of(s["surface"]): s for s in self.suffixes}
        for host in self.cfg.hosts:
            front = key_of(host["host"] + host["written_infix"])
            tail = key[len(front):] if key.startswith(front) else None
            if tail in {key_of(c) for c in host["clitics"]}:
                rule = clitics[next(c for c in host["clitics"] if key_of(c) == tail)]
                for form in rule[host["clitic_forms"]]:
                    out.append(Option(host["host_reading"] + host["reading_infix"] + form, Analysis(front, ((rule["surface"], rule["kind"]),)),
                                      kinds=("rule",), basis="rule"))
        return out

    def verbal(self, key: str) -> list[Option]:
        """A verb form built from a past stem or past participle of the lexicon: prefix + stem + personal ending (the grammar)."""
        cfg = self.cfg.verb
        if not cfg:
            return []
        out = []
        for prefix in cfg["prefixes"]:
            front = key_of(prefix["surface"])
            if not key.startswith(front):
                continue
            rest = key[len(front):]
            for ending in cfg["past_endings"]:
                tail = key_of(ending["surface"])
                if tail and not rest.endswith(tail):
                    continue
                stem = rest[: len(rest) - len(tail)] if tail else rest
                for form in self.lexicon.verbs.get(stem, []):
                    if form["kind"] == "past participle" and (ending["surface"] or prefix["surface"]):
                        continue
                    out.append(Option(prefix["reading"] + form["latin"] + ending["reading"],
                                      Analysis(stem, ((prefix["surface"] or "-", "prefix"), (ending["surface"] or "-", f"{form['kind']} of {form['infinitive']}"))),
                                      kinds=("verb", "romanization"), basis="verb"))
        return out

    def options(self, surface: str) -> list[Option]:
        confirmed = self.exceptions.get(key_of(surface))
        if confirmed:
            return [Option(r, Analysis(key_of(surface)), kinds=("exception",), basis="exception") for r in confirmed.right]
        out: list[Option] = [*self.hosted(key_of(surface)), *self.verbal(key_of(surface))]
        for analysis in self.analyses(key_of(surface)):
            evidence = self.lexicon.lookup(analysis.stem)
            for entry in evidence.entries:
                for cand in entry.candidates:
                    for stem in self.spoken(analysis, cand.latin):
                        support = (cand.kind, *(["tehrani"] if any(tag in ai_config.reading.evidence.tehrani_tags for tag in cand.tags) else []))
                        for latin in self.compose(stem, analysis.suffixes):
                            out.append(Option(latin, analysis, [entry], support))
        merged: dict[tuple[str, Analysis], Option] = {}
        for o in out:
            known = merged.get((o.latin, o.analysis))
            if known is None:
                merged[(o.latin, o.analysis)] = o
            else:
                known.entries = list({id(e): e for e in [*known.entries, *o.entries]}.values())
                known.kinds = tuple(dict.fromkeys([*known.kinds, *o.kinds]))
        return list(merged.values())

    def phrases(self, surfaces: list[str], start: int, longest: int = 3) -> list[tuple[int, list[Option]]]:
        """Readings of the written words that are one entry of the lexicon ("با هم"): (word count, options)."""
        found = []
        for size in range(2, longest + 1):
            chunk = surfaces[start:start + size]
            if len(chunk) < size:
                break
            evidence = self.lexicon.index.get(" ".join(key_of(c) for c in chunk))
            if evidence:
                options = [Option(c["latin"], Analysis(" ".join(key_of(c) for c in chunk))) for e in evidence for c in e["candidates"]]
                found.append((size, options))
        return found


STRONG = {"tehrani", "rule", "exception"}


def strong(kinds: set[str]) -> bool:
    """Whether the support of a reading may replace a writer's reading: a Tehrani-tagged sound, a grammar rule, a confirmed exception, or two
    independent sources of the lexicon (a romanisation is the formal spelling of a word and says little of how it is spoken)."""
    return bool(kinds & STRONG) or {"romanization", "ipa"} <= kinds


def distinct(options: list[Option]) -> list[str]:
    return list(dict.fromkeys(o.latin for o in options))
