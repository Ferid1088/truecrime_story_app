"""The check and the trace of a block's pronunciation dictionary, with real text and real rules of Case 15 (copied from its takes and its dictionary)."""

import asyncio
import json

import pytest

from app.core.ai_config import ai_config
from app.documentary import pronunciation_trace as PT
from app.providers.voice.base import VoiceProviderError

# What a take of Case 15 was really sent with (block FA_B52_g02_01: the editor's vowel marks are in the text) and the film's rules.
B52 = ("بعد رو به پدر و مادر اشلی و بقیهٔ اقوامش کرد. از عَهْدهایی حرف زد که روز ازدواجش بسته بود. [somber] قبول کرد که به اون عَهْدها وفادار نمونده. "
       "گفت دربارهٔ خیلی چیزها دروغ گفته، از حرف‌های وحشتناکش دربارهٔ اشلی شرمنده‌ست و نتونسته عَهْدهای ازدواجش رو نگه داره.")
B55 = ("[concerned] متیو از غمی گفت که بعد از تموم شدن دادگاه هم تموم نشد. غمی که دیگه فقط به سالن دادگاه یا خونه‌ی کانینگهام کورت محدود نبود. "
       "[slowly] دخترها برای اینکه به مادرشون نزدیک بشن، می‌رفتن گورستون.")
B02 = "بعد کالب به اپراتور گفت که درِ گاراژ کاملاً بازِ بازه. می‌گفت یه نفر به زور اومده تو خونه. [slowly] اشلی توی تخت خواب بوده، و اون آدم بهش شلیک کرده."
B38 = "حالا سؤال اصلی این بود: دادستانی می‌تونه بدون پیدا کردن اسلحه‌ی واقعی قتل، هیئت منصفه رو به یقین قانونی برسونه؟"
RULES = [
    {"string_to_replace": "عهدهای", "type": "alias", "alias": "عَهدهای"},
    {"string_to_replace": "کانینگهام کورت", "type": "phoneme", "alphabet": "ipa", "phoneme": "kɑːniŋhɑːmkoːrt"},
    {"string_to_replace": "کانینگهام", "type": "phoneme", "alphabet": "ipa", "phoneme": "kɑːniŋhɑːm"},
    {"string_to_replace": "کورت", "type": "phoneme", "alphabet": "ipa", "phoneme": "kuːɾt"},
    {"string_to_replace": "درِ گاراژ", "type": "phoneme", "alphabet": "ipa", "phoneme": "dæɾegɑːɾɑːʒ"},
    {"string_to_replace": "درِ", "type": "phoneme", "alphabet": "ipa", "phoneme": "dære"},
    {"string_to_replace": "دَرِ", "type": "phoneme", "alphabet": "ipa", "phoneme": "dæɾe"},
    {"string_to_replace": "هیئت منصفه", "type": "phoneme", "alphabet": "ipa", "phoneme": "hejʔætemonsefe"},
    {"string_to_replace": "منصفه", "type": "alias", "alias": "مُنصِفِه"},
]


@pytest.fixture(autouse=True)
def rules_stay_as_made(monkeypatch):
    """The checks below are about matching; the conversion of IPA rules is tested on its own."""
    monkeypatch.setattr(ai_config.pronunciation_trace, "phoneme_as_alias", False)


def status_of(validation: PT.Validation) -> dict[str, str]:
    return {c.string: c.status for c in validation.checks if c.status != "absent"}


def test_a_rule_is_matched_only_as_a_whole_word_outside_audio_tags():
    rule = [{"string_to_replace": "گورستون", "type": "alias", "alias": "گورِستون"}]
    assert status_of(PT.validate("B", B55, rule)) == {"گورستون": "matched"}
    assert status_of(PT.validate("B", "او گورستونی دید", rule)) == {}                       # letters touch it: another word
    assert status_of(PT.validate("B", "[گورستون] او", rule)) == {}                           # inside an audio tag: not spoken text
    found = PT.validate("B", B55, rule).checks[0].occurrences[0]
    assert B55[found["start"]:found["end"]] == "گورستون"


def test_a_half_space_next_to_a_rule_is_matched_but_flagged_because_the_provider_does_not_say():
    rule = [{"string_to_replace": "رفتن", "type": "alias", "alias": "رَفتَن"}]
    check = PT.validate("B", B55, rule).checks[0]
    assert check.status == "matched" and check.boundary_uncertain


def test_the_real_case_15_text_of_a_block_is_checked_against_the_film_rules():
    got = status_of(PT.validate("FA_B55_g03_01", B55, RULES, B55))
    assert got["کانینگهام کورت"] == "matched" and got["کانینگهام"] == "shadowed" and got["کورت"] == "shadowed"
    garage = status_of(PT.validate("FA_B02_g01_01", B02, RULES, B02))
    assert garage["درِ گاراژ"] == "matched" and garage["درِ"] == "shadowed" and garage["دَرِ"] == "shadowed"
    jury = status_of(PT.validate("FA_B38_g02_01", B38, RULES, B38))
    assert jury == {"هیئت منصفه": "matched", "منصفه": "shadowed"}


def test_a_word_the_editor_gave_vowel_marks_after_the_dictionary_was_made_is_not_taken_over_by_the_rule_and_the_text_is_not_touched():
    plain = B52.replace("عَهْدهای", "عهدهای")
    check = PT.validate("FA_B52_g02_01", B52, RULES, plain)
    rule = next(c for c in check.checks if c.string == "عهدهای")
    assert rule.status == "unresolved" and "other vowel marks" in rule.reason and check.changed_after_dictionary
    assert [o["text"] for o in rule.occurrences] == ["عَهْدهای"]
    assert all(r["string_to_replace"] != "عَهْدهای" for r in check.effective), "the rule would replace the marks the editor wrote"
    assert "عَهْدهای" in B52, "the text keeps its word"


def test_a_rule_is_never_moved_onto_a_form_with_an_ezafe_mark_it_would_wipe_out():
    rule = [{"string_to_replace": "کنسول", "type": "alias", "alias": "کُنسول"}]
    got = PT.validate("B", "از کُنسولِ وانتش", rule)
    assert status_of(got) == {"کنسول": "unresolved"} and got.effective == []


def test_the_letters_of_the_other_alphabet_forms_are_the_same_word_but_a_mark_the_rule_has_is_a_sound():
    rule = [{"string_to_replace": "کنسول", "type": "alias", "alias": "کُنسول"}]
    assert status_of(PT.validate("B", "از كنسول وانتش", rule)) == {"کنسول": "reconciled"}          # Arabic kaf
    assert status_of(PT.validate("B", "از کنسول وانتش", rule)) == {"کنسول": "matched"}
    ezafe = [{"string_to_replace": "کنسولِ", "type": "phoneme", "alphabet": "ipa", "phoneme": "konsole"}]
    lost = PT.validate("B", "از کنسول وانتش", ezafe, "از کنسولِ وانتش")
    assert status_of(lost) == {"کنسولِ": "unresolved"} and lost.effective == [] and lost.checks[0].reason
    never_meant = PT.validate("B", "از کنسول وانتش", ezafe, "از میز وانتش")
    assert status_of(never_meant) == {}, "the word without the ezafe mark is not what the rule is for"


def test_a_rule_made_for_a_space_is_found_with_a_half_space_and_the_other_way_round():
    spaced = [{"string_to_replace": "می رفتن", "type": "alias", "alias": "می‌رَفتَن"}]
    assert status_of(PT.validate("B", "می‌رفتن گورستون", spaced)) == {"می رفتن": "reconciled"}


def test_malformed_duplicate_and_conflicting_rules_are_kept_out():
    rules = [
        {"string_to_replace": "کورت", "type": "phoneme", "alphabet": "ipa", "phoneme": "kuːɾt"},
        {"string_to_replace": "کورت", "type": "phoneme", "alphabet": "ipa", "phoneme": "kuːɾt"},
        {"string_to_replace": "منصفه", "type": "alias", "alias": "مُنصِفِه"},
        {"string_to_replace": "منصفه", "type": "alias", "alias": "مَنصَفِه"},
        {"string_to_replace": "", "type": "alias", "alias": "x"},
        {"string_to_replace": "قتل", "type": "phoneme", "phoneme": "ɢætl"},
        {"string_to_replace": "رأی", "type": "other", "alias": "x"},
    ]
    got = PT.validate("B", "کانینگهام کورت و هیئت منصفه و قتل", rules)
    assert [c.status for c in got.checks] == ["matched", "duplicate", "conflict", "conflict", "invalid", "invalid", "invalid"]
    assert got.effective == [rules[0]] and "alphabet" in got.checks[5].reason


def test_an_exact_rule_wins_over_a_reconciled_one_for_the_same_place_and_a_longer_rule_over_a_shorter():
    rules = [{"string_to_replace": "دَرِ", "type": "phoneme", "alphabet": "ipa", "phoneme": "dæɾe"},
             {"string_to_replace": "درِ", "type": "phoneme", "alphabet": "ipa", "phoneme": "dære"}]
    got = PT.validate("B", "درِ باغ", rules)
    assert status_of(got) == {"درِ": "matched", "دَرِ": "shadowed"}, "the rule with another mark is not moved onto the form that has its own rule"
    exact_and_reconciled = [{"string_to_replace": "قتل", "type": "alias", "alias": "قَتل"}, {"string_to_replace": "قتل", "type": "alias", "alias": "x"}]
    assert PT.validate("B", "قتل", exact_and_reconciled).checks[1].status == "conflict"
    longer = PT.validate("B", "کانینگهام کورت", RULES)
    assert [c.status for c in longer.checks if c.status != "absent"] == ["matched", "shadowed", "shadowed"]
    assert [r["string_to_replace"] for r in longer.effective] == ["کانینگهام کورت"]


def test_the_fingerprint_of_a_rule_set_does_not_depend_on_its_order():
    assert PT.fingerprint(RULES) == PT.fingerprint(list(reversed(RULES))) != PT.fingerprint(RULES[:-1])


def test_the_film_summary_counts_each_rule_once_over_all_blocks():
    texts = {"FA_B55": B55, "FA_B02": B02, "FA_B38": B38, "FA_B52": B52}
    vals = [PT.validate(b, t, RULES, t.replace("عَهْدهای", "عهدهای")) for b, t in texts.items()]
    summary = PT.film_summary(vals, RULES)
    assert summary["rules"] == len(RULES) and summary["blocks"] == 4
    by = dict(zip([r["string_to_replace"] for r in RULES], summary["per_rule"], strict=True))
    assert by["عهدهای"] == "unresolved" and by["کانینگهام کورت"] == "matched" and by["کورت"] == "shadowed" and by["دَرِ"] == "shadowed"
    assert summary["blocks_changed_after_dictionary"] == 1


# --- the dictionary of a block ---------------------------------------------------------------------------------------------------------------

class DictProvider:
    name = "dict_provider"

    def __init__(self, fail: list[str] | None = None):
        self.created: list[tuple[str, list[dict]]] = []
        self.fail = list(fail or [])

    async def create_pronunciation_dictionary(self, name, rules):
        await asyncio.sleep(0)
        if self.fail:
            raise VoiceProviderError(self.fail.pop(0), "boom")
        self.created.append((name, rules))
        return {"id": f"D{len(self.created)}", "version_id": f"V{len(self.created)}"}


FILM = [{"pronunciation_dictionary_id": "FILM", "version_id": "V0"}]


def manager(provider, tmp_path, mode="enforce", rules=None):
    return PT.BlockDictionaries(provider, RULES if rules is None else rules, FILM, language="fa", case_id=15, version_id=289, folder=tmp_path, mode=mode)


def test_in_trace_mode_the_request_keeps_the_film_dictionary_and_nothing_is_created(tmp_path):
    provider = DictProvider()
    got = asyncio.run(manager(provider, tmp_path, "trace").resolve("FA_B55", B55, B55))
    assert got.locators == FILM and got.mode == "trace" and provider.created == [] and got.validation.counts()["matched"] == 1


def test_in_enforce_mode_a_block_gets_a_dictionary_of_only_the_rules_that_act_on_its_text(tmp_path):
    provider = DictProvider()
    got = asyncio.run(manager(provider, tmp_path).resolve("FA_B55", B55, B55))
    assert got.locators == [{"pronunciation_dictionary_id": "D1", "version_id": "V1"}] and got.mode == "enforce"
    name, rules = provider.created[0]
    assert [r["string_to_replace"] for r in rules] == ["کانینگهام کورت"]
    assert name == f"truecrime-fa-case-15-v289-{PT.fingerprint(rules)}"
    none = asyncio.run(manager(provider, tmp_path).resolve("B", "هیچ قاعده‌ای اینجا نیست", None))
    assert none.locators == [] and len(provider.created) == 1


def test_blocks_that_run_at_the_same_time_with_the_same_rules_share_one_dictionary_and_different_rules_do_not_mix(tmp_path):
    provider = DictProvider()
    mgr = manager(provider, tmp_path)

    async def go():
        return await asyncio.gather(*(mgr.resolve(f"B{i}", B55, B55) for i in range(5)), mgr.resolve("X", B02, B02), mgr.resolve("Y", B38, B38))

    got = asyncio.run(go())
    assert len(provider.created) == 3
    assert len({json.dumps(g.locators) for g in got[:5]}) == 1, "five blocks with the same rules share one dictionary"
    assert got[5].locators != got[0].locators != got[6].locators != got[5].locators
    assert sorted(sorted(r["string_to_replace"] for r in rules) for _, rules in provider.created) == [["درِ گاراژ"], ["هیئت منصفه"], ["کانینگهام کورت"]]


def test_a_dictionary_is_remembered_between_runs_and_never_changed(tmp_path):
    first = DictProvider()
    asyncio.run(manager(first, tmp_path).resolve("B", B55, B55))
    second = DictProvider()
    again = asyncio.run(manager(second, tmp_path).resolve("B", B55, B55))
    assert second.created == [] and again.locators == [{"pronunciation_dictionary_id": "D1", "version_id": "V1"}]
    stored = json.loads((tmp_path / ai_config.pronunciation_trace.block_dictionaries_file).read_text(encoding="utf-8"))
    assert list(stored.values())[0]["rules"][0]["string_to_replace"] == "کانینگهام کورت"


def test_a_failing_provider_is_tried_again_and_a_hard_failure_falls_back_to_the_film_dictionary(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.pronunciation_trace, "retry_pause_s", 0.0)
    flaky = DictProvider(fail=["rate_limited", "timeout"])
    got = asyncio.run(manager(flaky, tmp_path).resolve("B", B55, B55))
    assert got.locators[0]["pronunciation_dictionary_id"] == "D1" and not got.notes
    broken = DictProvider(fail=["unauthorized"])
    fell = asyncio.run(manager(broken, tmp_path / "other").resolve("B", B55, B55))
    assert fell.locators == FILM and "block dictionary failed" in fell.notes[0]
    exhausted = DictProvider(fail=["timeout"] * 9)
    assert asyncio.run(manager(exhausted, tmp_path / "third").resolve("B", B55, B55)).locators == FILM


def test_a_plan_without_rules_cannot_be_checked_and_says_so_and_a_film_without_a_dictionary_is_left_alone(tmp_path):
    provider = DictProvider()
    unchecked = asyncio.run(manager(provider, tmp_path, rules=[]).resolve("B", B55, B55))
    assert unchecked.locators == FILM and unchecked.validation is None and "cannot be checked" in unchecked.notes[0]
    none = PT.BlockDictionaries(provider, RULES, [], language="fa", case_id=15, version_id=289, folder=tmp_path)
    assert asyncio.run(none.resolve("B", B55, B55)).locators == []


# --- the renderer: what every take was sent with is kept ---------------------------------------------------------------------------------

def test_every_take_is_traced_with_its_text_dictionary_voice_settings_and_request_id(tmp_path, monkeypatch):
    from test_voice_render import FakeTTS, _block

    from app.documentary.voice_render import VoiceRenderer

    class Tts(FakeTTS, DictProvider):
        def __init__(self):
            FakeTTS.__init__(self)
            DictProvider.__init__(self)

    monkeypatch.setattr(ai_config.voice, "prepared_languages", ["fa"])
    monkeypatch.setattr(ai_config.pronunciation_trace, "mode", "enforce")
    tts = Tts()
    renderer = VoiceRenderer(provider=tts, use_asr=False, use_listener=False)
    renderer.dictionaries, renderer.rules = list(FILM), list(RULES)
    renderer.block_dictionaries = PT.BlockDictionaries(tts, RULES, FILM, language="fa", case_id=15, version_id=289, folder=tmp_path)
    renderer.trace = PT.Trace(renderer.block_dictionaries)
    block = _block("FA_B55_g03_01", "B55", B55)
    result = asyncio.run(renderer.speak_block(block, "fa", ai_config.voice.for_language("fa"), {"stability": 0.2}, 1, tmp_path / "takes"))
    sent = tts.requests[0]
    assert sent.text == B55 and sent.pronunciation_dictionaries == [{"pronunciation_dictionary_id": "D1", "version_id": "V1"}]
    assert [r["string_to_replace"] for r in sent.pronunciation_rules] == ["کانینگهام کورت"]
    entry = renderer.trace.entries[0]
    assert entry["text"] == B55 and entry["request_id"] == result["take"]["request_id"] and entry["voice_id"] == sent.voice_id
    assert entry["settings"] == {"stability": 0.2} and entry["dictionary_locators"] == sent.pronunciation_dictionaries and entry["mode"] == "enforce"
    assert {r["string"]: r["status"] for r in entry["rules"]}["کانینگهام کورت"] == "matched"
    meta = json.loads(next((tmp_path / "takes").glob("*.json")).read_text(encoding="utf-8"))
    assert meta["dictionaries"] == sent.pronunciation_dictionaries and meta["rules_sent"] == sent.pronunciation_rules and meta["request_id"]
    saved = renderer.trace.save(tmp_path)
    assert saved["takes"] == 1 and json.loads((tmp_path / "pronunciation_trace.json").read_text(encoding="utf-8"))["film_rules"] == RULES


def test_in_trace_mode_the_cache_key_of_a_take_is_what_it_was_before(tmp_path, monkeypatch):
    from test_voice_render import FakeTTS, _block

    from app.documentary.voice_render import VoiceRenderer

    monkeypatch.setattr(ai_config.voice, "prepared_languages", ["fa"])
    monkeypatch.setattr(ai_config.pronunciation_trace, "mode", "trace")
    tts = FakeTTS()
    plain = VoiceRenderer(provider=tts, use_asr=False, use_listener=False)
    plain.dictionaries = list(FILM)
    traced = VoiceRenderer(provider=tts, use_asr=False, use_listener=False)
    traced.dictionaries, traced.rules = list(FILM), list(RULES)
    traced.block_dictionaries = PT.BlockDictionaries(tts, RULES, FILM, language="fa", case_id=15, version_id=289, folder=None)
    traced.trace = PT.Trace(traced.block_dictionaries)
    block = _block("FA_B55_g03_01", "B55", B55)
    one = asyncio.run(plain.speak_block(block, "fa", ai_config.voice.for_language("fa"), {}, 1, tmp_path / "a"))
    two = asyncio.run(traced.speak_block(block, "fa", ai_config.voice.for_language("fa"), {}, 1, tmp_path / "b"))
    assert one["take"]["cache_key"] == two["take"]["cache_key"] and tts.requests[0].pronunciation_dictionaries == tts.requests[1].pronunciation_dictionaries == FILM


def test_the_mode_is_one_of_the_two_and_a_persian_film_gets_a_dictionary_for_its_final_text():
    assert ai_config.pronunciation_trace.mode == "enforce"
    with pytest.raises(ValueError):
        ai_config.pronunciation_trace.__class__(mode="rewrite")


# --- Case 15 as it is on disk (skipped on a machine without it) ------------------------------------------------------------------------------

def test_the_real_case_15_takes_against_the_real_film_dictionary():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    folder = root / "data/cases/15/audio/fa/v289_prepared_reviewed"
    if not (folder / "manifest.json").exists():
        pytest.skip("Case 15 is not on this machine")
    rules = json.loads((root / "tests/data/case15_dictionary_rules.json").read_text(encoding="utf-8"))["rules"]
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    validations = []
    for tb in manifest["timeline"]["blocks"]:
        meta = json.loads((root / tb["wav"].replace(".wav", ".json")).read_text(encoding="utf-8"))
        assert meta["text"] and meta["request_id"] and meta["voice_id"] and meta["model_id"] and meta["settings"]
        validations.append(PT.validate(tb["block_id"], meta["text"], rules))
    summary = PT.film_summary(validations, rules)
    assert (summary["blocks"], summary["rules"]) == (236, 86)
    assert summary["by_status"]["invalid"] == summary["by_status"]["conflict"] == summary["by_status"]["duplicate"] == 0
    assert summary["by_status"]["matched"] + summary["by_status"]["reconciled"] + summary["by_status"]["shadowed"] + summary["by_status"]["unresolved"] == 86
    assert summary["by_status"]["matched"] >= 80


# --- IPA rules as aliases in Persian letters -------------------------------------------------------------------------------------------------

def test_an_ipa_rule_becomes_an_alias_in_persian_letters_so_the_voice_has_no_notation_to_read_aloud(monkeypatch):
    monkeypatch.setattr(ai_config.pronunciation_trace, "phoneme_as_alias", True)
    got, log = PT.aliases_for(RULES)
    by = {r["string_to_replace"]: r for r in got}
    assert by["هیئت منصفه"] == {"type": "alias", "string_to_replace": "هیئت منصفه", "alias": "هِیعَتِمُنسِفه"}
    assert by["درِ"]["alias"] == "دَره" and "phoneme" not in by["درِ"] and all(r["type"] == "alias" for r in got)
    assert {n["string"]: n["latin"] for n in log}["هیئت منصفه"] == "hey'atemonsefe"


def test_ipa_that_the_table_cannot_write_or_that_is_the_word_itself_is_left_out_with_the_reason():
    monkeypatch_rule = {"type": "phoneme", "alphabet": "ipa", "string_to_replace": "تاد", "phoneme": "tɑːd"}
    made, note = PT.as_alias(monkeypatch_rule)
    assert made is None and "already written" in note["left_out"]
    odd = {"type": "phoneme", "alphabet": "ipa", "string_to_replace": "کلمه", "phoneme": "kælɘme"}
    made, note = PT.as_alias(odd)
    assert made is None and "no letters" in note["left_out"]
    alias = {"type": "alias", "string_to_replace": "منصفه", "alias": "مُنصِفِه"}
    assert PT.as_alias(alias) == (alias, {})


def test_letters_that_would_read_as_one_symbol_are_kept_apart_when_an_ipa_is_written_in_letters():
    assert PT.ipa_to_latin("kɑːniŋhɑːm") == "kāning-hām"
    from app.documentary.reading.spelling import persian

    assert persian("kāning-hām") == "کانینگهام" and persian("miyāmiye") == "میامیه"


def test_in_enforce_mode_the_block_dictionary_has_only_alias_rules_and_the_conversion_is_in_the_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.pronunciation_trace, "phoneme_as_alias", True)
    provider = DictProvider()
    mgr = manager(provider, tmp_path)
    got = asyncio.run(mgr.resolve("FA_B02_g01_01", B02, B02))
    _, rules = provider.created[0]
    assert rules == [{"type": "alias", "string_to_replace": "درِ گاراژ", "alias": "دَرِگاراژ"}] and got.mode == "enforce"
    assert any(n["string"] == "درِ گاراژ" and n["alias"] == "دَرِگاراژ" for n in mgr.converted)
    assert "phoneme" not in json.dumps(rules)
