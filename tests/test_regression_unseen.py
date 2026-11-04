"""Unseen regression sentences (none from any case), one for every pronunciation failure found so far, and an audit of the REFERENCE_IRAB rules on every
word of them: final sukun, ezafe, long vowels, clusters and the Unicode placement of every mark."""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from app.documentary.reading import reference as REF
from app.documentary.reading.words import tokenize

CASES = json.loads((Path(__file__).resolve().parents[1] / "prompts" / "pronunciation" / "decision_cases" / "regression_unseen.json").read_text(encoding="utf-8"))["cases"]


def ref_of(case: dict) -> REF.Reference:
    word = next(w for w in tokenize(case["sentence"]) if w.surface == case["word"])
    return REF.reference_of(word, case["reading"])


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_each_failure_found_so_far_stays_fixed(case):
    r = ref_of(case)
    got = r.irab if r is not None and r.valid else None
    assert got == case["expect"]["irab"], (case["failure"], r.describe() if r else None)


@pytest.mark.parametrize("case", [c for c in CASES if c["expect"]["irab"]], ids=[c["name"] for c in CASES if c["expect"]["irab"]])
def test_the_audit_of_every_valid_reference(case):
    r = ref_of(case)
    irab, units = r.irab, REF.units_of(r.irab)
    assert unicodedata.normalize("NFC", irab) == irab and units is not None, "valid Unicode, every mark on a letter"
    assert REF.letters_of(irab) == REF.letters_of(case["word"]), "the word's letters unchanged"
    marks = [m for _, m in units]
    assert all(sum(x in m for x in (REF.FATHA, REF.KASRA, REF.DAMMA, REF.SUKUN)) <= 1 for m in marks), "one vowel or sukun per letter"
    assert all(not any(x in m for x in (REF.FATHA, REF.KASRA, REF.DAMMA)) for c, m in units if c == "آ"), "آ is ā: no short vowel on it"
    long_vowels = sum(1 for p in r.phonemes if p in REF.LONG)
    writers = [k for k, (c, m) in enumerate(units) if c in "اآوی" and (not any(x in m for x in (REF.FATHA, REF.KASRA, REF.DAMMA, REF.SUKUN))
               or REF.DAGGER_ALEF in m or (c == "ی" and m == REF.KASRA and k == len(units) - 1)    # a final yeh with the ezafe's kasra still writes its i
               or (c == "ی" and k and not units[k - 1][1] and units[k - 1][0] not in "اوی"))]       # one yeh for i and y (بقیه): its mark is the vowel after y
    assert len(writers) >= long_vowels, "every long vowel is written by its letter, never by a mark"
    if r.phonemes[-1:] == ("e",) and r.irab[-1] != "ه":
        assert REF.SUKUN not in marks[-1], "an ezafe is never under a final sukun"
    assert REF.reads_as(REF.decode(irab), r.phonemes), "the IRAB reads back as the reading"


def test_the_cases_name_the_failure_they_guard_and_are_not_from_case_15():
    case15 = (Path(__file__).resolve().parents[1] / "data" / "cases" / "15" / "reading" / "fa_v289.json")
    texts = case15.read_text(encoding="utf-8") if case15.exists() else ""
    assert all(c["failure"] for c in CASES)
    assert not any(c["sentence"].rstrip(".") in texts for c in CASES)
