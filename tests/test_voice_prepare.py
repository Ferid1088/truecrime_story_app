"""The narration of a prepared language is made right before it is voiced: the editor's text and rules, the preflight checks, the prepared
version, the film's dictionary, and a voice path that listens to nothing afterwards. No network, no model: every agent is a fake."""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
from uuid import uuid4

import pytest

from app.core.ai_config import ai_config
from app.db.models import Case, HostPlan, HostScene, HostSegments, StoryVersion
from app.documentary import voice_prepare as VP
from app.documentary.production.picture_rules import NameForms
from app.providers.voice import VoiceRequest

SECTIONS = [{"id": "B01", "text": "ساعت دو بامداد بود. کالب با ۹۱۱ تماس گرفت.\n\nدر گاراژ باز بود."},
            {"id": "B02", "text": "اشلی روی تخت بود."}]
EDITS = {0: {"final_text": "ساعت دو بامداد بود.", "pronunciation_rules": [], "uncertain_items": []},
         1: {"final_text": "کالب با ۹۱۱ تماس گرفت.", "pronunciation_rules": [{"surface": "کالب", "type": "phoneme", "pronunciation": "kɑːleb", "reason": "name"}],
             "uncertain_items": []},
         2: {"final_text": "درِ گاراژ باز بود.", "pronunciation_rules": [{"surface": "گاراژ", "type": "alias", "pronunciation": "گاراج", "reason": "loanword"}],
             "uncertain_items": []},
         3: {"final_text": "اشلی روی تخت بود.", "pronunciation_rules": [], "uncertain_items": []}}


def sentence(i, draft, final, rules=None, beat="B01"):
    return VP.Sentence(i, beat, 0, draft, final, rules or [])


# ---------------------------------------------------------------------------
# the text and the preflight checks
# ---------------------------------------------------------------------------


def test_numbers_are_read_across_persian_and_ascii_digits_and_a_rule_matches_whole_words_only():
    assert VP.numbers_of("۹۱۱ و 12 و ٣٠") == ["911", "12", "30"]
    assert VP.whole_word("در").search("در خانه") and not VP.whole_word("در").search("دربار") and not VP.whole_word("در").search("پدر")
    assert not VP.whole_word("خانه").search("خانه‌اش"), "the half-space joins words"
    assert VP.normalize("  سلام​   دنیا ‌ ") == "سلام دنیا ‌".strip()


def test_a_final_text_that_loses_a_number_or_a_name_or_is_empty_or_far_from_the_draft_is_an_issue():
    names = NameForms({"caleb_flynn": {"full": ["کالب فلین"], "first": ["کالب"], "last": ["فلین"]}})
    ok = sentence(0, "کالب با ۹۱۱ تماس گرفت.", "کالب با ۹۱۱ تماس گرفت.")
    assert VP.check_sentence(ok, names) == []
    assert "number 911" in VP.check_sentence(sentence(0, "کالب با ۹۱۱ تماس گرفت.", "کالب تماس گرفت و گفت یه چیزی.", None), names)[0]["what"]
    lost_name = VP.check_sentence(sentence(0, "کالب با پلیس حرف زد.", "او با پلیس حرف زد.", None), names)
    assert any("caleb_flynn" in i["what"] for i in lost_name)
    assert VP.check_sentence(sentence(0, "یک جمله.", "  "), names)[0]["what"] == "the final text is empty"
    too_long = VP.check_sentence(sentence(0, "کوتاه بود.", " ".join(["کلمه"] * 12)), names)
    assert any("times as long" in i["what"] for i in too_long)


def test_a_rule_must_belong_to_a_word_of_the_text_not_match_part_of_another_word_and_a_phoneme_must_be_ipa():
    good = {"surface": "کالب", "type": "phoneme", "pronunciation": "kɑːleb", "reason": ""}
    assert VP.check_sentence(sentence(0, "کالب آمد.", "کالب آمد.", [good])) == []
    absent = VP.check_sentence(sentence(0, "او آمد.", "او آمد.", [good]))
    assert "belongs to no word" in absent[0]["what"]
    inside = VP.check_sentence(sentence(0, "کالبد او", "کالبد و کالب", [good]))
    assert any("part of another word" in i["what"] for i in inside)
    persian_phoneme = {**good, "pronunciation": "کالب"}
    assert any("not an IPA reading" in i["what"] for i in VP.check_sentence(sentence(0, "کالب آمد.", "کالب آمد.", [persian_phoneme])))


def test_the_films_rules_become_one_dictionary_and_a_word_with_two_readings_is_a_conflict():
    a = sentence(0, "x", "کالب", [{"surface": "کالب", "type": "phoneme", "pronunciation": "kɑːleb", "reason": ""}])
    b = sentence(1, "x", "کالب", [{"surface": "کالب", "type": "phoneme", "pronunciation": "kɑːleb", "reason": ""},
                                   {"surface": "گاراژ", "type": "alias", "pronunciation": "گاراج", "reason": ""}])
    rules, conflicts = VP.dictionary_rules([a, b])
    assert conflicts == [] and rules == [{"string_to_replace": "کالب", "type": "phoneme", "alphabet": "ipa", "phoneme": "kɑːleb"},
                                          {"string_to_replace": "گاراژ", "type": "alias", "alias": "گاراج"}]
    c = sentence(2, "x", "کالب", [{"surface": "کالب", "type": "phoneme", "pronunciation": "mɒnstər", "reason": ""}])
    assert "two readings" in VP.dictionary_rules([a, c])[1][0]["what"], "clearly different readings of one word cannot be one rule"


def test_a_name_given_almost_the_same_reading_in_different_sentences_has_one_reading_the_most_frequent_and_an_approved_reading_wins(monkeypatch):
    def rule(pron):
        return [{"surface": "کالب", "type": "phoneme", "pronunciation": pron, "reason": ""}]

    sentences = [sentence(0, "x", "کالب", rule("kɑːleb")), sentence(1, "x", "کالب", rule("kɑːleb")), sentence(2, "x", "کالب", rule("kɑleb"))]
    rules, conflicts = VP.dictionary_rules(sentences)
    assert conflicts == [] and rules == [{"string_to_replace": "کالب", "type": "phoneme", "alphabet": "ipa", "phoneme": "kɑːleb"}]
    monkeypatch.setattr(ai_config.voice_prepare, "approved_readings", {"fa": {"کالب": {"type": "alias", "pronunciation": "کِیلِب"}}})
    quiet = [sentence(0, "x", "کالب آمد", rule("kɑːleb")), sentence(1, "x", "او آمد")]
    VP.apply_approved(quiet, "fa")
    assert quiet[0].rules == [{"surface": "کالب", "type": "alias", "pronunciation": "کِیلِب", "reason": "approved by the owner"}] and quiet[1].rules == []
    assert VP.dictionary_rules(quiet)[0] == [{"string_to_replace": "کالب", "type": "alias", "alias": "کِیلِب"}]


def test_an_editor_answer_that_misses_sentences_or_uses_another_key_is_asked_again_and_read_tolerantly(db_session, monkeypatch):
    from types import SimpleNamespace

    case, old = _version(db_session)
    answers = [{"sentences": [{"i": 0, "text": "ساعت دو بامداد بود."}]}, {"sentences": [{"i": 1, "final_text": "کالب با ۹۱۱ تماس گرفت."}]}]
    asked: list[int] = []

    async def run_agent(name, gen, body):
        asked.append(len(json.loads(body.split("INPUT:\n", 1)[1])["beats"][0]["sentences"]))
        return answers.pop(0), SimpleNamespace(model="m")

    monkeypatch.setattr(VP, "run_agent", run_agent)
    monkeypatch.setattr(VP, "get_generation_provider", lambda: object())
    monkeypatch.setattr(VP, "stamp_run", lambda *a: None)
    sentences = [VP.Sentence(0, "B01", 0, "ساعت دو بامداد بود."), VP.Sentence(1, "B01", 0, "کالب با ۹۱۱ تماس گرفت.")]
    asyncio.run(VP.edit(db_session, case, "fa", sentences, {"B01": "x"}, {}))
    assert [s.final for s in sentences] == ["ساعت دو بامداد بود.", "کالب با ۹۱۱ تماس گرفت."] and asked == [2, 2]


def test_the_sections_of_the_prepared_version_keep_the_beats_and_paragraphs_of_the_old_one():
    sentences = [VP.Sentence(0, "B01", 0, "a", "A"), VP.Sentence(1, "B01", 0, "b", "B"), VP.Sentence(2, "B01", 1, "c", "C"), VP.Sentence(3, "B02", 0, "d", "")]
    assert VP.assemble(sentences) == [{"id": "B01", "text": "A B\n\nC"}, {"id": "B02", "text": "d"}]


# ---------------------------------------------------------------------------
# the prepared version
# ---------------------------------------------------------------------------


def _version(db):
    case = Case(canonical_title="Prepare", slug=f"vp-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    master = StoryVersion(case_id=case.id, version=1, kind="final", language="fa", story_text="t", narrative_angle="a", status="ready")
    db.add(master)
    db.commit()
    text = "\n\n".join(s["text"] for s in SECTIONS)
    old = StoryVersion(case_id=case.id, version=2, kind="spoken", language="fa", story_text=text, narrative_angle="a", status="ready",
                       master_version_id=master.id,
                       narrative_structure=json.dumps({"sections": SECTIONS, "blueprint_id": 5, "source_version_id": master.id, "beat_sections": True}))
    db.add(old)
    db.commit()
    return case, old


@pytest.fixture(autouse=True)
def work_dir(tmp_path, monkeypatch):
    """The editor's answers are cached on disk: in a test they go to a temporary folder, never to the real data."""
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))


@pytest.fixture
def fakes(monkeypatch):
    calls: list[str] = []

    async def ask(db, case_id, label, payload, role, runner):
        calls.append(role)
        if role == "voice_prepare_editor":
            return {"sentences": [{"i": s["i"], **EDITS[s["i"]]} for beat in payload["beats"] for s in beat["sentences"]]}
        return {"beats": [{"beat_id": b["beat_id"], "ok": True, "problems": []} for b in payload["beats"]]}

    monkeypatch.setattr(VP, "_ask", ask)
    monkeypatch.setattr(VP, "english_sources", lambda db, version: {"B01": "At 2 a.m. Caleb called 911. The garage door was open.", "B02": "Ashley was in bed."})
    monkeypatch.setattr(VP, "tones_of", lambda db, version: {})
    return calls


def test_the_prepared_version_is_a_new_version_with_the_final_text_and_the_rules_and_the_old_one_is_untouched(db_session, fakes):
    case, old = _version(db_session)
    old_text = old.story_text
    new = asyncio.run(VP.prepare_version(db_session, case, old, "fa"))
    assert new.id != old.id and new.kind == "spoken" and new.language == "fa" and new.master_version_id == old.master_version_id
    assert old.story_text == old_text and not VP.is_prepared(old), "v270 is never overwritten"
    assert VP.is_prepared(new)
    assert new.story_text == "ساعت دو بامداد بود. کالب با ۹۱۱ تماس گرفت.\n\nدرِ گاراژ باز بود.\n\nاشلی روی تخت بود."
    struct = json.loads(new.narrative_structure)
    assert [s["id"] for s in struct["sections"]] == ["B01", "B02"] and struct["blueprint_id"] == 5
    assert [r["string_to_replace"] for r in struct["prepared"]["rules"]] == ["کالب", "گاراژ"] and struct["prepared"]["from_version_id"] == old.id
    assert fakes == ["voice_prepare_editor", "voice_prepare_checker"], "one edit call and one check call, nothing else"
    assert asyncio.run(VP.prepare_version(db_session, case, new, "fa")) is new, "a prepared version is not prepared again"


def test_an_uncertain_word_a_failed_preflight_or_a_checker_problem_stops_before_anything_is_made(db_session, fakes, monkeypatch):
    case, old = _version(db_session)
    before = db_session.query(StoryVersion).count()
    uncertain = {**EDITS, 1: {**EDITS[1], "uncertain_items": [{"surface": "ملک", "question": "melk or molk?"}]}}

    async def with_uncertain(db, case_id, label, payload, role, runner):
        if role == "voice_prepare_editor":
            return {"sentences": [{"i": s["i"], **uncertain[s["i"]]} for beat in payload["beats"] for s in beat["sentences"]]}
        return {"beats": [{"beat_id": b["beat_id"], "ok": True, "problems": []} for b in payload["beats"]]}

    monkeypatch.setattr(VP, "_ask", with_uncertain)
    with pytest.raises(VP.PreparationUncertain) as stopped:
        asyncio.run(VP.prepare_version(db_session, case, old, "fa"))
    assert "melk or molk?" in str(stopped.value) and stopped.value.items

    async def checker_objects(db, case_id, label, payload, role, runner):
        if role == "voice_prepare_editor":
            return {"sentences": [{"i": s["i"], **EDITS[s["i"]]} for beat in payload["beats"] for s in beat["sentences"]]}
        return {"beats": [{"beat_id": "B01", "ok": False, "problems": [{"kind": "number", "quote": "۹۱۱", "why": "the source says 911 twice"}]},
                          {"beat_id": "B02", "ok": True, "problems": []}]}

    shutil.rmtree(ai_config.voice.work_dir, ignore_errors=True)       # the editor's earlier answer is not read again
    monkeypatch.setattr(VP, "_ask", checker_objects)
    with pytest.raises(VP.PreparationUncertain, match="number"):
        asyncio.run(VP.prepare_version(db_session, case, old, "fa"))
    assert db_session.query(StoryVersion).count() == before, "no version is made when anything needs a person"


def test_without_an_english_source_nothing_is_prepared(db_session, fakes, monkeypatch):
    case, old = _version(db_session)
    monkeypatch.setattr(VP, "english_sources", lambda db, version: {})
    with pytest.raises(VP.PreparationUncertain, match="no English source"):
        asyncio.run(VP.prepare_version(db_session, case, old, "fa"))
    assert fakes == []


def test_the_host_of_the_old_version_is_the_host_of_the_prepared_one_nothing_of_it_is_made_again(db_session, fakes):
    case, old = _version(db_session)
    plan = HostPlan(case_id=case.id, version=1, status="valid", plan_json="{}", validation_json="{}")
    db_session.add(plan)
    db_session.commit()
    segments = HostSegments(case_id=case.id, story_version_id=old.id, host_plan_id=plan.id, language="fa", version=1, status="valid",
                            segments_json='[{"segment_id":"S1"}]', validation_json="{}")
    db_session.add(segments)
    db_session.commit()
    db_session.add(HostScene(case_id=case.id, story_version_id=old.id, host_segments_id=segments.id, host_segment_id="S1", language="fa", channel="fa",
                             position="opening", text="سلام", text_sha256="x", status="rendered", studio_profile_id="STUDIO_x", studio_asset_id="ASSET_x", framing_preset="MEDIUM", avatar_video_path="data/avatar.mp4",
                             voice_path="data/voice.mp3", planned_start=0.0, planned_duration=5.0))
    db_session.commit()
    new = asyncio.run(VP.prepare_version(db_session, case, old, "fa"))
    copy = db_session.query(HostSegments).filter_by(story_version_id=new.id).one()
    assert copy.id != segments.id and copy.segments_json == segments.segments_json and copy.status == "valid"
    scene = db_session.query(HostScene).filter_by(host_segments_id=copy.id).one()
    assert scene.story_version_id == new.id and scene.avatar_video_path == "data/avatar.mp4" and scene.voice_path == "data/voice.mp3"
    assert db_session.query(HostScene).filter_by(host_segments_id=segments.id).count() == 1, "the old scenes stay"


def test_the_films_dictionary_is_made_once_for_its_rules_and_reused(db_session, fakes):
    case, old = _version(db_session)
    new = asyncio.run(VP.prepare_version(db_session, case, old, "fa"))
    made: list[tuple[str, list]] = []

    class Provider:
        async def create_pronunciation_dictionary(self, name, rules):
            made.append((name, rules))
            return {"id": "D1", "version_id": "V1"}

    assert asyncio.run(VP.ensure_dictionary(db_session, old, "fa", Provider())) == [], "a version without rules needs none"
    locators = asyncio.run(VP.ensure_dictionary(db_session, new, "fa", Provider()))
    assert locators == [{"pronunciation_dictionary_id": "D1", "version_id": "V1"}] and len(made) == 1
    assert made[0][0] == f"truecrime-fa-case-{case.id}-v{new.id}" and len(made[0][1]) == 2
    assert asyncio.run(VP.ensure_dictionary(db_session, new, "fa", Provider())) == locators and len(made) == 1, "the same rules reuse the dictionary"


# ---------------------------------------------------------------------------
# the request and the voice path
# ---------------------------------------------------------------------------


def test_the_request_carries_the_context_and_the_dictionary_for_a_prepared_language_and_english_is_unchanged(monkeypatch):
    from app.providers.voice.elevenlabs import ElevenLabsVoiceProvider

    provider = ElevenLabsVoiceProvider.__new__(ElevenLabsVoiceProvider)
    provider.cfg = ai_config.voice.elevenlabs
    locators = [{"pronunciation_dictionary_id": "D1", "version_id": "V1"}]
    fa = provider._body(VoiceRequest(text="x", voice_id="V", model_id="eleven_v4", settings={}, previous_text="قبل", next_text="بعد", language="fa",
                                     language_code="fa", pronunciation_dictionaries=locators))
    assert fa["previous_text"] == "قبل" and fa["next_text"] == "بعد" and fa["pronunciation_dictionary_locators"] == locators
    en = provider._body(VoiceRequest(text="x", voice_id="V", model_id="eleven_v4", settings={}, previous_text="before", next_text="after", language="en",
                                     language_code="en"))
    assert "previous_text" not in en and "next_text" not in en and "pronunciation_dictionary_locators" not in en


def test_the_dictionary_is_created_with_the_rules_and_a_refusal_is_an_error(monkeypatch):
    import app.providers.voice.elevenlabs as EL

    sent: list[tuple[str, dict]] = []

    class Response:
        def __init__(self, status, data):
            self.status_code, self._data, self.text = status, data, json.dumps(data)

        def json(self):
            return self._data

    answers = [Response(200, {"id": "D1", "version_id": "V1"}), Response(401, {"detail": {"message": "missing the permission pronunciation_dictionaries_write"}})]

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent.append((url, json))
            return answers.pop(0)

    monkeypatch.setattr(EL.httpx, "AsyncClient", Client)
    provider = EL.ElevenLabsVoiceProvider.__new__(EL.ElevenLabsVoiceProvider)
    provider.cfg, provider.api_key = ai_config.voice.elevenlabs, "key"
    rules = [{"string_to_replace": "کالب", "type": "phoneme", "alphabet": "ipa", "phoneme": "kɑːleb"}]
    assert asyncio.run(provider.create_pronunciation_dictionary("film", rules)) == {"id": "D1", "version_id": "V1"}
    assert sent[0][0].endswith("/v1/pronunciation-dictionaries/add-from-rules") and sent[0][1] == {"name": "film", "rules": rules}
    with pytest.raises(EL.VoiceProviderError, match="pronunciation_dictionaries_write"):
        asyncio.run(provider.create_pronunciation_dictionary("film", rules))


pytestmark_audio = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for audio tests")


@pytestmark_audio
def test_a_prepared_language_is_voiced_once_per_block_with_its_dictionary_and_nothing_is_listened_to(tmp_path, monkeypatch):
    from test_voice_render import FakeASR, FakeTTS, _block

    from app.documentary.voice_render import VoiceRenderer

    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))

    class Listener:
        def frames(self, wav):
            raise AssertionError("the phoneme listener is never used for a prepared language")

    class Asr(FakeASR):
        def transcribe(self, audio_path, language):
            raise AssertionError("speech-to-text is never used for a prepared language")

    locators = [{"pronunciation_dictionary_id": "D1", "version_id": "V1"}]
    tts = FakeTTS()
    renderer = VoiceRenderer(provider=tts, asr=Asr(), listener=Listener())
    block = _block("FA_a_01", "act1", "کالب آمد و گاراژ را دید و رفت.")
    block["previous_text"], block["next_text"] = "قبلی.", "بعدی."
    block["sentences"] = [{"speech": block["text"], "display": block["text"], "tts": block["text"], "risky": [{"w": "کالب", "read": "kaaleb", "meaning": "name"}]}]
    manifest = asyncio.run(renderer.render({"language": "fa", "blocks": [block], "pronunciation_dictionary": locators}, case_id=9, story_version_id=4))
    assert len(tts.requests) == 1, "one take, no retake"
    assert tts.requests[0].pronunciation_dictionaries == locators and (tts.requests[0].previous_text, tts.requests[0].next_text) == ("قبلی.", "بعدی.")
    assert manifest["blocks"][0]["attempts"] == 1 and manifest["blocks"][0]["asr"] is None
    assert (tmp_path / "9" / "audio" / "fa" / "v4_prepared" / "manifest.json").exists() and not (tmp_path / "9" / "audio" / "fa" / "v4").exists()


@pytestmark_audio
def test_english_is_still_checked_and_voiced_the_old_way(tmp_path, monkeypatch):
    from test_voice_render import FakeASR, FakeTTS, _block, _plan

    from app.documentary.voice_render import VoiceRenderer

    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    tts, asr = FakeTTS(), FakeASR()
    manifest = asyncio.run(VoiceRenderer(provider=tts, asr=asr).render(
        _plan([_block("EN_a_01", "act1", "The house was empty and every room had been scrubbed clean.")]), case_id=9, story_version_id=4))
    assert asr.calls >= 1 and manifest["blocks"][0]["asr"] is not None and tts.requests[0].pronunciation_dictionaries == []
    assert (tmp_path / "9" / "audio" / "en" / "v4" / "manifest.json").exists()


def test_the_editor_and_the_checker_get_a_task_line_before_their_input_never_the_bare_json(db_session, monkeypatch):
    from types import SimpleNamespace

    case, old = _version(db_session)
    seen: list[tuple[str, str]] = []

    async def run_agent(name, gen, body):
        seen.append((name, body))
        if name.endswith("edit"):
            return {"sentences": [{"i": s["i"], **EDITS[s["i"]]} for s in json.loads(body.split("INPUT:\n", 1)[1])["beats"][0]["sentences"]]}, SimpleNamespace(model="m")
        return {"beats": [{"beat_id": "B01", "ok": True, "problems": []}]}, SimpleNamespace(model="m")

    monkeypatch.setattr(VP, "run_agent", run_agent)
    monkeypatch.setattr(VP, "get_generation_provider", lambda: object())
    monkeypatch.setattr(VP, "stamp_run", lambda *a: None)
    sentences = [VP.Sentence(0, "B01", 0, "ساعت دو بامداد بود.")]
    asyncio.run(VP.edit(db_session, case, "fa", sentences, {"B01": "At 2 a.m."}, {}))
    sentences[0].final = "ساعت دو بامداد بود."
    assert asyncio.run(VP.verify(db_session, case, "fa", sentences, {"B01": "At 2 a.m."})) == []
    assert [name for name, _ in seen] == ["documentary.voice_prepare.edit", "documentary.voice_prepare.check"]
    assert all(body.startswith("TASK:") and "\n\nINPUT:\n{" in body for _, body in seen)


def test_a_word_written_down_one_way_most_of_the_time_keeps_that_way_when_one_sentence_wrote_it_the_other():
    def rule(kind, pron):
        return [{"surface": "تیپ‌سیتی", "type": kind, "pronunciation": pron, "reason": ""}]

    sentences = [sentence(0, "x", "تیپ‌سیتی", rule("phoneme", "tipsiti")), sentence(1, "x", "تیپ‌سیتی", rule("phoneme", "tipsiti")),
                 sentence(2, "x", "تیپ‌سیتی", rule("alias", "تیپ سیتی"))]
    rules, conflicts = VP.dictionary_rules(sentences)
    assert conflicts == [] and rules == [{"string_to_replace": "تیپ‌سیتی", "type": "phoneme", "alphabet": "ipa", "phoneme": "tipsiti"}]
    split = [sentences[0], sentences[2]]
    assert "two readings" in VP.dictionary_rules(split)[1][0]["what"], "an even split is a real doubt"


def test_the_readings_the_editor_decided_itself_are_listed_the_least_sure_first_and_the_approved_ones_are_not(monkeypatch):
    def rule(surface, pron, conf, basis="english"):
        return {"surface": surface, "type": "phoneme", "pronunciation": pron, "reason": "", "basis": basis, "confidence": conf}

    monkeypatch.setattr(ai_config.voice_prepare, "approved_readings", {"fa": {"کالب": {"type": "phoneme", "pronunciation": "kɑːleb"}}})
    sentences = [sentence(0, "x", "کالب باتنر", [rule("باتنر", "bɑːtner", "high", "spelling")]), sentence(1, "x", "ریزمیلر باتنر", [rule("ریزمیلر", "rizmiler", "low"),
                                                                                                                   rule("باتنر", "bɑːtner", "medium", "spelling")]),
                 sentence(2, "x", "کالب")]
    VP.apply_approved(sentences, "fa")
    decided = VP.decisions_of(sentences)
    assert [(d["surface"], d["confidence"], d["sentences"]) for d in decided] == [("ریزمیلر", "low", 1), ("باتنر", "medium", 2)], "the approved word is not listed"


def test_an_answer_given_before_for_the_same_input_is_read_from_the_cache_and_costs_nothing(db_session, monkeypatch):
    case, old = _version(db_session)
    asked: list[int] = []

    async def ask(db, case_id, label, payload, role, runner):
        asked.append(sum(len(b["sentences"]) for b in payload["beats"]))
        return {"sentences": [{"i": s["i"], **EDITS[s["i"]]} for beat in payload["beats"] for s in beat["sentences"]]}

    monkeypatch.setattr(VP, "_ask", ask)
    first = VP.sentences_of(old)
    asyncio.run(VP.edit(db_session, case, "fa", first, {"B01": "x", "B02": "y"}, {}))
    second = VP.sentences_of(old)
    asyncio.run(VP.edit(db_session, case, "fa", second, {"B01": "x", "B02": "y"}, {}))
    assert asked == [4] and [s.final for s in second] == [s.final for s in first], "the second run asked nothing"
    asyncio.run(VP.edit(db_session, case, "fa", VP.sentences_of(old), {"B01": "a different source", "B02": "y"}, {}))
    assert asked == [4, 3], "only the sentences whose input changed (the beat B01 has three sentences) are asked again"


def test_a_sentence_that_fails_a_check_goes_back_to_the_editor_alone_with_its_problem_and_is_then_accepted(db_session, fakes, monkeypatch):
    case, old = _version(db_session)
    seen: list[tuple[list[int], dict]] = []

    async def ask(db, case_id, label, payload, role, runner):
        if role == "voice_prepare_checker":
            return {"beats": [{"beat_id": b["beat_id"], "ok": True, "problems": []} for b in payload["beats"]]}
        asked = [s for beat in payload["beats"] for s in beat["sentences"]]
        seen.append(([s["i"] for s in asked], {s["i"]: s.get("problems_to_fix") for s in asked}))
        out = []
        for s in asked:
            item = {"i": s["i"], **EDITS[s["i"]]}
            if len(seen) == 1 and s["i"] == 1:
                item["final_text"] = "او تماس گرفت."           # the first answer loses the number 911
            out.append(item)
        return {"sentences": out}

    monkeypatch.setattr(VP, "_ask", ask)
    new = asyncio.run(VP.prepare_version(db_session, case, old, "fa"))
    assert seen[0][0] == [0, 1, 2, 3] and seen[1][0] == [1], "the second call carries only the failing sentence"
    assert "911" in seen[1][1][1][0] or "۹۱۱" in seen[1][1][1][0] or "number" in seen[1][1][1][0]
    assert "کالب با ۹۱۱ تماس گرفت." in new.story_text
