import asyncio

import pytest
from test_voice_render import FakeASR, FakeTTS, _block, _render

from app.core.ai_config import ai_config
from app.documentary.reading.scheme import to_ipa
from app.documentary.reading.voicing import Voicing
from app.documentary.voice_render import VoiceRenderer


@pytest.mark.parametrize("reading,ipa", [
    ("'ahd", "ʔæhd"), ("gurestun", "ɡuːɾestuːn"), ("prat", "pɾæt"), ("zhānin", "ʒɑːniːn"), ("khāne-ye", "xɑːneje"), ("dar-e", "dæɾe"),
    ("keylā", "kejlɑː"), ("bebakhshadesh|", "bebæxʃædeʃ"),
])
def test_a_reading_is_written_as_the_ipa_eleven_v4_takes(reading, ipa):
    assert to_ipa(reading) == ipa


def test_a_fixed_word_goes_to_the_voice_in_persian_letters_with_marks_and_every_other_word_stays_as_written():
    voicing = Voicing({"عهد": "'ahd"}, {"b": ["az", "'ahd", "harf", "zad"]})
    assert voicing.text("b", "[somber] از عَهْد، حرف زد.") == "[somber] از اَهد، حرف زد."


def test_the_ipa_form_is_still_available_for_a_voice_that_does_not_read_it_aloud(monkeypatch):
    monkeypatch.setattr(ai_config.reading, "voice_form", "ipa")
    voicing = Voicing({"عهد": "'ahd"}, {"b": ["az", "'ahd", "harf", "zad"]})
    assert voicing.text("b", "[somber] از عَهْد، حرف زد.") == "[somber] از /ʔæhd/، حرف زد."


def test_the_ezafe_of_the_blocks_reading_goes_with_the_fixed_word():
    voicing = Voicing({"عهد": "'ahd"}, {"b": ["'ahd-e", "ali"]})
    assert voicing.text("b", "«عهد» علی") == "«اَهدِ» علی"


def test_a_block_whose_words_are_not_the_words_of_its_reading_is_left_as_written(caplog):
    voicing = Voicing({"عهد": "'ahd"}, {"b": ["az", "'ahd"]})
    assert voicing.text("b", "از عهد حرف زد") == "از عهد حرف زد"
    assert "left as written" in caplog.text


def test_a_block_with_no_reading_and_a_number_are_left_as_written():
    voicing = Voicing({"۹۱۱": "NUM"}, {"b": ["NUM"]})
    assert voicing.text("b", "۹۱۱") == "۹۱۱" and voicing.text("other", "عهد") == "عهد"


def test_the_renderer_gives_the_voice_the_text_with_the_respelling(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    tts = FakeTTS()
    renderer = VoiceRenderer(provider=tts, asr=FakeASR())
    renderer.voicing = Voicing({"کتاب": "ketāb"}, {"EN_a_01": ["in", "ketāb", "bud"]})
    _render(renderer, [_block("EN_a_01", "act1", "این کتاب بود")])
    assert [r.text for r in tts.requests] == ["این کِتاب بود"]


def test_the_voicing_of_a_version_is_the_approvals_of_its_language_over_the_readings_of_its_blocks_except_for_a_ppie_language(db_session, monkeypatch):
    from app.db.models import ReadingApproval
    from app.documentary.reading import stage
    from app.documentary.reading.answer import ReadingWord
    from app.documentary.reading.text import ReadingResult

    db_session.add_all([ReadingApproval(language="fa", surface="عهد", kind="spoken_form", sense="", latin="'ahd", source="owner"),
                        ReadingApproval(language="en", surface="عهد", kind="spoken_form", sense="", latin="ohd", source="owner")])
    db_session.flush()
    read = ReadingResult({"b": [ReadingWord(0, "az", False, "", "high"), ReadingWord(1, "'ahd-e", False, "", "high")]}, 0)
    fresh = asyncio.run(stage.voicing_of(db_session, "fa", read, []))
    assert fresh.fixed == {} and fresh.text("b", "از عهد") == "از عهد", "a PPIE run applies no approval of an earlier run"
    monkeypatch.setattr(ai_config.ppie, "languages", [])
    voicing = asyncio.run(stage.voicing_of(db_session, "fa", read, []))
    assert voicing.text("b", "از عهد") == "از اَهدِ"


@pytest.mark.parametrize("reading,spelled", [
    ("'ahd", "اَهد"), ("'ahdhāyi", "اَهدهایی"), ("beham", "بِهَم"), ("gurestun", "گورِستون"), ("bebakhshadesh", "بِبَخشَدِش"), ("pat", "پَت"),
    ("ahd-e", "اَهدِ"), ("khāne-ye", "خانهٔ"), ("sedā-ye", "سِدای"), ("kheyli", "خِیلی"), ("nowbat", "نَوبَت"), ("sag-hā", "سَگها"), ("ghāzi|", "قازی"),
])
def test_a_reading_is_written_in_persian_letters_with_the_short_vowels_as_marks(reading, spelled):
    from app.documentary.reading.spelling import persian

    assert persian(reading) == spelled


def test_the_respellings_the_owner_heard_say_the_word_right_are_these_ones():
    """Heard 2026-11-03: اَهدهایی says ahd (plain text and the editor's marked عَهْد say ohd); بِهَم says beham (IPA between slashes, inline or in a
    dictionary rule, is read aloud as symbols)."""
    from app.documentary.reading.spelling import persian

    assert persian("'ahdhāyi") == "اَهدهایی" and persian("beham") == "بِهَم"


def test_a_symbol_the_scheme_has_not_is_refused_not_guessed():
    from app.documentary.reading.spelling import persian

    with pytest.raises(ValueError):
        persian("bēham")


def test_a_final_consonant_h_keeps_the_letter_the_word_is_written_with_and_heh_is_never_turned_into_hah():
    """The owner heard مُسَللَه (mosallah written with heh) said "mosallal": the respelling had replaced the word's own hah. The respelling keeps the word's
    own letter and converts nothing (no word has the evidence that a conversion helps)."""
    from app.documentary.reading.spelling import final_h_for, persian

    assert persian("mosallah") == "مُسَللَه" and persian("mosallah", final_h=True) == "مُسَللَح"
    assert final_h_for("مسلح") and final_h_for("سَلاح") and not final_h_for("دادگاه") and not final_h_for("شاه")
    assert persian("khāne") == "خانه" and persian("hāl") == "هال", "a final e keeps the heh; a first h stays heh"


def test_the_voice_is_given_the_words_own_final_letter_and_a_heh_is_never_turned_into_a_hah():
    voicing = Voicing({"مسلح": "mosallah", "شاه": "shāh"}, {"b": ["mosallah", "shāh"]})
    assert voicing.text("b", "مسلح شاه") == "مُسَلَّح شاه", "the word's own letters with a shadda: no letter added or replaced"


def test_an_approved_reading_keeps_the_words_letters_and_its_ezafe_belongs_to_the_sentence():
    assert Voicing.spell(Voicing.reading_of("pukkehā-ye", "pukkehā-ye", "پوکه‌های"), "پوکه‌های") == "پوکِّه‌هایِ", "no letter doubled, no half-space lost"
    assert Voicing.spell(Voicing.reading_of("fevriye-ye", "fevriye", "فوریه"), "فوریه") == "فِوریِه", "an approval's ezafe is not put where the sentence has none"
    assert Voicing.spell(Voicing.reading_of("'ahdhāye", "'ahdhā-ye", "عهدهای"), "عهدهای") == "اَهدهای", "an ezafe without its hyphen, and the alef the owner heard"
    assert Voicing.spell(Voicing.reading_of("pat", "pat", "پرت"), "پرت") == "پَت", "a spoken form the letters do not show is respelled"
