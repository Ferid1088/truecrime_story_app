"""The phoneme-first reference of a word: from the word's contextual reading (the reading agents' Finglish, the arbiter's decision, the owner's approval) to
its phonemes, syllables, rule-based stress, morphology and its complete REFERENCE_IRAB on the word's own letters; and an independent validator that
reads the IRAB back to phonemes and checks every mark.

The REFERENCE_IRAB convention of the project:
  a consonant followed by a short vowel carries it (fatha a, kasra e, damma o); a consonant followed by nothing (another consonant, the end of the word)
  carries a sukun; a consonant followed by a long vowel carries nothing (the long vowel is its letter: ا ی و, never a mark); a geminate carries a shadda
  (and the mark of the vowel after it; a final geminate the shadda alone); an ezafe -e is the kasra of the last consonant (it replaces the final sukun);
  -ye is a hamza on a ه said e, a kasra on a carrier yeh, a kasra on a final yeh said i; the letters of the word are never changed (a carrier yeh after
  a final ā / u / o the text does not write is the one thing added, and it is flagged).

The reference is a linguistic reference, not the voice's input: what the voice is given is the planner's decision (pronunciation_planner.py)."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from functools import cache

from app.core.ai_config import ai_config
from app.documentary.reading import scheme, spelling
from app.documentary.reading.words import VOWEL_MARKS, Word

FATHA, KASRA, DAMMA, SUKUN, SHADDA, HAMZA_ABOVE = "َ", "ِ", "ُ", "ْ", "ّ", "ٔ"
ZWNJ = "‌"
SHORT = {"a": FATHA, "e": KASRA, "o": DAMMA}
VOWEL_OF = {v: k for k, v in SHORT.items()}
LONG = ("ā", "i", "u")
LETTERS = {"b": "ب", "p": "پ", "t": "تط", "s": "سصث", "j": "ج", "ch": "چ", "h": "هح", "kh": "خ", "d": "د", "z": "زذضظ", "r": "ر", "zh": "ژ", "sh": "ش",
           "gh": "قغ", "f": "ف", "k": "ک", "g": "گ", "l": "ل", "m": "م", "n": "ن", "v": "و", "w": "و", "y": "ی", "'": "عءئأؤ"}
SYMBOL_OF = {letter: sym for sym, letters in LETTERS.items() if sym != "w" for letter in letters}
CONSONANTS = set(LETTERS)
VOWELS = {"a", "e", "o", "ā", "i", "u"}


@dataclass(frozen=True)
class Morph:
    text: str
    kind: str          # stem | continuous | negation | plural | enclitic


@dataclass(frozen=True)
class Reference:
    """The complete reference of one word in its sentence."""

    surface: str
    finglish: str                       # the reading as the agents wrote it (with its ezafe)
    phonemes: tuple[str, ...]           # the reading's sounds in the scheme, one symbol per sound (ezafe included)
    ipa: str
    syllables: tuple[str, ...]          # in the scheme, with the ezafe
    stress: int                         # the index of the stressed syllable (rule-based: a reference, not a measurement)
    morphology: tuple[Morph, ...]
    irab: str | None                    # the complete REFERENCE_IRAB, or None when the reading cannot be aligned to the word's letters
    added_carrier: bool = False         # the ezafe's yeh after ā / u / o was added because the text does not write it
    problems: tuple[str, ...] = field(default_factory=tuple)        # what the validator found (empty: valid)

    @property
    def valid(self) -> bool:
        return self.irab is not None and not self.problems

    def describe(self) -> dict:
        return {"surface": self.surface, "finglish": self.finglish, "phonemes": list(self.phonemes), "ipa": self.ipa, "syllables": list(self.syllables),
                "stress": self.stress, "morphology": [{"text": m.text, "kind": m.kind} for m in self.morphology], "reference_irab": self.irab,
                "added_carrier": self.added_carrier, "valid": self.valid, "problems": list(self.problems)}


# --- phonemes and syllables -----------------------------------------------------------------------------------------------------------------

def phonemes_of(reading: str) -> tuple[str, ...]:
    """The sounds of a reading, one symbol each; a diphthong is a vowel and its glide (ey: e y, ow: a w), an ezafe its sounds (-e: e, -ye: y e).
    Raises ValueError for a reading that is not of the scheme."""
    body, _ = scheme.split_pause(reading)
    stem, ezafe = scheme.split_ezafe(body)
    out: list[str] = []
    for piece in scheme.canonical(stem).split("-"):
        parts = spelling.symbols(piece)
        if "".join(parts) != piece:
            raise ValueError(f"not a reading of the scheme: {reading!r}")
        for p in parts:
            out += ["e", "y"] if p == "ey" else ["a", "w"] if p == "ow" else [p]
    return tuple(out + (["e"] if ezafe == "e" else ["y", "e"] if ezafe == "ye" else []))


IPA = {"kh": "x", "gh": "ɢ", "zh": "ʒ", "sh": "ʃ", "ch": "tʃ", "j": "dʒ", "y": "j", "w": "w", "r": "ɾ", "g": "ɡ", "'": "ʔ", "a": "æ", "e": "e", "o": "o",
       "ā": "ɒː", "i": "iː", "u": "uː"}


def ipa_of(phonemes: tuple[str, ...]) -> str:
    return "".join(IPA.get(p, p) for p in phonemes)


def syllables_of(phonemes: tuple[str, ...]) -> tuple[str, ...]:
    """Persian syllables: every syllable starts with one consonant (a word-initial vowel with the glottal stop the scheme leaves unwritten), a nucleus
    and up to the consonants before the next onset (CV, CVC, CVCC). Raises ValueError for a shape Persian does not have (two vowels in a row inside
    a word, an onset cluster)."""
    nuclei = [i for i, p in enumerate(phonemes) if p in VOWELS]
    if not nuclei:
        raise ValueError("no vowel")
    out: list[str] = []
    starts = [0] + [n - 1 for n in nuclei[1:]]
    for k, (start, nucleus) in enumerate(zip(starts, nuclei, strict=True)):
        if k and phonemes[start] in VOWELS:
            raise ValueError(f"two vowels in a row: {''.join(phonemes)}")
        if nucleus - start > 1:
            raise ValueError(f"an onset cluster: {''.join(phonemes[start:nucleus + 1])}")
        end = starts[k + 1] if k + 1 < len(starts) else len(phonemes)
        if end - nucleus - 1 > 3:
            raise ValueError(f"a coda of more than three consonants: {''.join(phonemes[nucleus + 1:end])}")
        out.append("".join(phonemes[start:end]))
    return tuple(out)


def morphology_of(word: Word) -> tuple[Morph, ...]:
    """The word cut where its writing shows a boundary (a half-space): a verb prefix (می‌، نمی‌), the stem, a plural ending, an enclitic. A word written
    in one piece is one stem: nothing is guessed (کتابم may be ketāb-am or a word of its own). The cut never changes a pronunciation."""
    cfg = ai_config.morphology
    plain = VOWEL_MARKS.sub("", word.surface).replace("ي", "ی").replace("ك", "ک")
    pieces = plain.split(ZWNJ)
    out: list[Morph] = []
    if len(pieces) > 1 and pieces[0] in cfg.verb_prefixes:
        out.append(Morph(pieces[0], cfg.verb_prefixes[pieces[0]]))
        pieces = pieces[1:]
    out.append(Morph(pieces[0], "stem"))
    for piece in pieces[1:]:
        plural = next((p for p in cfg.plural if piece.startswith(p)), None)
        if plural:
            out.append(Morph(plural, "plural"))
            piece = piece[len(plural):]
        if piece:
            out.append(Morph(piece, "enclitic" if piece in cfg.enclitics else "stem"))
    return tuple(out)


def stress_of(syllables: tuple[str, ...], morphs: tuple[Morph, ...], ezafe: bool) -> int:
    """The stressed syllable by the rules of Persian word stress: a negation or continuous verb prefix takes it; otherwise the last syllable that is not
    an enclitic (an ezafe, a possessive, the copula) takes it. A reference by rule, never a measurement of the audio."""
    if any(m.kind in ("negation", "continuous") for m in morphs):
        return 0
    trailing = (1 if ezafe else 0) + sum(ai_config.morphology.enclitics.get(m.text, 0) for m in morphs if m.kind == "enclitic")
    return max(0, len(syllables) - 1 - trailing)


# --- the complete REFERENCE_IRAB --------------------------------------------------------------------------------------------------------------

def letters_of(surface: str) -> str:
    """The word's letters without marks (an ۀ is ه with the ezafe written: the reference writes the ezafe itself)."""
    return VOWEL_MARKS.sub("", unicodedata.normalize("NFC", surface)).replace("ۀ", "ه").replace("ي", "ی").replace("ك", "ک")


def align(letters: str, sounds: tuple[str, ...]) -> list[tuple[str, tuple[str, ...]]] | None:
    """The word's letters, each with its marks, read as `sounds` (the first alignment found; None when the reading cannot be written on these letters).
    Only the ezafe's carrier yeh may be added at the end."""

    @cache
    def go(i: int, j: int, last_mark: str) -> tuple | None:
        if i == len(letters):
            rest = sounds[j:]
            if not rest:
                return ()
            if rest == ("y", "e") and i and letters[i - 1] in "اوی" and last_mark == "long":
                return ("+carrier",)
            if rest == ("y", "e") and i and letters[i - 1] == "ه" and last_mark == "silent":
                return ("+hamza",)
            return None
        c = letters[i]
        initial = i == 0
        options: list[tuple] = []
        if c == ZWNJ:
            options.append(((c, ()), i + 1, j, last_mark))
        sound = sounds[j] if j < len(sounds) else None
        # a consonant letter with its sound
        if sound in CONSONANTS and c in LETTERS[sound]:
            k = j + 1
            geminate = k < len(sounds) and sounds[k] == sound
            if geminate:
                k += 1
            nxt = sounds[k] if k < len(sounds) else None
            if nxt in SHORT:
                options.append(((c, ((SHADDA,) if geminate else ()) + (SHORT[nxt],)), i + 1, k + 1, SHORT[nxt]))
            elif nxt in LONG:
                options.append(((c, (SHADDA,) if geminate else ()), i + 1, k, "before_long"))
            else:
                options.append(((c, (SHADDA,) if geminate and nxt is None else (SHADDA, SUKUN) if geminate else (SUKUN,)), i + 1, k, "sukun"))
        # a word-initial vowel: on ا (or آ), or on ع / ء when the reading leaves the glottal stop unwritten
        if initial:
            j0 = j + 1 if sound == "'" else j
            v = sounds[j0] if j0 < len(sounds) else None
            if c == "آ" and v == "ā":
                options.append(((c, ()), i + 1, j0 + 1, "long"))
            if c in "اعء" and v in SHORT:
                options.append(((c, (SHORT[v],)), i + 1, j0 + 1, SHORT[v]))
            if c == "ا" and v in ("i", "u"):
                options.append(((c, ()), i + 1, j0, "before_long"))
            if c == "و" and len(letters) == 1 and sound == "o" and len(sounds) == 1:
                options.append(((c, ()), 1, 1, "long"))               # the conjunction و said o
        # a long vowel letter
        if c == "ا" and sound == "ā" and not initial:
            options.append(((c, ()), i + 1, j + 1, "long"))
        if c == "ی" and sound == "i":
            options.append(((c, ()), i + 1, j + 1, "long"))
            if j + 2 < len(sounds) and sounds[j + 1] == "y" and (i + 1 == len(letters) or letters[i + 1] != "ی"):
                after = sounds[j + 2]                       # i and y written as one yeh: میامی miyāmi, بقیه baghiye, محکومیت mahkumiyat
                if after in LONG:
                    options.append(((c, ()), i + 1, j + 2, "before_long"))
                elif after in SHORT:
                    options.append(((c, (SHORT[after],)), i + 1, j + 3, SHORT[after]))
        if c == "و" and sound == "u":
            options.append(((c, ()), i + 1, j + 1, "long"))
        # a silent letter: و after a damma (خود، دو), و between خ and ا (خواهر), ه after a short vowel at the end of the word or before a half-space
        if c == "و" and (last_mark == DAMMA or (i and letters[i - 1] == "خ" and i + 1 < len(letters) and letters[i + 1] == "ا" and sound == "ā")):
            options.append(((c, ()), i + 1, j, "silent"))
        if c == "ه" and last_mark in (KASRA, FATHA) and (i + 1 == len(letters) or letters[i + 1] == ZWNJ):
            options.append(((c, ()), i + 1, j, "silent"))
        for unit, ni, nj, mark in options:
            rest = go(ni, nj, mark)
            if rest is not None:
                return (unit,) + rest
        return None

    found = go(0, 0, "")
    return list(found) if found is not None else None


def write(units: list) -> tuple[str, bool]:
    """The aligned letters as text, with the ezafe the end of the alignment asks for. Returns (the IRAB, whether a carrier yeh was added)."""
    added = False
    out: list[str] = []
    for u in units:
        if u == "+carrier":
            if out and out[-1].startswith("ی"):
                out[-1] += KASRA                        # a final i: the kasra on its own yeh, never a second yeh
            else:
                out.append("ی" + KASRA)
                added = True
        elif u == "+hamza":
            out[-1] += HAMZA_ABOVE
        else:
            out.append(u[0] + "".join(u[1]))
    return unicodedata.normalize("NFC", "".join(out)), added


def irab_of(surface: str, sounds: tuple[str, ...]) -> tuple[str | None, bool]:
    units = align(letters_of(surface), sounds)
    if units is None:
        return None, False
    return write(units)


# --- the independent validator ------------------------------------------------------------------------------------------------------------

def units_of(irab: str) -> list[tuple[str, str]] | None:
    """The IRAB as (letter, its marks); None when a mark stands on no letter."""
    out: list[tuple[str, str]] = []
    for ch in irab:
        if VOWEL_MARKS.fullmatch(ch):
            if not out or out[-1][0] == ZWNJ:
                return None
            out[-1] = (out[-1][0], out[-1][1] + ch)
        else:
            out.append((ch, ""))
    return out


def decode(irab: str) -> tuple[str, ...]:
    """The sounds an IRAB writes, read by its own rules (not by the generator's alignment). Raises ValueError for a letter it cannot read."""
    raw = units_of(irab) or []
    ends = {k for k, u in enumerate(raw) if k + 1 == len(raw) or raw[k + 1][0] == ZWNJ}       # the last letter of a written piece
    kept = [(k, u) for k, u in enumerate(raw) if u[0] != ZWNJ]
    units = [u for _, u in kept]
    piece_end = [k in ends for k, _ in kept]
    out: list[str] = []
    for k, (c, marks) in enumerate(units):
        prev = units[k - 1] if k else None
        nxt = units[k + 1] if k + 1 < len(units) else None
        long_next = nxt is not None and nxt[0] in "اوی" and (not nxt[1] or (nxt[0] == "ی" and any(m in nxt[1] for m in SHORT.values()) and SUKUN not in nxt[1]))
        vowel = next((VOWEL_OF[m] for m in marks if m in VOWEL_OF), None)
        after_consonant = bool(out) and out[-1] in CONSONANTS and prev is not None and SUKUN not in prev[1]     # a yeh or vav after a consonant
        if c == "و" and not marks and len(units) == 1:
            out.append("o")                                     # the conjunction و said o
            continue
        if c == "آ":
            out.append("ā")
        elif c == "ا" and k == 0:
            if vowel:
                out.append(vowel)
            elif nxt is None or nxt[0] not in "یو" or nxt[1]:
                out.append("ā")
        elif c == "ا":
            out.append("ā")
        elif c == "و" and not marks and prev is not None and (DAMMA in prev[1] or (prev[0] == "خ" and not prev[1] and nxt is not None and nxt[0] == "ا")):
            continue                                            # silent
        elif c == "ی" and vowel and SUKUN not in marks:
            out += (["i", "y"] if after_consonant else ["y"]) + [vowel]     # one yeh for i and y after a consonant (بقیه، صندلیِ), else the consonant y
        elif c in "وی" and not marks:
            glide = "y" if c == "ی" else "v"
            if after_consonant:
                out += ["i", "y"] if c == "ی" and nxt is not None and nxt[0] == "ا" and not nxt[1] else ["i" if c == "ی" else "u"]   # میامی: i and y in one yeh
            elif k == 0 or (long_next and out):
                out.append(glide)       # a word's first letter (ویژه، یا، والیبال) or a consonant before a long vowel (شنوایی vā-yi, تأیید ta'-yid)
            else:
                out.append("i" if c == "ی" else "u")
        elif c == "ه" and piece_end[k] and (not marks or marks == HAMZA_ABOVE) and prev is not None and (KASRA in prev[1] or FATHA in prev[1]):
            out += ["y", "e"] if marks == HAMZA_ABOVE else []    # a silent ه, with the ezafe's hamza
        elif c in SYMBOL_OF:
            sym = SYMBOL_OF[c]
            if k == 0 and c in "عء" and vowel and sym == "'":
                out.append("'")
            else:
                out.append(sym)
            if SHADDA in marks:
                out.append(sym)
            if vowel:
                out.append(vowel)
            elif SUKUN not in marks and SHADDA not in marks and not long_next and not (c == "خ" and nxt is not None and nxt[0] == "و"):
                raise ValueError(f"the consonant {c} has no mark and no long vowel after it")
        else:
            raise ValueError(f"a letter the reference does not read: {c}")
    return tuple(out)


def validate(surface: str, sounds: tuple[str, ...], irab: str, added_carrier: bool = False) -> tuple[str, ...]:
    """Every problem of an IRAB against its word and its reading (empty: valid). Checked by its own rules: the marks stand on letters, one vowel or
    sukun per letter, no vowel with a sukun, no shadda on a long vowel letter, the word's letters and half-spaces unchanged (but the flagged carrier),
    no final sukun under an ezafe, a mark the text already has is kept, and the IRAB read back gives the reading's sounds."""
    problems: list[str] = []
    if unicodedata.normalize("NFC", irab) != irab:
        problems.append("not NFC")
    units = units_of(irab)
    if units is None:
        return ("a mark stands on no letter",)
    for c, marks in units:
        vowels = [m for m in marks if m in (FATHA, KASRA, DAMMA, SUKUN)]
        if len(vowels) > 1:
            problems.append(f"{c}: two vowel marks or a vowel with a sukun")
        if SHADDA in marks and c in "اآ":
            problems.append(f"{c}: a shadda on a long vowel letter")
        if HAMZA_ABOVE in marks and c != "ه":
            problems.append(f"{c}: an ezafe hamza on a letter that is not ه")
    want = letters_of(surface)
    have = "".join(c for c, _ in units)
    if have != want and not (added_carrier and have == want + "ی"):
        problems.append(f"the letters changed: {want} -> {have}")
    if sounds and sounds[-1] == "e" and units and SUKUN in units[-1][1]:
        problems.append("a final sukun where the ezafe is said")
    written = units_of(VOWEL_MARKS.sub(lambda m: m.group() if m.group() in (FATHA, KASRA, DAMMA, SUKUN, SHADDA) else "", unicodedata.normalize("NFC", surface)))
    for (c, mine), (_, theirs) in zip(written or [], units, strict=False):
        if mine and not set(mine) <= set(theirs):
            problems.append(f"{c}: the text's own mark {mine} is not kept")
    try:
        read = decode(irab)
    except ValueError as e:
        problems.append(str(e))
    else:
        if comparable(read) != comparable(sounds):
            problems.append(f"the IRAB reads {''.join(read)}, the reading is {''.join(sounds)}")
    return tuple(problems)


def comparable(sounds: tuple[str, ...]) -> tuple[str, ...]:
    """The sounds as letters can show them: without a word-initial glottal stop (the scheme writes it or not; the letter is the same), the glide w as v
    (one letter), and without the y between i and a vowel (one yeh writes both: میامی)."""
    out = ["v" if p == "w" else p for p in (sounds[1:] if sounds[:1] == ("'",) else sounds)]     # the glide of ow is the letter و
    return tuple(p for k, p in enumerate(out) if not (p == "y" and k and out[k - 1] == "i" and k + 1 < len(out) and out[k + 1] in VOWELS))


# --- the whole reference ----------------------------------------------------------------------------------------------------------------------

def reference_of(word: Word, reading: str) -> Reference | None:
    """The reference of a word read as `reading`; None for a number (read by code) or a reading that is not of the scheme."""
    if word.number or reading == "NUM":
        return None
    try:
        sounds = phonemes_of(reading)
    except ValueError:
        return None
    morphs = morphology_of(word)
    problems: list[str] = []
    stem_sounds = sounds[:-2] if sounds[-2:] == ("y", "e") and scheme.split_ezafe(scheme.split_pause(reading)[0])[1] == "ye" else \
        sounds[:-1] if scheme.split_ezafe(scheme.split_pause(reading)[0])[1] == "e" else sounds
    try:
        syllables = syllables_of(sounds)
        stem_syllables = syllables_of(stem_sounds)
    except ValueError as e:
        syllables, stem_syllables = (), ()
        problems.append(f"syllables: {e}")
    irab, added = irab_of(word.surface, sounds)
    if irab is None:
        problems.append("the reading cannot be written on the word's own letters")
    else:
        problems += validate(word.surface, sounds, irab, added)
    stress = stress_of(stem_syllables, morphs, False) if stem_syllables else 0
    return Reference(word.surface, scheme.split_pause(reading)[0], sounds, ipa_of(sounds), syllables, stress, morphs, irab, added, tuple(problems))


def minimal(irab: str) -> str:
    """The minimal marking of a reference for the voice: its short vowels, shaddas and ezafe, without the sukuns (a sukun says nothing the letters do
    not already say to a reader, and every extra mark is a change to the voice's input)."""
    return irab.replace(SUKUN, "")


def sukun_places(irab: str) -> list[bool]:
    """For every letter of an IRAB whether it carries a sukun (for the sukun placement accuracy)."""
    return [SUKUN in marks for _, marks in units_of(irab) or []]


def evaluate(cases: list[dict]) -> dict:
    """Runs reference cases ({"sentence", "word", "reading", "expect": {"irab", "syllables"?, "stress"?, "morphology"?}}) and reports each metric apart:
    the complete IRAB (exact), the sukun placement (per letter), the phonemes the IRAB reads back, the validator's verdict, syllables, stress and
    morphology. No metric is folded into another."""
    from app.documentary.reading.words import tokenize

    totals = {"irab_exact": 0, "sukun_letters": 0, "sukun_right": 0, "phonemes_right": 0, "valid": 0, "syllables_right": 0, "syllables_checked": 0,
              "stress_right": 0, "stress_checked": 0, "morphology_right": 0, "morphology_checked": 0}
    failures: list[dict] = []
    for case in cases:
        word = next(w for w in tokenize(case["sentence"]) if w.surface == case["word"])
        ref, want = reference_of(word, case["reading"]), case["expect"]
        got = ref.irab if ref else None
        problems = []
        if got == want["irab"]:
            totals["irab_exact"] += 1
        else:
            problems.append("irab")
        mine, theirs = sukun_places(got or ""), sukun_places(want["irab"])
        totals["sukun_letters"] += len(theirs)
        totals["sukun_right"] += sum(1 for a, b in zip(mine, theirs, strict=False) if a == b) if len(mine) == len(theirs) else 0
        try:
            if comparable(decode(want["irab"])) == comparable(ref.phonemes if ref else ()):
                totals["phonemes_right"] += 1
            else:
                problems.append("phonemes")
        except ValueError:
            problems.append("phonemes")
        if ref is not None and ref.valid:
            totals["valid"] += 1
        else:
            problems.append("validator")
        for key, have in (("syllables", list(ref.syllables) if ref else None), ("stress", ref.stress if ref else None),
                          ("morphology", [m.kind for m in ref.morphology] if ref else None)):
            if key in want:
                totals[f"{key}_checked"] += 1
                if want[key] == have:
                    totals[f"{key}_right"] += 1
                else:
                    problems.append(key)
        if problems:
            failures.append({"case": case["name"], "problems": problems, "expected": want, "got": ref.describe() if ref else None})
    return {"cases": len(cases), **totals, "failures": failures}
