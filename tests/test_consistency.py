"""A word read the same way across a film: names by the current film table (the owner's approval included, inside a title + name too), free variation
and homographs allowed, a real disagreement reported and never written for the voice in its minority reading; and the form of a word's reference the
voice is given chosen by evidence, not by assuming that fewer marks are always better."""

from __future__ import annotations

from test_pronunciation_planner import MODEL, VOICE, entry_for, language  # noqa: F401  (language is a fixture)

from app.documentary.pronunciation_memory import PronunciationMemory
from app.documentary.pronunciation_planner import Planner
from app.documentary.reading import consistency as CS
from app.documentary.reading.table import FilmTable
from app.documentary.reading.voicing import Voicing, WordSignal
from app.documentary.reading.words import key_of, tokenize


def blocks(*pairs: tuple[str, list[str]]) -> dict[str, list]:
    return {f"b{i}": list(zip(tokenize(text), readings, strict=True)) for i, (text, readings) in enumerate(pairs)}


def test_notation_and_free_colloquial_variation_are_one_reading():
    assert CS.normal("ba'd") == CS.normal("bad") and CS.normal("dokhtarhā") == CS.normal("dokhtarā") and CS.normal("miyāmi") == CS.normal("miāmi")
    assert CS.normal("rowshan") == CS.normal("roshan") and CS.normal("jāy-e") == CS.normal("jā") and CS.normal("shāh") == "shāh"
    assert CS.normal("barresi") != CS.normal("barrasi"), "another vowel is another reading"


def test_a_name_read_two_ways_is_an_inconsistency_and_a_homograph_is_not():
    found = CS.variants(blocks(("کالب آمد", ["kāleb", "āmad"]), ("کالب رفت", ["kālb", "raft"]), ("او مرد", ["u", "mard"]), ("او مرد", ["u", "mord"])),
                        frozenset({key_of("کالب")}))
    by = {v.key: v for v in found}
    assert by[key_of("کالب")].kind == "name" and not by[key_of("کالب")].legitimate
    assert by[key_of("مرد")].legitimate, "a homograph: the sentence decides"
    assert CS.inconsistent(found) == {key_of("کالب"): ("kāleb", "kālb")}


def test_the_owners_approval_of_a_name_reaches_the_title_and_name_entry_with_its_ezafe():
    table = FilmTable.build({"people": [{"id": "p", "last": {"fa": "پرت", "latin": "part"}, "titles": [{"fa": "قاضی", "latin": "qāzi"}]}],
                             "places": [], "foreign": []}, [{"surface": "پرت", "latin": "pat", "kind": "person"}])
    assert table.fixed_readings(tokenize("قاضی پرت گفت")) == {0: "qāzi-ye", 1: "pat"}


def test_the_minority_reading_of_a_word_the_film_reads_two_ways_is_not_written_for_the_voice(db_session, language):  # noqa: F811
    doubt = WordSignal("low", True, False)
    variants = {key_of("بررسی"): ("barresi", "barrasi")}
    minority = Voicing({}, {"b": ["barrasi", "kard"]}, {"b": [doubt, WordSignal()]}, variants=variants)
    majority = Voicing({}, {"b": ["barresi", "kard"]}, {"b": [doubt, WordSignal()]}, variants=variants)
    low = Planner(db_session, language, VOICE, MODEL).plan("b", "بررسی کرد", minority)
    assert low.decisions[0].strategy == "escalate" and low.text == "بررسی کرد" and "reading_inconsistent" in {s.name for s in low.risks[0].signals}
    high = Planner(db_session, language, VOICE, MODEL).plan("b", "بررسی کرد", majority)
    assert high.decisions[0].strategy == "selective_marks"


def test_the_voice_form_is_chosen_by_evidence_and_both_forms_are_kept(db_session, language):  # noqa: F811
    signals = {"b": [WordSignal(), WordSignal(), WordSignal(), WordSignal("low", True, False)]}
    voicing = Voicing({}, {"b": ["u", "dar", "tasādof", "mord"]}, signals)
    first = Planner(db_session, language, VOICE, MODEL).plan("b", "او در تصادف مرد", voicing).decisions[3]
    assert first.candidates == {"minimal": "مُرد", "complete": "مُرْدْ"} and first.replacement == "مُرد" and "configured default" in first.basis
    entry = entry_for(db_session, language, "مرد")
    failed = PronunciationMemory(db_session).add_intervention(entry.id, kind="irab", exact_form="مرد", replacement="مُرد", voice_id=VOICE, model_id=MODEL)
    failed.failures = 2
    db_session.commit()
    second = Planner(db_session, language, VOICE, MODEL).plan("b", "او در تصادف مرد", voicing).decisions[3]
    assert second.replacement == "مُرْدْ" and "minimal form failed" in second.basis, "a form that failed is not given again while another has not"
