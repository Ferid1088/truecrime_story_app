"""The Persian Reading Engine: the transcription check, the lexical evidence, the morphology, the audit, the contextual selector and the engine."""

import asyncio
import json
from pathlib import Path

import pytest

from app.documentary.reading import evidence as EV
from app.documentary.reading.audit import Auditor, summary
from app.documentary.reading.block import BlockInput
from app.documentary.reading.engine import ReadingEngine
from app.documentary.reading.evidence import Lexicon
from app.documentary.reading.morphology import Morphology, distinct
from app.documentary.reading.selector import Selector, with_ezafe
from app.documentary.reading.transcription import Transcription

DATA = json.loads((Path(__file__).parent / "data" / "reading_regressions.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def lexicon(tmp_path_factory) -> Lexicon:
    raw = tmp_path_factory.mktemp("lex") / "fixture.jsonl"
    raw.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in DATA["wiktionary_fixture"]), encoding="utf-8")
    lex = Lexicon()
    index, verbs = {}, {}
    for line in raw.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        entry = lex.entry_of(rec)
        if entry:
            index.setdefault(EV.key_of(rec["word"]), []).append(entry)
            if rec.get("pos") == "verb":
                for k, v in lex.verb_forms(rec, entry).items():
                    verbs.setdefault(k, []).extend(v)
    lex.index, lex.verbs = index, verbs
    return lex


@pytest.fixture(scope="module")
def morphology(lexicon) -> Morphology:
    """The evidence and the grammar alone: no verified exception, so these tests show what the lexicon and the rules give."""
    return Morphology(lexicon, exceptions={})


@pytest.fixture(scope="module")
def with_exceptions(lexicon) -> Morphology:
    return Morphology(lexicon)


# --- transcription: format faults are rewritten and recorded, sound doubts are never rewritten -------------------------------------------

def test_format_variants_are_rewritten_and_recorded():
    tr = Transcription()
    checked = tr.check("ettefāq")
    assert (checked.normalized, checked.status) == ("ettefāgh", "format_fixed")
    assert [(f.before, f.after) for f in checked.fixes] == [("q", "gh")]
    assert tr.check("Rezā").normalized == "rezā" and tr.check("maʼmuli").normalized == "ma'muli" and tr.check("naraftе").normalized == "narafte"


def test_a_joiner_hyphen_and_a_space_only_join_parts_of_one_word_but_an_ezafe_hyphen_stays():
    tr = Transcription()
    assert tr.check("payām-hā").normalized == "payāmhā" and tr.check("tip siti").normalized == "tipsiti"
    assert tr.check("sag-hā-ye").normalized == "sag-hā-ye" and tr.check("payām-hā-ye").normalized == "payāmhā-ye" and tr.check("qāzi-ye|").normalized == "ghāzi-ye|"


def test_symbols_whose_sound_is_not_known_are_reported_never_rewritten():
    tr = Transcription()
    for reading, symbol in (("bēhtar", "ē"), ("bāw", "w")):
        checked = tr.check(reading)
        assert checked.status == "sound_doubt" and checked.normalized == reading and symbol in checked.ambiguous
    assert tr.check("rāhrow").status == "valid"            # the w of the diphthong ow is not a lone w
    assert tr.check("NUM").status == "valid"


# --- lexical evidence ------------------------------------------------------------------------------------------------------------------

def test_ipa_and_romanisation_are_written_in_the_scheme():
    assert EV.ipa_to_latin("[ɡu.ɾis.t̪ʰɔ́ːn]") == "guristān" and EV.ipa_to_latin("/ˈʔahd/") == "'ahd"
    assert EV.romanization_to_latin("gurestân") == "gurestān" and EV.romanization_to_latin("šoxm") == "shokhm"


def test_other_dialects_and_classical_romanisations_are_not_evidence(lexicon):
    got = lexicon.lookup("عهد").readings()
    assert "ahd" in got and all("ʔ" not in r for r in got)
    assert "gurestān" in lexicon.lookup("گورستان").readings() and not any("ō" in r for r in lexicon.lookup("گورستان").readings())


def test_every_candidate_keeps_its_source_page(lexicon):
    entry = lexicon.lookup("گاراژ").entries[0]
    assert entry.page.endswith("/wiki/گاراژ") and entry.candidates[0].kind in ("ipa", "romanization")


# --- morphology ------------------------------------------------------------------------------------------------------------------------

def test_clitics_plural_and_a_spoken_stem_are_analysed(morphology):
    assert "dokhtarhāsh" in distinct(morphology.options("دخترهاش"))
    assert "dokhtarā" in distinct(morphology.options("دخترها"))
    spoken = distinct(morphology.options("گورستون"))
    assert "gurestun" in spoken and "gurestān" not in spoken
    assert "khune" in distinct(morphology.options("خونه"))


def test_a_host_word_with_its_clitic_is_a_grammar_rule_not_a_dictionary_entry(morphology):
    options = morphology.options("بهم")
    assert "beham" in distinct(options) and "baham" not in distinct(options) and {o.basis for o in options} == {"rule"}
    assert "bāhāsh" in distinct(morphology.options("باهاش"))


def test_verbs_come_from_the_infinitive_of_the_lexicon_and_the_grammar_of_the_endings(morphology):
    assert "migoft" in distinct(morphology.options("می‌گفت")) and "nagoftam" in distinct(morphology.options("نگفتم"))
    assert "gofte" in distinct(morphology.options("گفته")) and distinct(morphology.options("می‌گفته")) == []


def test_a_word_without_a_stem_in_the_evidence_has_no_reading(morphology):
    assert morphology.options("اشلی") == []


# --- the owner's confirmed readings are permanent regressions ----------------------------------------------------------------------------

def test_the_unresolved_regressions_are_reported_not_silently_approved(morphology):
    for case in DATA["unresolved"]:
        assert morphology.options(case["surface"]) == []


# --- the audit -------------------------------------------------------------------------------------------------------------------------

def block(text: str) -> BlockInput:
    return BlockInput("B1", "S1", text)


def audited(morphology, text: str, readings: list[str]):
    return Auditor(morphology).block(block(text), readings)


def test_the_audit_separates_a_supported_reading_a_conflict_and_a_word_without_evidence(morphology):
    ok, bad, name = audited(morphology, "بهم گاراژ اشلی", ["beham", "gerāzh", "ashli"])
    assert ok.status == "supported" and bad.status == "vowel_conflict" and bad.options == ["gārāzh"] and name.status == "no_evidence"
    wrong = audited(morphology, "بهم", ["baham"])[0]
    assert wrong.status == "vowel_conflict" and "beham" in wrong.options


def test_an_ezafe_a_pause_and_a_joiner_are_not_a_difference_of_sound(morphology):
    got = audited(morphology, "دختر خانه", ["dokhtar-e|", "khāne"])
    assert [f.status for f in got] == ["supported", "supported"]


def test_coverage_and_agreement_are_measured_apart(morphology):
    findings = audited(morphology, "بهم گاراژ اشلی ۱۲", ["baham", "gārāzh", "ashli", "NUM"])
    s = summary(findings)
    assert s["numbers"] == 1 and s["coverage"] == round(2 / 3, 4) and s["agreement_with_evidence"] == 0.5


def test_format_faults_are_counted_apart_from_sound_conflicts(morphology):
    findings = audited(morphology, "گاراژ", ["Gārāzh"])
    assert findings[0].status == "supported" and findings[0].format_fixes == [("Gārāzh", "gārāzh")]
    assert summary(findings)["format_faults"] == 1


# --- the selector and the engine -------------------------------------------------------------------------------------------------------

class Script:
    """A model that chooses by the reading's text; counts its calls."""

    def __init__(self, pick: str, sure: bool = True, source: str = "lexicon"):
        self.pick, self.sure, self.source, self.calls = pick, sure, source, 0

    async def __call__(self, gen, body):
        self.calls += 1
        options = json.loads(body)["options"]
        letter = next((k for k, v in options.items() if v["reading"] == self.pick and v["source"] == self.source), "none")
        return {"choice": letter, "sure": self.sure, "reason": "fits the sentence"}, type("R", (), {"cost_usd": 0, "model": "m", "usage": {}})()


@pytest.fixture()
def engine_for(morphology, tmp_path, monkeypatch):
    from app.documentary.reading import selector as SEL

    async def ask(db, case_id, label, payload, role, runner):
        return (await runner(None, json.dumps(payload, ensure_ascii=False)))[0]

    monkeypatch.setattr(SEL, "ask", ask)

    def make(runner):
        return ReadingEngine(Auditor(morphology), Selector(None, 1, tmp_path / "sel.json", runner))

    return make


def test_the_model_chooses_the_lexicon_reading_and_the_writer_is_overruled(engine_for):
    script = Script("gārāzh")
    result = asyncio.run(engine_for(script).run([block("گاراژ")], {"B1": ["gārāj"]}))
    out = result.outcomes[0]
    assert (out.status, out.reading, out.writer) == ("resolved", "gārāzh", "gārāj") and script.calls == 1


def test_a_model_that_agrees_with_the_writer_against_the_evidence_does_not_approve_it(engine_for):
    result = asyncio.run(engine_for(Script("baham", source="writer")).run([block("بهم")], {"B1": ["baham"]}))
    out = result.outcomes[0]
    assert out.status == "uncertain" and out.reading == "baham" and "lexicon does not" in out.reason


def test_a_model_that_is_not_sure_leaves_the_word_uncertain(engine_for):
    out = asyncio.run(engine_for(Script("beham", sure=False)).run([block("بهم")], {"B1": ["baham"]})).outcomes[0]
    assert out.status == "uncertain" and out.reading == "baham"


def test_supported_words_need_no_model_and_words_without_evidence_stay_unverified(engine_for):
    script = Script("x")
    result = asyncio.run(engine_for(script).run([block("بهم اشلی ۱۲")], {"B1": ["beham", "Ashli", "NUM"]}))
    assert [o.status for o in result.outcomes] == ["verified", "unverified", "verified"] and script.calls == 0
    assert result.outcomes[1].reading == "ashli" and result.outcomes[1].format_fixes


def test_the_words_and_their_order_never_change(engine_for):
    result = asyncio.run(engine_for(Script("beham")).run([block("بهم گاراژ")], {"B1": ["baham", "gārāzh"]}))
    assert [o.surface for o in result.outcomes] == ["بهم", "گاراژ"] and len(result.readings()["B1"]) == 2
    assert result.readings()["B1"][0] == "beham"


def test_the_selection_budget_bounds_the_model_calls(engine_for, monkeypatch):
    from app.core.ai_config import ai_config

    monkeypatch.setattr(ai_config.reading, "selector_max_words", 0)
    script = Script("beham")
    out = asyncio.run(engine_for(script).run([block("بهم")], {"B1": ["baham"]})).outcomes[0]
    assert script.calls == 0 and out.status == "uncertain" and "budget" in out.reason


def test_the_ezafe_and_the_pause_of_the_writer_are_kept_on_a_resolved_reading():
    assert with_ezafe("gārāzh", "gārāj-e|") == "gārāzh-e|" and with_ezafe("khune", "khune-ye") == "khune-ye"


# --- the verified exceptions -----------------------------------------------------------------------------------------------------------

EXCEPTIONS = json.loads(Path("config/reading_exceptions.json").read_text(encoding="utf-8"))["exceptions"]


@pytest.mark.parametrize("exc", EXCEPTIONS, ids=lambda e: e["surface"])
def test_every_verified_exception_is_a_regression_the_engine_enforces(with_exceptions, tmp_path, exc):
    assert distinct(with_exceptions.options(exc["surface"])) == exc["right"]
    engine = ReadingEngine(Auditor(with_exceptions), Selector(None, 1, tmp_path / "s.json", None))
    for wrong in exc["wrong"]:
        out = asyncio.run(engine.run([block(exc["surface"])], {"B1": [wrong]})).outcomes[0]
        assert out.status == "resolved" and out.reading == exc["right"][0] and "verified exception" in out.reason
    right = asyncio.run(engine.run([block(exc["surface"])], {"B1": [exc["right"][0]]})).outcomes[0]
    assert right.status == "verified" and right.reading == exc["right"][0]


def test_the_lexicon_agrees_with_the_exceptions_it_can_check(morphology):
    checked = 0
    for exc in EXCEPTIONS:
        allowed = distinct(morphology.options(exc["surface"]))
        if allowed:
            checked += 1
            assert all(r in allowed for r in exc["right"]) and not any(w in allowed for w in exc["wrong"])
    assert checked >= 3


def test_an_exception_needs_its_source_and_a_right_reading():
    for exc in EXCEPTIONS:
        assert exc["confirmed"] and exc["right"] and Transcription().check(exc["right"][0]).status == "valid"


def test_a_reading_only_a_formal_romanisation_supports_does_not_replace_the_writers(engine_for):
    """خانه is in the evidence by a romanisation only: the sentence may favour it, but that is not enough to overrule how the writer says it."""
    out = asyncio.run(engine_for(Script("khāne")).run([block("خانه")], {"B1": ["khune"]})).outcomes[0]
    assert out.status == "uncertain" and out.reading == "khune" and "only romanization" in out.reason


def test_a_reading_with_two_independent_sources_may_replace_the_writers(engine_for):
    out = asyncio.run(engine_for(Script("gārāzh")).run([block("گاراژ")], {"B1": ["gārāj"]})).outcomes[0]
    assert out.status == "resolved" and out.reading == "gārāzh"


# --- a hyphen that keeps two letters apart is a sound, not a format -------------------------------------------------------------------

def test_a_hyphen_between_letters_that_would_read_as_one_symbol_is_kept():
    from app.documentary.reading.audit import compare_key
    tr = Transcription()
    for reading in ("sag-hā", "shelik-hā", "mas-hur"):
        checked = tr.check(reading)
        assert checked.normalized == reading and checked.status == "valid" and not checked.fixes
    assert tr.check("payām-hā").normalized == "payāmhā" and tr.check("shelik hā").normalized == "shelik-hā"
    assert compare_key("sag-hā") != compare_key("saghā") and compare_key("payām-hā") == compare_key("payāmhā")


def test_the_morphology_does_not_write_a_suffix_that_makes_a_digraph(morphology):
    fixture = Morphology(Lexicon({"سگ": [{"word": "سگ", "pos": "noun", "gloss": ["dog"], "colloquial": False, "form_of": "",
                                          "candidates": [{"latin": "sag", "kind": "romanization", "tags": []}]}]}), exceptions={})
    assert "sag-hā" in distinct(fixture.options("سگ‌ها")) and "saghā" not in distinct(fixture.options("سگ‌ها"))


def test_an_ezafe_written_as_yeh_is_a_reading_with_its_ezafe_hyphen(morphology):
    got = distinct(morphology.options("خانه‌ی"))
    assert "khāne-ye" in got and with_ezafe("khāne-ye", "khāne-ye") == "khāne-ye"


def test_a_reading_is_written_in_persian_letters_with_the_short_vowels_as_marks():
    from app.documentary.reading.compiler import to_persian
    assert to_persian("beham") == "بِهَم" and to_persian("ahd") == "اَهد" and to_persian("gurestun") == "گورِستون"
    assert to_persian("bebakhshadesh") == "بِبَخشَدِش" and to_persian("gārāzh-e|") == "گاراژ"
    with pytest.raises(ValueError):
        to_persian("bēham")


# --- risk and the rules of a block -----------------------------------------------------------------------------------------------------

def outcome(surface, reading, status="verified", **kw):
    from app.documentary.reading.engine import Outcome
    return Outcome("B1", kw.pop("n", 0), surface, kw.pop("writer", reading), reading, status, **kw)


def test_risk_comes_from_the_evidence_and_the_structure_of_a_word_not_from_a_list_of_faults():
    from app.documentary.reading.risk import analyze
    plain = analyze(outcome("خانه", "khāne"))
    assert plain.score == 0.0
    name = analyze(outcome("اشتراوس", "eshtrāvs", "unverified"))
    assert name.factors.keys() >= {"no_evidence", "consonant_cluster"} and name.score > 0.45
    assert analyze(outcome("پرت", "prat", "unverified")).score < 0.45          # a short name without a hard cluster is below the threshold: a known gap
    resolved = analyze(outcome("سراغ", "sorāgh", "resolved", writer="sarāgh"))
    assert "resolved_against_writer" in resolved.factors and resolved.score > 0.45
    assert analyze(outcome("۱۲", "NUM")).score == 0.0


def test_a_block_gets_rules_only_for_risky_words_in_the_chosen_strategy_and_none_leak():
    from app.documentary.reading import dictionary as D
    words = [outcome("خانه", "khāne", n=0), outcome("سراغ", "sorāgh", "resolved", writer="sarāgh", n=1)]
    text = "خانه سراغ"
    fing = D.build(words, text, "finglish_alias")
    assert fing.rules == [{"type": "alias", "string_to_replace": "سراغ", "alias": "sorāgh"}]
    assert D.build(words, text, "persian_diacritic_alias").rules[0]["alias"] == "سُراق"
    assert D.build(words, text, "none").rules == []
    assert D.build([outcome("سراغ", "sorāgh", "resolved", writer="sarāgh")], "کلمه دیگر", "finglish_alias").skipped[0]["why"].startswith("the written word is not")


def test_an_uncertain_reading_a_double_reading_and_an_overlap_are_never_sent():
    from app.documentary.reading import dictionary as D
    uncertain = outcome("موقع", "moghe", "uncertain", conflict=True)
    assert D.build([uncertain], "موقع", "finglish_alias").skipped[0]["why"].startswith("the engine does not stand behind")
    two = [outcome("سراغ", "sorāgh", "resolved", writer="a", n=0), outcome("سراغ", "surāgh", "resolved", writer="a", n=1)]
    assert D.build(two, "سراغ سراغ", "finglish_alias").skipped[0]["why"].startswith("the same word has different readings")
    nested = [outcome("سراغ", "sorāgh", "resolved", writer="a", n=0), outcome("سراغها", "soraghā", "resolved", writer="b", n=1)]
    got = D.build(nested, "سراغ سراغها", "finglish_alias")
    assert len(got.rules) == 1 and any("overlap" in s["why"] for s in got.skipped)
    capped = [outcome(f"کلمه{i}", f"sorāgh{i}", "resolved", writer="a", n=i) for i in "abcdefghij"]
    assert len(D.build(capped, " ".join(o.surface for o in capped), "finglish_alias").rules) == 6


def test_a_finglish_alias_keeps_the_ezafe_and_never_a_pause_or_a_joining_hyphen():
    from app.documentary.reading.dictionary import finglish
    assert finglish("ghāzi-ye|") == "ghāziye" and finglish("sag-hā") == "sag-hā" and finglish("payām-hā") == "payāmhā"


def test_a_hyphen_that_keeps_letters_apart_keeps_them_apart_in_persian_letters_too():
    from app.documentary.reading.compiler import to_persian
    assert to_persian("sag-hāye") == "سَگهایِ" and to_persian("saghā") == "سَقا"
