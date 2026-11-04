"""The Persian ezafe analyzer: for every word of a block, whether an ezafe joins it to the next word, -e or -ye, the grammatical relation, how sure
that is and on what evidence. It runs after the reading agents (editor, checker, arbiter) and before the pronunciation risk engine, on what they left:

  the reading     the arbiter's final reading of each word: an ezafe is "-e" / "-ye" on the word that receives it (linguistic inference);
  the opinions    each agent's own ezafe judgement of the word (editor, checker, arbiter: presence, relation, confidence), when the agents gave one;
  the writing     the text itself: a kasra, a hamza on ه, a half-space yeh after ه or a carrier yeh after a vowel is an ezafe the writer wrote;
  the rules       what holds whatever the context (config ezafe): a function word takes none, none before the object marker or a verb, none at the end of
                  a phrase (punctuation, a pause, the end of the block);
  the ambiguities a month before a year in digits and a pair that can be a compound or a phrase, unless the text writes the ezafe or an agent is sure.

-e or -ye is decided by the SPOKEN final sound of the word in its reading, never by the written last letter: a ه said as a vowel takes -ye, a ه said h
takes -e, a final ا/و said ā/u/o takes -ye, a diphthong or a consonantal و/ی takes -e. What the evidence cannot settle is UNCERTAIN, never a guess.

The analysis is a linguistic expectation. It says nothing about what the voice said; whether the voice needs help with it is decided by the risk engine
and the planner (`requires_tts_intervention` stays None here)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.ai_config import ai_config
from app.documentary.reading import scheme, spelling
from app.documentary.reading.words import NUMBER_WORD, VOWEL_MARKS, Word, key_of

TYPES = ("NONE", "E", "YE", "UNCERTAIN")
RELATIONS = ("noun_adjective", "noun_possessor", "noun_noun", "title_name", "apposition", "preposition_like", "pronoun_possessor", "date", "quantity",
             "compound", "none", "unknown")
PHRASE_END = set(".,;:!?؟،؛»…)")
KASRA, HAMZA_ABOVE, ZWNJ = "ِ", "ٔ", "‌"


@dataclass(frozen=True)
class Opinion:
    """One reading agent's ezafe judgement of a word: present True / False / None (it could not decide)."""

    source: str                 # editor | checker | arbiter
    present: bool | None
    relation: str = "unknown"
    confidence: float = 0.0

    @classmethod
    def parse(cls, source: str, raw: object) -> Opinion | None:
        """An agent's answer {"present": true|false|null, "relation": "...", "confidence": 0..1}; anything else is no opinion."""
        if not isinstance(raw, dict) or "present" not in raw:
            return None
        present = raw["present"] if isinstance(raw["present"], bool) else None
        relation = str(raw.get("relation") or "unknown")
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        return cls(source, present, relation if relation in RELATIONS else "unknown", confidence)

    def describe(self) -> dict:
        return {"source": self.source, "present": self.present, "relation": self.relation, "confidence": self.confidence}


@dataclass(frozen=True)
class Boundary:
    """The ezafe between word `n` and the word after it (the structured output of the analyzer)."""

    n: int
    word: str
    next_word: str
    ezafe_required: bool | None             # None: uncertain
    ezafe_type: str                         # NONE | E | YE | UNCERTAIN
    final_phoneme: str                      # the spoken last sound of the word (from its reading)
    expected_reading: str                   # the two words as they are expected to be said
    grammatical_relation: str
    confidence: float                       # an estimate of the linguistic decision, never a proof of the audio
    evidence_type: str                      # rule | orthography | linguistic_inference | agents_agree | conflict | uncertain
    written: bool = False                   # the text itself writes the ezafe
    conflicts: tuple[str, ...] = ()          # sources that disagree with the decision
    phonology_error: str = ""               # the reading writes an ezafe form that its own final sound does not take
    opinions: tuple[Opinion, ...] = field(default_factory=tuple)
    requires_tts_intervention: bool | None = None       # decided by the risk engine and the planner, never here
    phonological_class: str = ""            # EZAFE_FINAL_I, EZAFE_SILENT_HEH, EZAFE_CONSONANT_H, ... ("" when no ezafe is said)
    realizations: tuple[str, ...] = ()      # the connected forms a listener may accept for it (the first is the reference)
    tts_form: str | None = None             # what the voice is given for the word when the planner changed it (the reference stays `expected_reading`)

    def describe(self) -> dict:
        return {"n": self.n, "word": self.word, "next_word": self.next_word, "ezafe_required": self.ezafe_required, "ezafe_type": self.ezafe_type,
                "final_phoneme": self.final_phoneme, "expected_reading": self.expected_reading, "grammatical_relation": self.grammatical_relation,
                "confidence": self.confidence, "evidence_type": self.evidence_type, "written": self.written, "conflicts": list(self.conflicts),
                "phonology_error": self.phonology_error, "opinions": [o.describe() for o in self.opinions],
                "requires_tts_intervention": self.requires_tts_intervention, "phonological_class": self.phonological_class,
                "realizations": list(self.realizations), "tts_form": self.tts_form}


def final_sound(stem: str) -> str:
    """The last sound of a reading's stem in the reading scheme ("" when the reading is not one of the scheme)."""
    parts = spelling.symbols(scheme.canonical(stem).split("-")[-1])
    return parts[-1] if parts else ""


def type_for(sound: str) -> str:
    """-ye after a vowel said at the end of the word, -e after a consonant or a diphthong (its glide is a consonant: jelow-e, mey-e)."""
    return "YE" if sound in scheme.VOWELS else "E"


def phonological_class(sound: str, plain: str) -> str:
    """The class of an ezafe by the word's spoken final sound: a final long i (بینایی، صندلی) is EZAFE_FINAL_I, written with a kasra on its own yeh."""
    if sound == "i":
        return "EZAFE_FINAL_I"
    if sound == "e" and plain.endswith("ه"):
        return "EZAFE_SILENT_HEH"
    if sound == "h":
        return "EZAFE_CONSONANT_H"
    if sound in ("ā", "u", "o", "e"):
        return f"EZAFE_FINAL_{sound.upper().replace('Ā', 'A')}"
    if sound in ("ey", "ow"):
        return "EZAFE_DIPHTHONG"
    return "EZAFE_CONSONANT"


def realizations_of(stem: str, next_stem: str, cls: str) -> tuple[str, ...]:
    """The connected forms of an ezafe a listener may accept: after a final long i the glide of -ye is often weak in connected speech (bināyi-ye ~
    bināyi-e); the reference is the first. Elsewhere the one reference."""
    reference = " ".join(x for x in (scheme.ezafe_form(stem), next_stem) if x)
    if cls == "EZAFE_FINAL_I":
        return reference, " ".join(x for x in (f"{stem}-e", next_stem) if x)
    return (reference,)


def written_ezafe(word: Word, sound: str) -> bool:
    """Whether the text writes an ezafe on this word: a kasra at its end, a hamza on its ه (or ۀ), a half-space yeh after ه, a carrier yeh after a
    final ā / u / o when the reading's stem does not have that yeh, or a second yeh after a final long i (non-standard: یی‌ی, ییی; the canonical form is
    the kasra)."""
    s = word.surface
    if s.endswith(KASRA) or s.endswith("ه" + HAMZA_ABOVE) or s.endswith("ۀ") or s.endswith("ه" + ZWNJ + "ی"):
        return True
    if sound == "i" and (word.plain.endswith("ی" + ZWNJ + "ی") or word.plain.endswith("ییی")):
        return True
    return sound in ("ā", "u", "o") and word.plain.endswith(("ای", "وی"))


def base_key(word: Word) -> str:
    """The word's key without a written ezafe (فوریه‌ی is فوریه), for the word lists."""
    return key_of(word.surface[:-2] if word.surface.endswith("ه" + ZWNJ + "ی") else word.surface)


def plain_stem(reading: str) -> tuple[str, str, bool]:
    body, pause = scheme.split_pause(reading)
    stem, ezafe = scheme.split_ezafe(body)
    return stem, ezafe, pause


class EzafeAnalyzer:
    def __init__(self):
        cfg = ai_config.ezafe
        self.cfg = cfg
        keys = lambda xs: frozenset(key_of(x) for x in xs)          # noqa: E731
        self.never_takes, self.never_before = keys(cfg.never_takes), keys(cfg.never_before)
        self.pronouns, self.titles, self.prepositions, self.months = keys(cfg.pronouns), keys(cfg.titles), keys(cfg.preposition_nouns), keys(cfg.months)
        self.compounds = frozenset((key_of(a), key_of(b)) for a, b in cfg.compound_pairs)

    def analyze(self, words: list[Word], readings: list[str], opinions: dict[int, list[Opinion]] | None = None,
                confidences: list[str] | None = None, table_set: frozenset[int] = frozenset()) -> list[Boundary]:
        """One boundary per word (the last word's is the end of the block). `readings` are the final readings, one per word; `confidences` the
        editor's confidence of each reading (high | medium | low); `table_set` the words whose reading the film table set (their sounds, not their
        grammar: a reading cache written before the table kept the ezafe has lost it there)."""
        opinions = opinions or {}
        return [self.boundary(w, words[w.n + 1] if w.n + 1 < len(words) else None, readings[w.n], readings[w.n + 1] if w.n + 1 < len(words) else "",
                              tuple(opinions.get(w.n, ())), (confidences or [])[w.n] if confidences and w.n < len(confidences) else "high", w.n in table_set)
                for w in words]

    def boundary(self, word: Word, nxt: Word | None, reading: str, next_reading: str, opinions: tuple[Opinion, ...], confidence: str,
                 table_set: bool = False) -> Boundary:
        conf = self.cfg.confidence
        stem, ezafe, pause = plain_stem(reading)
        next_stem = plain_stem(next_reading)[0] if next_reading else ""
        sound = final_sound(stem) if reading != NUMBER_WORD else ""
        written = written_ezafe(word, sound)
        said = bool(ezafe) or (written and table_set)          # the table's reading has no grammar: the ezafe the text writes stands

        def made(required: bool | None, relation: str, confidence_: float, evidence: str, conflicts: tuple[str, ...] = (), phonology_error: str = "") -> Boundary:
            kind = "UNCERTAIN" if required is None else type_for(sound) if required else "NONE"
            joined = scheme.ezafe_form(stem) if required and stem else stem
            expected = " ".join(x for x in (joined if reading != NUMBER_WORD else word.plain, next_stem if next_reading != NUMBER_WORD else (nxt.plain if nxt else "")) if x)
            cls = phonological_class(sound, word.plain) if kind in ("E", "YE") else ""
            heard = realizations_of(stem, next_stem, cls) if cls else (expected,)
            return Boundary(word.n, word.surface, nxt.surface if nxt else "", required, kind, sound, expected, relation, round(confidence_, 2), evidence, written,
                            conflicts, phonology_error, opinions, phonological_class=cls, realizations=heard)

        phonology_error = ""
        if said and sound and ezafe == "ye" and type_for(sound) == "E":
            phonology_error = f"the reading writes -ye after the consonant {sound}"
        # 1. the rules: an ezafe cannot be said here
        blocked = self.blocked(word, nxt, reading, pause)
        if blocked:
            if said or written:
                return made(None, "unknown", conf["conflict"], "conflict", (f"rule: {blocked}",) + (("orthography",) if written else ()) + (("reading",) if said else ()))
            return made(False, "none", conf["rule"], "rule")
        # 2. what each source says
        votes = {"reading": said} | ({"orthography": True} if written else {})
        for o in opinions:
            if o.present is not None and o.confidence >= self.cfg.uncertain_below:
                votes[o.source] = o.present
        unsure = [o.source for o in opinions if o.present is None or o.confidence < self.cfg.uncertain_below]
        relation = self.relation(word, nxt, opinions) if said or written else "none"
        disagree = tuple(sorted(k for k, v in votes.items() if v != said))
        if written and not said:
            return made(None, relation, conf["conflict"], "conflict", disagree, phonology_error)
        # 3. the ambiguities the text has to settle itself
        ambiguous = self.ambiguous(word, nxt)
        if ambiguous and not written:
            sure = [o for o in opinions if o.present is not None and o.confidence >= max(self.cfg.uncertain_below, conf["agents_agree"])]
            if not sure or any(o.present != said for o in sure):
                return made(None, ambiguous, conf["uncertain"], "uncertain", disagree)
        if disagree:
            return made(said, relation, conf["conflict"], "conflict", disagree, phonology_error)
        if unsure:
            return made(None, relation, conf["uncertain"], "uncertain", tuple(unsure), phonology_error)
        if written:
            return made(True, relation, conf["orthography"], "orthography", (), phonology_error)
        if len([o for o in opinions if o.present is not None]) >= 2:
            return made(said, relation, conf["agents_agree"], "agents_agree", (), phonology_error)
        return made(said, relation, conf.get(confidence, conf["medium"]), "linguistic_inference", (), phonology_error)

    def blocked(self, word: Word, nxt: Word | None, reading: str, pause: bool) -> str:
        """Why no ezafe can be said after this word ("" when it can)."""
        if nxt is None:
            return "the end of the block"
        if word.number or reading == NUMBER_WORD:
            return "digits are read by code"
        if any(ch in PHRASE_END for ch in word.after):
            return f"the phrase ends at {word.after.strip()!r}"
        if pause:
            return "the reading pauses after the word"
        if base_key(word) in self.never_takes:
            return f"{word.plain} never takes an ezafe"
        if nxt.key in self.never_before:
            return f"no ezafe before {nxt.plain}"
        if any(nxt.surface.startswith(p) for p in self.cfg.verb_prefixes):
            return f"{nxt.plain} is a verb"
        return ""

    def ambiguous(self, word: Word, nxt: Word | None) -> str:
        """The relation an ambiguous pair would have ("" when the pair is not one)."""
        if nxt is None:
            return ""
        if base_key(word) in self.months and nxt.number:
            return "date"
        if (base_key(word), nxt.key) in self.compounds:
            return "compound"
        return ""

    def relation(self, word: Word, nxt: Word | None, opinions: tuple[Opinion, ...]) -> str:
        """The relation the agents named (the arbiter first), or the one the word lists name, or unknown."""
        for source in ("arbiter", "checker", "editor"):
            named = next((o.relation for o in opinions if o.source == source and o.present and o.relation not in ("unknown", "none")), "")
            if named:
                return named
        if nxt is not None and nxt.key in self.pronouns:
            return "pronoun_possessor"
        if base_key(word) in self.titles:
            return "title_name"
        if base_key(word) in self.prepositions:
            return "preposition_like"
        if base_key(word) in self.months and nxt is not None and nxt.number:
            return "date"
        return "unknown"


def mark(word: Word, boundary: Boundary) -> str | None:
    """The word with only its ezafe written on it (its letters and its other marks untouched), or None when there is nothing to add or no safe way to
    write it: a kasra after a consonant or a ه said h, a hamza on a ه said e, a kasra on a final yeh said i, a carrier yeh after ā / u / o."""
    if boundary.ezafe_type not in ("E", "YE") or boundary.written:
        return None
    sound, surface = boundary.final_phoneme, word.surface
    if VOWEL_MARKS.sub("", surface[-1:]) == "":
        return None                                     # the word already ends in a mark: nothing is written over it
    if boundary.ezafe_type == "E":
        return surface + KASRA
    if sound == "e" and surface.endswith("ه"):
        return surface + HAMZA_ABOVE
    if sound == "i" and surface.endswith("ی"):
        return surface + KASRA
    if sound in ("ā", "u", "o") and surface.endswith(("ا", "و")):
        return surface + "ی"
    return None


ERRORS = ("false_positive", "false_negative", "phonology", "uncertainty", "relation", "evidence", "intervention")


def evaluate(cases: list[dict]) -> dict:
    """Runs decision cases ({"text", "readings", "opinions": {n: [opinion]}, "expect": {n: {"type", "relation"?, "evidence"?, "mark"?, "phonology_error"?}}})
    through the analyzer and counts each kind of error apart: an ezafe found where none is said, one missed, -e for -ye (or the reverse), a certainty
    the evidence does not give (or an uncertainty it settles), a wrong relation or evidence, a wrong mark for the voice."""
    from app.documentary.reading.words import tokenize

    analyzer = EzafeAnalyzer()
    counts = dict.fromkeys(ERRORS, 0)
    failures: list[dict] = []
    checked = 0
    for case in cases:
        words = tokenize(case["text"])
        opinions = {int(n): [Opinion.parse(o["source"], o) for o in ops] for n, ops in (case.get("opinions") or {}).items()}
        found = analyzer.analyze(words, case["readings"], opinions, case.get("confidences"))
        for n, want in case["expect"].items():
            got, checked = found[int(n)], checked + 1
            problems = []
            if want["type"] != got.ezafe_type:
                kinds = {want["type"], got.ezafe_type}
                problems.append("uncertainty" if "UNCERTAIN" in kinds else "false_positive" if want["type"] == "NONE" else "false_negative"
                                if got.ezafe_type == "NONE" else "phonology")
            if "relation" in want and want["relation"] != got.grammatical_relation:
                problems.append("relation")
            if "evidence" in want and want["evidence"] != got.evidence_type:
                problems.append("evidence")
            if "class" in want and want["class"] != got.phonological_class:
                problems.append("phonology")
            if "phonology_error" in want and want["phonology_error"] != bool(got.phonology_error):
                problems.append("phonology")
            if "mark" in want and want["mark"] != mark(words[int(n)], got):
                problems.append("intervention")
            for p in problems:
                counts[p] += 1
            if problems:
                failures.append({"case": case["name"], "n": int(n), "problems": problems, "expected": want, "got": got.describe(), "mark": mark(words[int(n)], got)})
    return {"cases": len(cases), "boundaries": checked, "errors": counts, "failures": failures}
