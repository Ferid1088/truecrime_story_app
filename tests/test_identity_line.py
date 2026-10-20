import re

import pytest

from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.documentary import host as H
from app.documentary import identity_line as IL

LANGUAGES = ("fa", "en", "de", "ar")
OWNER_FA = "من فریدونم و اینجا داستان‌های جنایی واقعی رو واست تعریف می‌کنم."


def test_the_four_approved_identity_lines_are_in_config():
    assert {lang: IL.identity_line(lang) for lang in LANGUAGES} == {
        "fa": OWNER_FA,
        "en": "I'm Fereidoun, and here I tell you real true crime stories.",
        "de": "Ich bin Fereidoun, und hier erzähle ich dir wahre Kriminalfälle.",
        "ar": "أنا فريدون، وهنا أروي لك قصص الجرائم الحقيقية."}


def test_persian_and_arabic_lines_are_in_their_own_script_only():
    for lang in ("fa", "ar"):
        assert not re.search(r"[A-Za-z]", IL.identity_line(lang)), lang


def test_an_unknown_language_has_no_identity_line():
    with pytest.raises(KeyError):
        IL.identity_line("xx")


def test_the_line_is_appended_once_and_can_be_taken_off_again():
    body = "One thing keeps me up at night. Why did nobody see him leave?"
    full = IL.with_identity(body, "en")
    assert full == f"{body} {IL.identity_line('en')}"
    assert IL.with_identity(full, "en") == full
    assert IL.ends_with_identity(full, "en") and not IL.ends_with_identity(body, "en")
    assert IL.strip_identity(full, "en") == body
    assert IL.strip_identity(body, "en") == body


def test_the_identity_line_is_the_last_thing_an_opening_says_and_only_an_opening():
    from app.documentary.host import segment_output

    story = type("S", (), {"acts": [], "act": lambda self, b: None})()
    seg = {"id": "H1", "position": "opening", "role": "opening", "beat_id": None, "cutoff": 0,
           "reason": "", "target_seconds": 20}
    written = {"dialogue": "Why did nobody see him leave? Think about it."}
    opening = segment_output(seg, written, "en", story, None, {})
    assert opening["avatar_dialogue"].endswith(IL.identity_line("en"))
    mid = segment_output({**seg, "position": "mid", "role": "transition"}, written, "en", story,
                         None, {})
    assert mid["avatar_dialogue"] == written["dialogue"]


def test_the_writer_is_never_asked_for_a_handover_line():
    for language in LANGUAGES:
        text = H.writer_system_prompt(language).lower()
        assert "hand over" not in text and "handing over" not in text, language
        assert "transition_back" not in text, language
        assert "identity line" in text, language
    assert "hand over" not in H.persona_prompt().lower()


def test_the_writer_prompt_is_the_same_for_all_languages_but_the_language_and_brand():
    from app.documentary.host import LANG_NAMES

    def neutral(language: str) -> str:
        text = H.writer_system_prompt(language)
        text = text.replace(ai_config.channels[language].name, "BRAND")
        for name in LANG_NAMES.values():
            text = text.replace(name, "LANG")
        return text

    assert len({neutral(lang) for lang in LANGUAGES}) == 1


HANDOVERS = {
    "en": ["Now let's go to the story.", "So, let's begin.", "The story begins."],
    "de": ["Fangen wir an.", "Los geht's.", "Die Geschichte beginnt."],
    "fa": ["بریم سراغ داستان.", "بذار شروع کنیم.", "داستان شروع می‌شه."],
    "ar": ["لنبدأ القصة.", "هيا بنا.", "تبدأ القصة الآن."]}


@pytest.mark.parametrize("language", LANGUAGES)
def test_an_opening_that_ends_with_a_handover_phrase_is_rejected(language):
    seg = {"id": "H1", "position": "opening", "role": "opening", "target_seconds": 20,
           "beat_id": None, "cutoff": 0}
    for phrase in HANDOVERS[language]:
        body = f"Das ist ein sehr langer Text über einen Fall und was er mit dir macht. {phrase}"
        types = [i["type"] for i in H.dialogue_issues(seg, body, language, [], [])]
        assert "handover_line" in types, (language, phrase)


@pytest.mark.parametrize("language", LANGUAGES)
def test_a_handover_phrase_in_the_middle_of_a_thought_is_not_the_last_line(language):
    seg = {"id": "H1", "position": "opening", "role": "opening", "target_seconds": 20,
           "beat_id": None, "cutoff": 0}
    body = f"{HANDOVERS[language][0]} Aber warum hat dich das nie losgelassen, sag mal?"
    types = [i["type"] for i in H.dialogue_issues(seg, body, language, [], [])]
    assert "handover_line" not in types


def test_the_writer_prompt_has_the_identity_line_rule():
    assert "identity line" in prompt("documentary/host/writer_system_prompt").lower()
