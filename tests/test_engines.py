"""The eight cinematic engines: each draws a readable sequence in all four languages, refuses data that is not
grounded in a stored fact, and takes part in the planner's offers with the repetition rules."""
import json

import pytest

from app.documentary.graphics import shots as GS
from app.documentary.graphics.base import GraphicUnavailable
from app.documentary.graphics.engines import kit
from app.documentary.graphics.registry import graphic
from app.documentary.mvp import alternatives as AL
from app.documentary.mvp import engines as EN

LANGUAGES = ("en", "de", "fa", "ar")


def it(text, basis="fact", fact="F001", **extra):
    return {"text": text, "basis": basis, "fact_id": fact, **extra}


SAMPLES = {
    "courtroom": {"issue": it("Who fired the shots?"),
                  "prosecution": [it("Flynn shot his wife twice in bed", "allegation")],
                  "defense": [it("No direct evidence ties him to the killing", "allegation", "F005")],
                  "evidence": it("About 100,000 text messages were read in court", "fact", "F012"),
                  "finding": it("The jury found him guilty after two hours", "finding", "F080")},
    "custody": {"item": {"name": "9mm handgun", "fact_id": "F002"},
                "stages": [{"stage": "discovery", **it("Missing from the truck console", "fact", "F002")},
                           {"stage": "collection", **it("Officers searched the property", "fact", "F003"),
                            "gap_after": {"text": "No custody record is in the file", "fact_id": "F009"}},
                           {"stage": "court", **it("Prosecutors told the jury it was never found", "allegation", "F004")}]},
    "unknowns": {"known": [it("Flynn called 911 on Feb. 16")],
                 "unknown": [it("Where the missing handgun is", "unknown", "Q2")],
                 "disputed": [it("Whether the daughters attended", "disputed", "C001")],
                 "span": {"start": it("The 911 call"), "end": None}},
    "forensic": {"exam": "ballistics", "sample": it("Two 9mm bullets recovered", "expert", "F002"),
                 "method": {"text": "Marks on a bullet are compared with test shots", "basis": "illustration"},
                 "result": it("Prosecutors said the bullets were 9mm", "allegation", "F002"),
                 "limits": it("No firearm was recovered to compare", "fact", "F005")},
    "interrogation": {"turns": [{"role": "question", "who": "Investigator", "mode": "paraphrase",
                                 **it("Asked where he was that night", "witness", "F003")},
                                {"role": "answer", "who": "Flynn", "mode": "paraphrase",
                                 **it("Said he slept on the couch", "witness", "F003")}],
                      "compare": {"earlier": {**it("A man brought a flag", "witness", "F011"), "when": "Feb. 16",
                                              "mode": "paraphrase"},
                                  "later": {**it("No visitor was identified", "fact", "F011"), "when": "Later",
                                            "mode": "paraphrase"},
                                  "record": it("The claim stayed unsupported", "disputed", "F011")}},
    "sentencing": {"charges": [{"charge": "Aggravated murder", "outcome": "Life, no parole", "fact_id": "F008",
                                "basis": "finding", "text": "Life, no parole"}],
                   "aggravating": [it("Used his faith to manipulate others", "finding", "F032")],
                   "final": it("Life without parole", "finding", "F008")},
    "investigation_clock": {"events": [
        {"track": "crime", "date": {"year": 2026, "month": 2, "day": 16}, **it("Ashley Flynn is shot", "fact", "T001")},
        {"track": "investigation", "date": {"year": 2026, "month": 2, "day": 16}, **it("911 call", "fact", "T001")},
        {"track": "investigation", "date": {"year": 2026, "month": 4, "day": 20}, **it("Flynn is charged", "fact", "T004")}]},
    "evidence_check": {"item": {"name": "Text messages", "fact_id": "F012"},
                       "establishes": [it("Flynn wrote of wanting her dead", "fact", "F014")],
                       "not_establishes": [it("They do not show who fired", "fact", "F005")],
                       "supported_by": [it("Motive described by prosecutors", "allegation", "F001")],
                       "assessment": it("The jury treated them as central", "finding", "F080")},
}


def drawn(kind, language="en", seconds=20.0, data=None):
    data = data or SAMPLES[kind]
    graphic_ = graphic(kind)
    n = graphic_.required_cues(data)
    cues = [round(f * seconds, 3) for f in GS.cue_fractions(kind, n)]
    return graphic_.render(data, seconds, cues, language)


@pytest.mark.parametrize("kind", sorted(SAMPLES))
@pytest.mark.parametrize("language", LANGUAGES)
def test_every_engine_draws_every_step_in_every_language(kind, language):
    rendered = drawn(kind, language)
    assert rendered.steps == graphic(kind).required_cues(SAMPLES[kind]) >= 2
    sizes = {rendered.state(n).size for n in range(rendered.steps + 1)}
    assert sizes == {(1920, 1080)}
    assert rendered.state(rendered.steps).tobytes() != rendered.state(0).tobytes()


@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_drawing_is_deterministic(kind):
    assert drawn(kind).key() == drawn(kind).key()
    assert drawn(kind).state(2).tobytes() == drawn(kind).state(2).tobytes()


@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_steps_are_spread_over_the_shot(kind):
    cues = GS.cue_fractions(kind, graphic(kind).required_cues(SAMPLES[kind]))
    assert cues == sorted(cues) and cues[0] < 0.2 and cues[-1] > 0.6


def test_engine_shots_are_held_one_and_a_half_times_as_long_as_the_detective_board():
    for steps in range(2, 8):
        assert 1.5 * 15.0 <= EN.engine_seconds("courtroom", steps) <= 1.5 * 24.0


def test_a_statement_without_its_fact_is_refused():
    data = json.loads(json.dumps(SAMPLES["courtroom"]))
    data["finding"].pop("fact_id")
    with pytest.raises(GraphicUnavailable, match="no fact id"):
        drawn("courtroom", data=data)


def test_a_finding_must_be_a_finding():
    data = json.loads(json.dumps(SAMPLES["courtroom"]))
    data["finding"]["basis"] = "allegation"
    assert "must be one of" in graphic("courtroom").can_render(data)


def test_an_illustration_is_only_allowed_as_the_forensic_method():
    data = json.loads(json.dumps(SAMPLES["courtroom"]))
    data["evidence"] = {"text": "A generic picture", "basis": "illustration"}
    assert graphic("courtroom").can_render(data)
    assert graphic("forensic").can_render(SAMPLES["forensic"]) is None


def test_a_sentencing_card_needs_more_than_one_line():
    data = json.loads(json.dumps(SAMPLES["sentencing"]))
    data.pop("aggravating")
    assert "more than one line" in graphic("sentencing").can_render(data)


def test_the_evidence_check_has_no_scores():
    data = json.loads(json.dumps(SAMPLES["evidence_check"]))
    data["assessment"]["text"] = "Credible at 80%"
    assert "percent" in graphic("evidence_check").can_render(data)


def test_clock_events_must_be_in_date_order():
    data = json.loads(json.dumps(SAMPLES["investigation_clock"]))
    data["events"].reverse()
    assert "date order" in graphic("investigation_clock").can_render(data)


def test_a_long_quiet_period_is_marked_as_one_without_documented_developments():
    from app.documentary.graphics.engines.story import _slots, _span

    slots = _slots(SAMPLES["investigation_clock"]["events"])
    gap = next(s for s in slots if s.get("gap"))
    assert gap["from"]["day"] == 16 and gap["to"]["day"] == 20 and gap["to"]["month"] == 4
    assert _span(gap["from"], gap["to"], "en") == "2 months"


REFS = {
    "F001": EN.Fact("F001", "Prosecutors alleged Flynn shot his wife twice in bed.", True, "legal"),
    "F005": EN.Fact("F005", "Defense attorneys argued no direct evidence such as a firearm ties him to the murder.",
                    True, "legal"),
    "F080": EN.Fact("F080", "The jury deliberated for just over two hours before finding Flynn guilty of nine charges.",
                    False, "legal"),
    "F003": EN.Fact("F003", 'Flynn told investigators "I slept on the couch" that night.', True, "investigation"),
    "F009": EN.Fact("F009", "Records of the weapon are missing from the file.", False, "evidence"),
    "T001": EN.Fact("T001", "Flynn called 911 on Feb. 16.", False, "timeline", "2026-02-16"),
    "T004": EN.Fact("T004", "Flynn was charged on April 20.", False, "timeline", "2026-04-20"),
    "Q2": EN.Fact("Q2", "Where was the handgun kept?", True, "question"),
}


def test_a_grounded_statement_passes():
    item = it("Flynn shot his wife twice", "allegation", "F001")
    assert EN.check_item(item, REFS, "x") is None


def test_a_new_figure_is_refused():
    item = it("Flynn shot his wife three times", "allegation", "F001")
    item["text"] = "Flynn shot his wife 3 times in bed"
    assert "figure" in EN.check_item(item, REFS, "x")


def test_a_disputed_fact_cannot_be_shown_as_fact():
    item = it("Flynn shot his wife twice in bed", "fact", "F001")
    assert "disputed" in EN.check_item(item, REFS, "x")


def test_a_finding_needs_a_court_in_the_fact():
    item = it("Flynn shot his wife twice in bed", "finding", "F001")
    refs = {"F001": EN.Fact("F001", "Flynn shot his wife twice in bed.", False, "general")}
    assert "court finding" in EN.check_item(item, refs, "x")


def test_a_verbatim_quotation_must_stand_in_the_record():
    ok = it("I slept on the couch", "witness", "F003", mode="verbatim")
    assert EN.check_item(ok, REFS, "x", verbatim=True) is None
    bad = it("I never left the couch", "witness", "F003", mode="verbatim")
    assert "word for word" in EN.check_item(bad, REFS, "x", verbatim=True)


def test_an_unsupported_statement_is_refused():
    item = it("Police found a knife in the garden", "allegation", "F001")
    assert "not supported" in EN.check_item(item, REFS, "x")


def test_clock_dates_come_from_the_timeline_not_the_agent():
    data = {"events": [{"track": "crime", "date": {"year": 1999}, **it("Flynn called 911", "fact", "T001")},
                       {"track": "investigation", **it("Flynn was charged", "fact", "T004")}]}
    clean, problems = EN.validate("investigation_clock", data, REFS)
    assert problems == [] and clean["events"][0]["date"] == {"year": 2026, "month": 2, "day": 16}


def test_a_custody_gap_needs_a_fact_that_says_so():
    data = json.loads(json.dumps(SAMPLES["custody"]))
    refs = {**REFS, "F002": EN.Fact("F002", "A 9mm handgun was missing from the truck console.", False, "evidence"),
            "F003": EN.Fact("F003", "Officers searched the property.", False, "evidence"),
            "F004": EN.Fact("F004", "Prosecutors told the jury the gun was never found.", True, "legal")}
    clean, problems = EN.validate("custody", data, refs)
    assert clean is not None, problems
    data["stages"][1]["gap_after"]["fact_id"] = "F003"
    assert any("gap" in p for p in EN.validate("custody", data, refs)[1])


def test_the_owner_can_switch_an_engine_off_and_edit_its_text():
    decisions = {"B01": EN.Decision("B01", "courtroom", data={}), "B02": EN.Decision("B02", "sentencing", data={})}
    kept = EN.apply_overrides(decisions, {"disabled": ["courtroom|B01"]}, REFS)
    assert list(kept) == ["B02"]


def offer_of(kind, text, beat, key=None):
    return AL.Offer(kind, key or f"{kind}|{beat}", beat, beat + 2, beat, {"text": text}, 20.0, 15.0)


def test_an_engine_is_not_repeated_within_four_beats_nor_with_the_same_story():
    state = AL.State(engine_last={"courtroom": 10})
    assert AL.repeats_engine(offer_of("courtroom", "x", 12), 12, state)
    assert not AL.repeats_engine(offer_of("courtroom", "completely different words here", 14), 14, state)
    state.engine_texts.append("the jury found him guilty after two hours of talks")
    assert AL.repeats_engine(offer_of("sentencing", "the jury found him guilty after two hours of talks", 30), 30, state)


def test_the_next_engine_prefers_another_kind_than_the_last_one():
    state = AL.State(engine_kind="courtroom")
    a, b = offer_of("courtroom", "a", 5), offer_of("sentencing", "b", 5)
    assert AL.priority(b, 5, state) < AL.priority(a, 5, state)


def test_engine_text_is_put_into_the_film_language_but_ids_and_dates_are_not():
    data = SAMPLES["investigation_clock"]
    texts = [h[k] for h, k in kit.texts_of(data)]
    assert "Ashley Flynn is shot" in texts and "T001" not in texts and all(isinstance(t, str) for t in texts)
    assert kit.fact_ids(data) == ["T001", "T004"]
