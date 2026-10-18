# ruff: noqa: F811  (`setup` is the flow tests' fixture, requested by setup)
"""docs/Story_Revol_fa.txt and docs/Story_Revol_de.txt: the native film versions (Persian, German).
Written twice (rewrite, final), inspected once, exactly three model calls, nothing more; held to
markers and length only; a report against the English master; an automatic approval record."""

import asyncio
import json
import string

import pytest
from test_language_pipeline_flow import ACTS, FakeGen, _pipeline, setup  # noqa: F401  (the flow tests' fixture)

from app.agents.story import realign_sections
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import StoryVersion
from app.language_pipeline import fact_report, script_hygiene
from app.providers.generation.base import GenerationError

PERSIAN = "آنها خانه را ترک کردند و هیچ‌کس ندید."
ARABIC = "غادروا البيت، ولم يرَ أحدٌ «شيئًا» غريبًا؛ فهل كانوا وحدهم؟"
ENGLISH = "They left the house, and nobody saw them go - not the neighbors, not the maid."
GERMAN = "Sie verließen das Haus, und niemand sah sie „gehen“ – schön, über Größe und Ärger."
LANGUAGES = {
    "en": {
        "sample": ENGLISH, "names": "added_and_missing", "rewrite_marker": "EDITORIAL AUTHORITY",
        "writer": "You are an experienced English documentary storyteller and structural editor",
        "inspector": "You are an English documentary editor evaluating a complete true-crime script",
        "final": "You are the final structural and prose editor of an English true-crime documentary",
        "guide": "LANGUAGE GUIDE: ENGLISH (en)"},
    "fa": {
        "rewrite_marker": "NATIVE RE-TELLING, NOT TRANSLATION", "sample": PERSIAN, "names": "none",
        "writer": "You are an experienced Persian true-crime documentary writer",
        "inspector": "You are a native Persian documentary editor",
        "final": "You are the final Persian story editor",
        "guide": "LANGUAGE GUIDE: PERSIAN TRUE-CRIME STORYTELLING"},
    "de": {
        "rewrite_marker": "NATIVE RE-TELLING, NOT TRANSLATION", "sample": GERMAN, "names": "missing",
        "writer": "You are an experienced German true-crime documentary writer",
        "inspector": "You are a native German documentary editor",
        "final": "You are the final German story editor",
        "guide": "LANGUAGE GUIDE: GERMAN TRUE-CRIME STORYTELLING"},
    "ar": {
        "rewrite_marker": "NATIVE RE-TELLING, NOT TRANSLATION", "sample": ARABIC, "names": "none",
        "writer": "You are an experienced Arabic true-crime documentary writer",
        "inspector": "You are a native Arabic documentary editor",
        "final": "You are the final Arabic story editor",
        "guide": "LANGUAGE GUIDE: ARABIC TRUE-CRIME STORYTELLING"},
}


@pytest.fixture(params=list(LANGUAGES))
def lang(request):
    return request.param


class NativeGen(FakeGen):
    """Film texts of a native language in act markers (right-to-left or umlauts next to the markers)."""

    def __init__(self, language):
        super().__init__()
        self.language = language
        self.sample = LANGUAGES[language]["sample"]

    def _result_native(self, role, words):
        per_act = max(words // (len(ACTS) * len(self.sample.split())), 1)
        text = "\n\n".join(f"[[ACT:{a}]]\n\n" + " ".join([self.sample] * per_act) for a in ACTS)
        return self._result(text, role)

    async def generate_text(self, role, system, user, images=None, **k):
        task = json.loads(user)
        if task.get("language") == self.language and role in ("language_rewrite_writer", "language_final_writer"):
            self.calls.append({"role": role, "system": system, "user": task, "raw": user, **k})
            return self._result_native(role, task["target_words_min"] + 12)
        return await super().generate_text(role, system, user, images, **k)

    async def generate_structured(self, role, system, user, images=None, **k):
        if role == "language_inspector" and json.loads(user)["language"] == self.language:
            self.calls.append({"role": role, "system": system, "user": json.loads(user), "raw": user, **k})
            return ({"verdict": "revise", "scores": {"native_naturalness": 80, "spoken_quality": 80,
                                                     "narrative_flow": 80, "engagement": 80},
                     "summary": "ok", "items": [{"id": 1, "severity": "medium", "kind": "flow", "act": "act1",
                                                 "quote": self.sample, "problem": "p", "instruction": "i"}]},
                    self._result("{}", role))
        return await super().generate_structured(role, system, user, images, **k)


def fail_length_for(monkeypatch, language):
    """The length gate fails for one language only (English is the base of the others and must pass)."""
    from app.language_pipeline import gates

    monkeypatch.setattr(gates.FinalGates, "_length", staticmethod(
        lambda result, text, span: result.fail("length", "short") if span.language == language else None))


def run(gen, case, languages=None):
    return asyncio.run(_pipeline(gen).run_case(case.id, list(languages or [gen.language]), 60))


def native_calls(gen):
    return [c for c in gen.calls if c["user"].get("language") == gen.language
            and c["role"] in ("language_rewrite_writer", "language_inspector", "language_final_writer")]


def final_row(db_session, out, language):
    db_session.expire_all()
    return db_session.get(StoryVersion, next(
        lg for lg in out["languages"] if lg["language"] == language)["final_version_id"])


# ----------------------------------------------------------------------------- exactly three calls
def test_a_language_is_written_twice_inspected_once_and_its_delivered_text_reviewed_in_that_order(setup, lang):
    gen = NativeGen(lang)
    out = run(gen, setup)
    spec = LANGUAGES[lang]
    assert out["languages"][0]["status"] == "done"
    assert [c["role"] for c in native_calls(gen)] == [
        "language_rewrite_writer", "language_inspector", "language_final_writer", "language_inspector"]
    systems = [c["system"] for c in native_calls(gen)]
    assert systems[0].startswith(spec["writer"]) and systems[1].startswith(spec["inspector"])
    assert systems[2].startswith(spec["final"]) and systems[3].startswith(spec["inspector"])


def test_a_gate_failure_makes_no_second_final_call_and_resume_makes_none_either(setup, lang, monkeypatch):
    fail_length_for(monkeypatch, lang)
    gen = NativeGen(lang)
    out = run(gen, setup)
    language = next(lg for lg in out["languages"] if lg["language"] == lang)
    assert language["status"] == "failed" and "blocking gates failed" in language["error"]
    assert [c["role"] for c in native_calls(gen)].count("language_final_writer") == 1
    again = NativeGen(lang)
    out = run(again, setup)
    assert out["languages"][0]["status"] == "failed" and native_calls(again) == []


def test_an_invalid_inspection_is_asked_for_once_more_and_a_second_failure_fails_the_stage(setup, lang):
    class Bad(NativeGen):
        async def generate_structured(self, role, system, user, images=None, **k):
            if role == "language_inspector" and json.loads(user)["language"] == self.language:
                self.calls.append({"role": role, "system": system, "user": json.loads(user), **k})
                return {"verdict": "maybe"}, self._result("{}", role)
            return await super().generate_structured(role, system, user, images, **k)

    gen = Bad(lang)
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "failed" and "not valid" in out["languages"][0]["error"]
    assert [c["role"] for c in native_calls(gen)] == [
        "language_rewrite_writer", "language_inspector", "language_inspector"]


def test_a_first_invalid_inspection_is_repeated_once_and_the_second_answer_is_used(setup, lang):
    class OneBad(NativeGen):
        bad = True

        async def generate_structured(self, role, system, user, images=None, **k):
            if role == "language_inspector" and json.loads(user)["language"] == self.language and self.bad:
                self.bad = False
                self.calls.append({"role": role, "system": system, "user": json.loads(user), **k})
                return {"verdict": "maybe"}, self._result("{}", role)
            return await super().generate_structured(role, system, user, images, **k)

    gen = OneBad(lang)
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "done"
    assert [c["role"] for c in native_calls(gen)] == [
        "language_rewrite_writer", "language_inspector", "language_inspector", "language_final_writer",
        "language_inspector"]


def test_the_selection_kind_is_allowed_and_an_unknown_kind_is_not():
    from pydantic import ValidationError

    from app.language_pipeline.film_schema import FilmItem

    item = {"id": 1, "severity": "low", "act": "act1", "quote": "", "problem": "p", "instruction": "i"}
    assert FilmItem.model_validate({**item, "kind": "selection"}).kind == "selection"
    with pytest.raises(ValidationError):
        FilmItem.model_validate({**item, "kind": "padding"})


def test_a_rewrite_without_markers_fails_after_one_call(setup, lang):
    class NoMarkers(NativeGen):
        async def generate_text(self, role, system, user, images=None, **k):
            if role == "language_rewrite_writer" and json.loads(user)["language"] == self.language:
                self.calls.append({"role": role, "system": system, "user": json.loads(user), **k})
                return self._result("no markers", role)
            return await super().generate_text(role, system, user, images, **k)

    gen = NoMarkers(lang)
    out = run(gen, setup)
    assert out["languages"][0]["status"] == "failed" and "one attempt, no retry" in out["languages"][0]["error"]
    assert len(native_calls(gen)) == 1


def test_a_cut_off_answer_fails_loudly_and_stores_no_text(setup, db_session, lang):
    class Truncated(NativeGen):
        async def generate_text(self, role, system, user, images=None, **k):
            if role == "language_final_writer" and json.loads(user)["language"] == self.language:
                raise GenerationError("invalid_output", "completion truncated at max_tokens")
            return await super().generate_text(role, system, user, images, **k)

    out = run(Truncated(lang), setup)
    assert out["languages"][0]["status"] == "failed" and "truncated" in out["languages"][0]["error"]
    db_session.expire_all()
    assert db_session.query(StoryVersion).filter_by(case_id=setup.id, kind="final", language=lang).count() == 0


def test_resume_after_a_failed_final_calls_only_the_final_and_bills_nothing_twice(setup, lang):
    class Dies(NativeGen):
        async def generate_text(self, role, system, user, images=None, **k):
            if role == "language_final_writer" and json.loads(user)["language"] == self.language:
                raise RuntimeError("provider down")
            return await super().generate_text(role, system, user, images, **k)

    assert run(Dies(lang), setup)["languages"][0]["status"] == "failed"
    again = NativeGen(lang)
    out = run(again, setup)
    assert out["languages"][0]["status"] == "done" and out["languages"][0]["reused"] == ["rewrite", "inspection"]
    assert [c["role"] for c in native_calls(again)] == ["language_final_writer", "language_inspector"]


# ----------------------------------------------------------------------------- inputs
def test_rewrite_gets_the_approved_english_text_and_the_plan_and_no_evidence(setup, lang):
    gen = NativeGen(lang)
    run(gen, setup)
    call = next(c for c in native_calls(gen) if c["role"] == "language_rewrite_writer")
    assert list(call["user"]) == ["language", "plan", "target_minutes_min", "target_minutes_max",
                                  "target_words_min", "target_words_max", "act_word_targets", "english_master"]
    assert (call["user"]["target_minutes_min"], call["user"]["target_minutes_max"]) == (60, 70)
    # the source is the approved English draft (English) or the approved English final (every other language)
    expected = "[[ACT:act1]]\n\ndword" if lang == "en" else "[[ACT:act1]]\n\nenxword"
    assert call["user"]["english_master"].startswith(expected)
    assert "[[ACT:act2]]" in call["user"]["english_master"]
    assert "evidence" not in call["raw"] and "supporting_text" not in call["raw"] and "evidence_ids" not in call["raw"]
    assert set(call["user"]["plan"]) <= {"title", "central_question", "reveal_map", "ending_strategy", "acts"}
    assert call["cache_key"]


def test_inspect_gets_only_the_text_the_plan_and_the_counts(setup, lang):
    gen = NativeGen(lang)
    run(gen, setup)
    call = next(c for c in native_calls(gen) if c["role"] == "language_inspector")
    assert list(call["user"]) == ["language", "plan", "word_count", "allowed_word_range", "story"]
    assert LANGUAGES[lang]["sample"] in call["user"]["story"]
    assert call["user"]["word_count"] == len(call["user"]["story"].split())
    for absent in ("english_master", "evidence", "supporting_text", "dword", "revised", "do_not_reveal", "key_beats"):
        assert absent not in call["raw"]
    assert set(call["user"]["plan"]) <= {"acts", "ending_strategy"}
    assert "READ LIKE A LISTENER" in call["system"]


def test_final_gets_the_master_as_a_reference_and_the_critique_of_the_inspector(setup, lang):
    gen = NativeGen(lang)
    run(gen, setup)
    final = next(c for c in native_calls(gen) if c["role"] == "language_final_writer")
    rewrite = next(c for c in native_calls(gen) if c["role"] == "language_rewrite_writer")
    assert list(final["user"]) == ["language", "plan", "target_words_min", "target_words_max",
                                   "act_word_targets", "english_master", "last_version",
                                   "last_version_word_counts", "critique"]
    assert final["user"]["act_word_targets"] == rewrite["user"]["act_word_targets"]
    assert set(final["user"]["last_version_word_counts"]) == set(ACTS)
    assert final["user"]["critique"]["items"][0]["kind"] == "flow"
    assert final["user"]["last_version"].startswith("[[ACT:act1]]") and LANGUAGES[lang]["sample"] in final["user"]["last_version"]
    assert final["user"]["english_master"] == rewrite["user"]["english_master"]
    assert "FINAL LISTEN" in final["system"] and "evidence" not in final["raw"]
    assert list(rewrite["user"])[:2] == list(final["user"])[:2] == ["language", "plan"]


def test_the_other_languages_do_not_use_these_agents(setup, lang):
    others = [lg for lg in LANGUAGES if lg != lang]
    gen = NativeGen(lang)
    run(gen, setup, (lang, *others))
    systems = {c["user"]["language"]: c["system"] for c in gen.calls if c["role"] == "language_rewrite_writer"}
    assert LANGUAGES[lang]["guide"] in systems[lang]
    for language in others:
        assert LANGUAGES[lang]["guide"] not in systems[language]
    assert len({systems[lg] for lg in systems}) == len(systems)


# ----------------------------------------------------------------------------- text and markers
def test_act_markers_survive_right_to_left_text_exactly():
    raw = "\n\n".join(f"[[ACT:{a}]]\n\n{PERSIAN} \u200c\u200f{PERSIAN}" for a in ACTS)
    sections, ok = realign_sections([{"id": a, "text": ""} for a in ACTS], raw, allow_paragraph_fallback=False)
    assert ok and [s["id"] for s in sections] == ACTS
    assert all(s["text"].startswith(PERSIAN) and "[[" not in s["text"] for s in sections)


def test_act_markers_survive_arabic_text_with_guillemets_and_arabic_punctuation_exactly():
    raw = "\n\n".join(f"[[ACT:{a}]]\n\n{ARABIC}\n\n«الباب»، قالت؛ هل رأيتَ؟" for a in ACTS)
    sections, ok = realign_sections([{"id": a, "text": ""} for a in ACTS], raw, allow_paragraph_fallback=False)
    assert ok and [s["id"] for s in sections] == ACTS
    assert all(s["text"].startswith(ARABIC) and "«الباب»" in s["text"] and "[[" not in s["text"] for s in sections)
    # the marker stands alone on its line, with no bidirectional control character next to it
    assert all(line == line.strip() and not set(line) & set(script_hygiene.BIDI_CONTROLS)
               for line in raw.split("\n") if line.startswith("[["))
    assert script_hygiene.check(raw)["text_beside_marker"]["count"] == 0


def test_act_markers_survive_german_text_with_umlauts_and_quotation_marks_exactly():
    raw = "\n\n".join(f"[[ACT:{a}]]\n\n{GERMAN}\n\n„Das Haus“, sagte sie, ‚war leer‘." for a in ACTS)
    sections, ok = realign_sections([{"id": a, "text": ""} for a in ACTS], raw, allow_paragraph_fallback=False)
    assert ok and [s["id"] for s in sections] == ACTS
    assert all(s["text"].startswith(GERMAN) and "„Das Haus“" in s["text"] and "[[" not in s["text"] for s in sections)


def test_the_final_is_stored_with_its_markers_as_a_new_version(setup, db_session, lang):
    out = run(NativeGen(lang), setup)
    final = final_row(db_session, out, lang)
    assert final.story_text.startswith("[[ACT:act1]]") and LANGUAGES[lang]["sample"] in final.story_text
    assert final.language == lang and final.kind == "final" and final.minutes and final.cost_eur > 0
    assert final.writer_prompt_name == f"language_pipeline/{lang}/final_writer" and final.writer_prompt_sha256
    assert final.reasoning_effort == "high"


# ----------------------------------------------------------------------------- gates and report
def test_a_native_language_is_blocked_by_markers_and_length_only(setup, db_session, lang):
    gen = NativeGen(lang)
    out = run(gen, setup, (lang, "en"))
    reports = {lg: set(json.loads(final_row(db_session, out, lg).critic_notes)["quality_gates"]["reports"])
               for lg in (lang, "en")}
    assert reports[lang] == reports["en"] == {"markers", "length"}
    assert ai_config.language_pipeline.blocking_gates_for(lang) == ["markers", "length"]


def test_the_numbers_report_normalises_digits_and_finds_an_added_and_a_missing_number():
    master = "Andrew Borden was found in 1892. The reward was $5,000 and the trial began in 1893."
    persian = "اندرو بوردن در سال ۱۸۹۲ پیدا شد. محاکمه در ۱۸۹۴ آغاز شد و پاداش ٥٠٠٠ دلار بود."   # Arabic-Indic 5000
    report = fact_report.compare(master, persian, _material(), "none")
    assert [f["figure"] for f in report["added"]["figures"]] == ["1894"]
    assert report["missing"]["figures"] == ["1893"]
    assert report["names_compared"] is False and report["added"]["names"] == [] and report["missing"]["names"] == []
    assert report["model_calls"] == 0 and report["blocking"] is False and "written out in words" in report["note"]


def _material(names=("Abby Borden",)):
    from app.language_pipeline.materials import SharedMaterial

    facts = [{"id": f"F{i:03d}", "claim": "c", "people": [n], "locations": []} for i, n in enumerate(names, 1)]
    return SharedMaterial(case_id=1, plan_id=1, plan={}, pack={"facts": facts}, draft_pack={"facts": facts},
                          fingerprint="x")


def test_the_german_report_finds_an_added_number_and_a_missing_name_and_reports_no_added_names():
    master = ("In 1892 Abby Borden died. Lizzie Borden stood trial in New Bedford. "
              "The estate was worth $300,000, about 2.5 million dollars today.")
    german = ("1892 starb Abby Borden. Lizzie stand in Hausen vor Gericht, nach 1893. "
              "Der Nachlass war 300.000 Dollar wert, rund 2,5 Millionen Dollar heute. Der Richter wirkte müde.")
    report = fact_report.compare(master, german, _material(), "missing")
    assert [f["figure"] for f in report["added"]["figures"]] == ["1893"]            # 300.000 and 2,5 matched
    assert "New Bedford" in report["missing"]["names"] and "Abby Borden" not in report["missing"]["names"]
    assert report["added"]["names"] == []                                           # added names are never reported
    assert report["names_mode"] == "missing" and report["model_calls"] == 0 and report["blocking"] is False
    assert "cannot be told reliably" in report["note"]


def test_the_report_is_stored_with_the_final_blocks_nothing_and_calls_no_model(setup, db_session, lang):
    out = run(NativeGen(lang), setup)
    report = json.loads(final_row(db_session, out, lang).critic_notes)["fact_report"]
    assert report["model_calls"] == 0 and report["blocking"] is False
    assert report["names_mode"] == LANGUAGES[lang]["names"] and out["languages"][0]["status"] == "done"
    from app.language_pipeline.state import case_state

    db_session.expire_all()
    block = next(b for b in case_state(db_session, setup.id)["languages"] if b["language"] == lang)
    assert block["fact_report"] == report and block["blocking_gates"] == ["markers", "length"]


# ----------------------------------------------------------------------------- automatic approval
def test_the_final_gets_an_automatic_approval_record_only_after_the_checks_of_the_delivered_text(setup, db_session, lang):
    out = run(NativeGen(lang), setup)
    final = final_row(db_session, out, lang)
    notes = json.loads(final.critic_notes)
    approval = notes["approval"]
    assert final.approved_by == "automatic" and final.approved_at is not None and final.status == "ready"
    assert approval["by"] == "automatic" and approval["at"]
    assert set(approval["stage_version_ids"]) == {"rewrite", "inspection", "final"}
    assert approval["stage_version_ids"]["final"] == final.id and approval["cost_eur"] > 0
    assert notes["checks"]["open_issues"] == []
    # the checks were made on exactly this version
    kinds = {r.kind for r in db_session.query(StoryVersion).filter_by(case_id=setup.id, language=lang)}
    assert "review" in kinds and (("fidelity" in kinds) == (lang != "en"))
    from app.db.models import BackgroundJob

    assert db_session.query(BackgroundJob).filter_by(case_id=setup.id).count() == 0


def test_a_final_whose_gates_failed_is_not_approved(setup, db_session, lang, monkeypatch):
    fail_length_for(monkeypatch, lang)
    run(NativeGen(lang), setup)
    db_session.expire_all()
    final = db_session.query(StoryVersion).filter_by(case_id=setup.id, kind="final", language=lang).one()
    assert final.status == "needs_revision" and final.approved_at is None


# ----------------------------------------------------------------------------- detection
def test_the_three_agents_exist_exactly_as_specified(lang):
    suffix = "_fa_ar" if lang in ("fa", "ar") else ""          # the Persian and Arabic writers have roles of their own
    for stage, role, output in (("rewrite", f"language_rewrite_writer{suffix}", "text"),
                                ("inspect", "language_inspector", "json"),
                                ("final", f"language_final_writer{suffix}", "text")):
        name = f"language.{stage}.{lang}"
        prompt_name = f"language_pipeline/{lang}/{'rewrite_writer' if stage == 'rewrite' else 'inspector' if stage == 'inspect' else 'final_writer'}"
        agent = ai_config.agents[name]
        assert (agent.role, agent.prompt, agent.output) == (role, prompt_name, output)
        assert prompt(prompt_name)
    assert ai_config.language_pipeline.language_agents[lang].model_dump() == {
        "rewrite": f"language.rewrite.{lang}", "inspect": f"language.inspect.{lang}",
        "final": f"language.final.{lang}",
        "length_correction": "language.length_correction.fa_ar" if lang in ("fa", "ar") else "language.length_correction",
        "fidelity": None if lang == "en" else "language.fidelity"}


def test_the_prompts_carry_their_detection_markers(lang):
    assert LANGUAGES[lang]["rewrite_marker"] in prompt(f"language_pipeline/{lang}/rewrite_writer")
    assert "READ LIKE A LISTENER" in prompt(f"language_pipeline/{lang}/inspector")
    assert "FINAL LISTEN" in prompt(f"language_pipeline/{lang}/final_writer")
    assert prompt(f"language_pipeline/guides/{lang}").startswith(LANGUAGES[lang]["guide"])


def test_the_prompts_format_without_a_missing_or_leftover_placeholder(lang):
    wanted = {"rewrite_writer": {"marker_instruction", "style_guide", "target_words_min", "target_words_max"},
              "inspector": {"style_guide"},
              "final_writer": {"marker_instruction", "style_guide", "target_words_min", "target_words_max"}}
    for file, names in wanted.items():
        text = prompt(f"language_pipeline/{lang}/{file}")
        assert {f for _, f, _, _ in string.Formatter().parse(text) if f} == names
        out = text.format(**{n: f"<{n}>" for n in names})
        assert all(f"<{n}>" in out for n in names) and "{{" not in out
    guide = prompt(f"language_pipeline/guides/{lang}")
    assert guide == guide.format()                                          # no braces: injected as a value


def test_the_native_inspector_output_lines_match_the_english_film_inspector(lang):
    mine, en = prompt(f"language_pipeline/{lang}/inspector"), prompt("language_pipeline/en/inspector")
    for key in ('{{"verdict":"pass"', "Allowed kinds:", '{{"id":1,"severity":"medium"'):
        assert next(line for line in mine.splitlines() if line.startswith(key)) == \
            next(line for line in en.splitlines() if line.startswith(key))
    assert "Do not reward excessive caution" in mine


def test_the_alignment_patch_is_in_every_writer_and_inspector_prompt():
    """The persian, german and arabic writers carry the owner's episode paragraph verbatim; the English
    writers (replaced by the editorial revision of 2026-10-18) say the same in their own words."""
    writer = ("Keep an episode because it contributes to the story; do not spend a paragraph explaining that an "
              "irrelevant episode is irrelevant.")
    for language in ("fa", "de", "ar"):
        for file in ("rewrite_writer", "final_writer"):
            text = prompt(f"language_pipeline/{language}/{file}")
            assert writer in text and text.index(writer) < text.index("{style_guide}")
    assert "must earn its space" in prompt("language_pipeline/en/rewrite_writer")
    assert "unnecessary digressions" in prompt("language_pipeline/en/final_writer")
    for language in ("en", "fa", "de", "ar"):
        assert "Do not reward excessive caution" in prompt(f"language_pipeline/{language}/inspector")


# ----------------------------------------------------------------------------- Arabic: script hygiene
def test_the_hygiene_report_counts_every_case_with_an_example_position_and_blocks_nothing():
    text = ("[[ACT:act1]]\n\n"
            "كتب الرجل \u06a9\u06cc رسالة\u0640 ثم ٣ رجال و 4 و \u06f5 \u200f word\n\n"
            "[[ACT:act2]] نص بجانب العلامة")
    report = script_hygiene.check(text)
    assert report["persian_letters"]["count"] == 2 and report["persian_letters"]["example"]["line"] == 3
    assert report["mixed_digit_styles"]["styles"] == ["western", "arabic_indic", "persian"]
    assert report["mixed_digit_styles"]["count"] == 3 and report["mixed_digit_styles"]["example"]["line"] == 3
    assert report["bidi_controls"]["count"] == 1 and report["tatweel"]["count"] == 1
    assert report["latin_letters"]["count"] == 1 and report["latin_letters"]["example"]["context"].count("word")
    assert report["text_beside_marker"]["count"] == 1 and report["text_beside_marker"]["example"]["line"] == 5
    assert report["blocking"] is False and report["model_calls"] == 0


def test_a_clean_text_has_no_hygiene_finding_and_a_marker_is_not_counted_as_latin_or_digits():
    report = script_hygiene.check(f"[[ACT:act1]]\n\n{ARABIC} ٣ رجال\n\n[[ACT:act2]]\n\n{ARABIC}")
    assert all(report[k]["count"] == 0 for k in (
        "persian_letters", "mixed_digit_styles", "bidi_controls", "tatweel", "latin_letters", "text_beside_marker"))


def test_the_arabic_report_has_the_hygiene_counts_and_the_other_languages_do_not(setup, db_session):
    out = run(NativeGen("ar"), setup, ("ar", "de"))
    ar = json.loads(final_row(db_session, out, "ar").critic_notes)["fact_report"]
    de = json.loads(final_row(db_session, out, "de").critic_notes)["fact_report"]
    assert set(ar["script_hygiene"]) >= {"persian_letters", "mixed_digit_styles", "bidi_controls", "tatweel",
                                         "latin_letters", "text_beside_marker"}
    assert ar["script_hygiene"]["blocking"] is False and out["languages"][0]["status"] == "done"
    assert "script_hygiene" not in de and ar["names_compared"] is False


def test_the_arabic_numbers_report_normalises_arabic_indic_and_persian_digits_and_separators():
    master = "The estate was worth $300,000 in 1892 and the trial began in 1893."
    arabic = "بلغت التركة ٣٠٠٬٠٠٠ دولار في ١٨٩٢ وبدأت المحاكمة في ۱۸۹٤."      # Persian-digit 189 + Arabic-Indic 4
    report = fact_report.compare(master, arabic, _material(), "none")
    assert "300000" not in [f["figure"] for f in report["added"]["figures"]]
    assert "1892" not in report["missing"]["figures"]
    assert report["missing"]["figures"] == ["1893"] and [f["figure"] for f in report["added"]["figures"]] == ["1894"]


# ----------------------------------------------------------------------------- every language has its own agents
def test_a_production_language_without_its_own_agents_or_stage_config_is_a_config_error():
    import copy

    import pytest as _pytest

    from app.core.ai_config import AIConfig, load_raw_config

    AIConfig.model_validate(load_raw_config())
    for table in ("language_agents", "language_stages"):
        raw = copy.deepcopy(load_raw_config())
        del raw["language_pipeline"][table]["ar"]
        with _pytest.raises(ValueError, match="exactly one entry per production language"):
            AIConfig.model_validate(raw)
    with _pytest.raises(KeyError, match="no entry for 'xx'"):
        ai_config.language_pipeline.agents_for("xx")


def test_the_generic_stage_agents_are_gone_and_nothing_names_them():
    for name in ("language.rewrite_writer", "language.inspector", "language.final_writer"):
        assert name not in ai_config.agents
    assert not hasattr(ai_config.language_pipeline, "generic_agents")
    from pathlib import Path

    for name in ("rewrite_writer", "inspector", "final_writer"):
        assert not Path(f"prompts/language_pipeline/{name}.md").exists()


# ----------------------------------------------------------------------------- A15: the four languages cannot drift apart
EPISODE = ("Keep an episode because it contributes to the story; do not spend a paragraph explaining that an "
           "irrelevant episode is irrelevant. Do not append a disclaimer to every clue, allegation, inheritance, "
           "later-life incident or witness account. When a passage feels weak, ask whether it belongs at all before "
           "polishing it. Do not force a solved case into an artificial mystery, and do not force an unresolved case "
           "to end on a generic question.")
CAUTION = ("Do not reward excessive caution and do not demand additional evidentiary explanations as a substitute for "
           "better narration. Flag a paragraph whose only job is to explain that something is irrelevant or unproven.")


@pytest.mark.parametrize("language", ["en", "fa", "de", "ar"])
def test_the_native_languages_share_the_storytelling_paragraphs_verbatim(language):
    """The English prompts were revised on 2026-10-18 and say it in their own words (see the alignment test)."""
    if language == "en":
        return
    for file in ("rewrite_writer", "final_writer"):
        assert EPISODE in prompt(f"language_pipeline/{language}/{file}"), (language, file)
    assert CAUTION in prompt(f"language_pipeline/{language}/inspector"), language


def test_the_four_inspectors_have_the_same_output_json_line_and_allowed_kinds_line():
    lines = {}
    for language in ("en", "fa", "de", "ar"):
        text = prompt(f"language_pipeline/{language}/inspector").splitlines()
        lines[language] = (next(x for x in text if x.startswith('{{"verdict":"pass"')),
                           next(x for x in text if x.startswith('{{"id":1,"severity":"medium"')),
                           next(x for x in text if x.startswith("Allowed kinds:")))
    assert len(set(lines.values())) == 1, lines


def test_the_state_tells_the_page_which_way_each_language_is_written(setup, db_session):
    from app.language_pipeline.state import case_state

    run(NativeGen("ar"), setup, ("ar", "en"))
    db_session.expire_all()
    blocks = {b["language"]: b for b in case_state(db_session, setup.id)["languages"]}
    assert (blocks["ar"]["direction"], blocks["fa"]["direction"]) == ("rtl", "rtl")
    assert (blocks["en"]["direction"], blocks["de"]["direction"]) == ("ltr", "ltr")


# ----------------------------------------------------------------------------- a remark before the first marker
def test_a_remark_before_the_first_marker_and_a_marker_in_mid_line_are_a_format_fault_not_a_failure():
    from app.language_pipeline.stages import normalize_markers

    text, lead = normalize_markers("I'm revising the narration against the plan.[[ACT:act1]]\n\nOne.\n\n[[ACT:act2]] Two.")
    assert lead == "I'm revising the narration against the plan."
    assert text == "[[ACT:act1]]\n\nOne.\n\n[[ACT:act2]]\n\nTwo."
    assert normalize_markers("Mid [[ACT:act1]] text.")[0] == "[[ACT:act1]]\n\ntext." and normalize_markers("no markers")[0] == "no markers"
    assert normalize_markers("[[ACT:act1]]\n\nclean\n\n[[ACT:act2]]\n\nclean") == ("[[ACT:act1]]\n\nclean\n\n[[ACT:act2]]\n\nclean", "")


def test_the_remark_is_dropped_from_the_stored_text_and_recorded_with_the_version(setup, db_session, lang):
    class Remarks(NativeGen):
        async def generate_text(self, role, system, user, images=None, **k):
            result = await super().generate_text(role, system, user, images, **k)
            if role == "language_final_writer" and json.loads(user)["language"] == self.language and \
                    "acts_to_expand" not in json.loads(user):
                result.text = "I will now revise the text." + result.text
            return result

    out = run(Remarks(lang), setup)
    final = final_row(db_session, out, lang)
    assert final.story_text.startswith("[[ACT:act1]]") and "I will now" not in final.story_text
    assert json.loads(final.critic_notes)["removed_lead_in"] == "I will now revise the text."
    assert out["languages"][0]["status"] == "done"
