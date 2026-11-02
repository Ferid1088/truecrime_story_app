"""The semantic audio review: a model listens to a voiced block and judges it against the source; a failed block is corrected (what the voice is
given, never what viewers read) up to twice; a block that still fails stops the film. No network: the model is a fake, real FFmpeg runs."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil

import pytest

from app.core.ai_config import ai_config
from app.documentary import semantic_review as SR

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for audio tests")


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    monkeypatch.setattr(ai_config.voice, "prepared_languages", [])
    monkeypatch.setattr(ai_config.semantic_review, "languages", ["fa"])
    monkeypatch.setattr(ai_config.semantic_review, "delivery_tags", ["[calm]", "[serious]"])
    monkeypatch.setattr(SR, "track_run", lambda *a, **k: contextlib.nullcontext(object()))
    monkeypatch.setattr(SR, "stamp_run", lambda *a, **k: None)
    monkeypatch.setattr(SR, "get_generation_provider", lambda: object())


def raw(verdict="pass", errors=(), **extra):
    return {"verdict": verdict, "confidence": 0.9, "errors": list(errors), "corrected_text": None, "corrected_pronunciation_rules": [],
            "corrected_delivery_instruction": None, **extra}


def error(severity="critical", kind="name"):
    return {"type": kind, "severity": severity, "expected": "اشلی", "heard": "اصلی", "reason": "the name is said as a word"}


def block(texts=("اشلی آمد.", "او رفت.")):
    return {"block_id": "FA_B01_01", "section_id": "B01", "text": " ".join(texts), "previous_text": "قبل", "next_text": "بعد",
            "sentences": [{"speech": t, "display": t, "tts": t, "level": None, "risky": []} for t in texts]}


# ---------------------------------------------------------------------------
# the verdict and the corrections
# ---------------------------------------------------------------------------


def test_a_pass_needs_the_models_pass_no_blocking_error_and_enough_confidence():
    assert SR.normalise(raw())["verdict"] == "pass"
    assert SR.normalise(raw(errors=[error("minor", "delivery")]))["verdict"] == "pass"
    assert SR.normalise(raw(errors=[error("critical")]))["verdict"] == "fail", "a critical error fails even when the model says pass"
    assert SR.normalise(raw(errors=[error("major")]))["verdict"] == "fail"
    assert SR.normalise({**raw(), "confidence": 0.1})["verdict"] == "fail"
    assert SR.normalise(raw("fail"))["verdict"] == "fail"
    assert SR.normalise({})["verdict"] == "fail", "an answer without a verdict is not a pass"


def test_a_delivery_instruction_outside_the_allowed_tags_is_dropped():
    assert SR.normalise(raw(corrected_delivery_instruction="[calm]"))["corrected_delivery_instruction"] == "[calm]"
    assert SR.normalise(raw(corrected_delivery_instruction="[whispers]"))["corrected_delivery_instruction"] is None


def test_dates_numbers_urls_and_paths_are_protected_items():
    items = SR.protected_items("در ۱۶ فوریه ۲۰۲۶ به https://example.com/a رفت و /console/ را باز کرد.")
    assert {"۱۶", "۲۰۲۶", "https://example.com/a", "/console/"} <= set(items)


def test_a_correction_changes_what_the_voice_is_given_and_never_what_people_read():
    review = SR.normalise(raw("fail", [error()], corrected_pronunciation_rules=[{"surface": "اشلی", "replacement": "اَشْلی"}],
                              corrected_delivery_instruction="[calm]"))
    fixed, applied, respelled = SR.corrected_block(block(), review)
    assert fixed["sentences"][0]["tts"] == "[calm] اَشْلی آمد." and fixed["sentences"][1]["tts"] == "او رفت."
    assert [s["display"] for s in fixed["sentences"]] == ["اشلی آمد.", "او رفت."] and [s["speech"] for s in fixed["sentences"]] == ["اشلی آمد.", "او رفت."]
    assert fixed["tts_text"] == "[calm] اَشْلی آمد. او رفت." and applied == ["respelled اشلی", "tag [calm]"] and respelled == [{"surface": "اشلی", "replacement": "اَشْلی"}]


def test_a_corrected_text_is_taken_only_with_the_sentences_of_the_block():
    same = SR.normalise(raw("fail", [error()], corrected_text="اَشْلی آمد. او رفت."))
    assert SR.corrected_block(block(), same)[0]["sentences"][0]["tts"] == "اَشْلی آمد."
    other = SR.normalise(raw("fail", [error()], corrected_text="فقط یک جمله."))
    fixed, applied, _ = SR.corrected_block(block(), other)
    assert fixed["sentences"][0]["tts"] == "اشلی آمد." and applied == ["text ignored: its sentences are not those of the block"]


# ---------------------------------------------------------------------------
# the loop around the voice-repair loop
# ---------------------------------------------------------------------------


def make_wav(path):
    import subprocess

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=1", "-ac", "1", str(path)], check=True)
    return str(path)


class Scenario:
    """A fake reviewer model that answers from a list, and a fake voice-repair loop that records the blocks it is asked to make."""

    def __init__(self, tmp_path, answers):
        self.answers, self.heard, self.spoken, self.tmp = list(answers), [], [], tmp_path
        self.reviewer = SR.Reviewer(None, 1, {"B01": "Ashley came. He left."}, {"B01": {"purpose": "the call"}}, runner=self.runner)

    async def runner(self, gen, body, audio):
        self.heard.append((json.loads(body), audio))
        return self.answers.pop(0), object()

    async def speak(self, blk, seed):
        self.spoken.append((blk, seed))
        return {"block": blk, "take": {"wav_path": make_wav(self.tmp / f"take{len(self.spoken)}.wav")}}

    def run(self):
        first = asyncio.run(self.speak(block(), 1))
        self.spoken.clear()
        return asyncio.run(self.reviewer.run(first, self.speak, 1))


def test_the_reviewer_gets_the_audio_and_the_whole_context_of_the_block(tmp_path):
    s = Scenario(tmp_path, [raw()])
    result = s.run()
    context, audio = s.heard[0]
    assert audio[0].startswith("data:audio/mpeg;base64,")
    assert context["source_text"] == "Ashley came. He left." and context["scene_context"] == {"purpose": "the call"}
    assert context["display_text"] == "اشلی آمد. او رفت." and context["tts_text"] == "اشلی آمد. او رفت."
    assert context["previous_text"] == "قبل" and context["next_text"] == "بعد" and context["allowed_delivery_tags"] == ["[calm]", "[serious]"]
    record = result["semantic_review"]
    assert record["final_status"] == "approved" and record["voice_attempts"] == 1 and record["semantic_review_attempts"] == 1
    assert record["voice_review_result"] == "pass" and record["avatar_review_result"] == "not_applicable" and s.spoken == []


def test_a_failed_block_goes_back_to_the_voice_with_the_correction_and_is_reviewed_again(tmp_path):
    fail = raw("fail", [error()], corrected_pronunciation_rules=[{"surface": "اشلی", "replacement": "اَشْلی"}])
    s = Scenario(tmp_path, [fail, raw()])
    result = s.run()
    assert len(s.spoken) == 1 and s.spoken[0][0]["sentences"][0]["tts"] == "اَشْلی آمد." and s.spoken[0][1] == 2
    assert s.heard[1][0]["tts_text"] == "اَشْلی آمد. او رفت.", "the second listening is of the corrected block"
    record = result["semantic_review"]
    assert record["final_status"] == "approved" and record["voice_attempts"] == 2 and record["semantic_review_attempts"] == 2
    assert record["reviews"][0]["verdict"] == "fail" and record["reviews"][0]["applied"] == ["respelled اشلی"]


def test_after_two_corrections_a_block_that_still_fails_is_failed_and_the_loop_ends(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.semantic_review, "max_corrections", 2)
    fail = raw("fail", [error()])
    s = Scenario(tmp_path, [fail, fail, fail, fail])
    record = s.run()["semantic_review"]
    assert len(s.spoken) == 2 and record["voice_attempts"] == 3 and record["semantic_review_attempts"] == 3, "no endless retry loop"
    assert record["final_status"] == "failed" and record["voice_review_result"] == "fail" and record["errors"][0]["heard"] == "اصلی"
    assert SR.failed_blocks({"x": record}) == [{"block_id": "FA_B01_01", "reason": "still failing after the allowed corrections"}]


# ---------------------------------------------------------------------------
# inside the narration render
# ---------------------------------------------------------------------------


def test_the_render_keeps_every_record_and_does_not_assemble_the_film_when_a_block_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.semantic_review, "failed_blocks_wait_for_owner", False)
    from test_voice_render import FakeASR, FakeTTS, _block

    from app.documentary.voice_render import VoiceRenderer

    renderer = VoiceRenderer(provider=FakeTTS(), asr=FakeASR())
    renderer.reviewer = SR.Reviewer(None, 1, runner=Scenario(tmp_path, [raw("fail", [error()])] * (ai_config.semantic_review.max_corrections + 1)).runner)
    plan = {"language": "fa", "blocks": [_block("FA_a_01", "B01", "The house was empty and every room had been scrubbed clean.")]}
    with pytest.raises(SR.SemanticReviewFailed, match="FA_a_01"):
        asyncio.run(renderer.render(plan, case_id=7, story_version_id=3))
    out = VoiceRenderer.out_dir_for(7, "fa", 3)
    assert out.name == "v3_reviewed", "a reviewed language has its own version folder"
    assert json.loads((out / "semantic_review.json").read_text())["FA_a_01"]["final_status"] == "failed"
    assert not (out / "narration.mp3").exists(), "a failed block is not published"


def test_a_reviewed_language_is_not_listened_to_by_the_older_checks_and_the_others_still_are(monkeypatch):
    monkeypatch.setattr(ai_config.voice, "prepared_languages", [])
    assert ai_config.listens_after_voice("fa") is False and ai_config.listens_after_voice("ar") is True and ai_config.listens_after_voice("en") is True
    monkeypatch.setattr(ai_config.semantic_review, "languages", [])
    assert ai_config.listens_after_voice("fa") is True
    monkeypatch.setattr(ai_config.voice, "prepared_languages", ["fa"])
    assert ai_config.listens_after_voice("fa") is False


def test_a_reviewed_language_is_voiced_once_per_block_without_speech_to_text_or_the_pronunciation_loop(tmp_path):
    from test_voice_render import FakeTTS, _block

    from app.documentary.voice_render import VoiceRenderer

    class Asr:
        name = "never-called"

        def transcribe(self, *a):
            raise AssertionError("speech-to-text is not used for a reviewed language")

    tts = FakeTTS()
    renderer = VoiceRenderer(provider=tts, asr=Asr())
    plan = {"language": "fa", "blocks": [_block("FA_a_01", "B01", "The house was empty and every room had been scrubbed clean.")]}
    asyncio.run(renderer.render(plan, case_id=7, story_version_id=4))
    assert len(tts.requests) == 1


def test_a_voice_that_reads_the_pronunciation_notation_aloud_gets_the_block_without_the_dictionary_and_then_without_tags():
    review = SR.normalise(raw("fail", [{"type": "pronunciation", "severity": "major", "expected": "فوریه", "heard": "اسلش اف ای وی اسلش", "reason": "read as symbols"}]))
    assert review["notation_read_aloud"] is True and SR.normalise(raw())["notation_read_aloud"] is False
    rules = [{"string_to_replace": "کالب", "type": "alias", "alias": "کالِبْ"}, {"string_to_replace": "فوریه", "type": "phoneme", "alphabet": "ipa", "phoneme": "fevrije"}]
    blk = block(("[calm] کالب در فوریه آمد.", "او رفت."))
    fixed, applied = SR.without_dictionary(blk, rules)
    assert fixed["no_dictionary"] is True and fixed["sentences"][0]["tts"] == "[calm] کالِبْ در فوریه آمد." and applied == ["without the dictionary", "respelled کالب"]
    assert [s["display"] for s in fixed["sentences"]] == ["[calm] کالب در فوریه آمد.", "او رفت."], "what people read is not touched"
    again, applied = SR.without_dictionary(fixed, rules)
    assert again["sentences"][0]["tts"] == "کالِبْ در فوریه آمد." and "without audio tags" in applied


def test_a_block_marked_without_the_dictionary_is_sent_without_it(tmp_path, monkeypatch):
    from test_voice_render import FakeTTS, _block

    from app.documentary.voice_render import VoiceRenderer

    monkeypatch.setattr(ai_config.voice, "prepared_languages", ["fa"])
    tts = FakeTTS()
    renderer = VoiceRenderer(provider=tts, use_asr=False, use_listener=False)
    renderer.dictionaries = [{"pronunciation_dictionary_id": "D", "version_id": "V"}]
    plain = _block("FA_a_01", "B01", "The house was empty and every room had been scrubbed clean.")
    asyncio.run(renderer.speak_block(plain, "fa", ai_config.voice.for_language("en"), {}, 1, tmp_path / "t1"))
    asyncio.run(renderer.speak_block({**plain, "no_dictionary": True}, "fa", ai_config.voice.for_language("en"), {}, 1, tmp_path / "t2"))
    assert [r.pronunciation_dictionaries for r in tts.requests] == [renderer.dictionaries, []]


def test_the_reviewer_has_its_own_model_route_without_a_fallback_to_a_model_that_cannot_hear():
    assert ai_config.model_for("semantic_audio_reviewer") == "gemini-3.8-flash" and ai_config.fallback_models_for("semantic_audio_reviewer") == []
    assert ai_config.capabilities_of("gemini-3.8-flash").audio_input is True


def test_a_failed_review_call_is_made_again_and_a_judged_block_is_not_judged_twice(tmp_path, monkeypatch):
    from app.providers.generation.base import GenerationError

    monkeypatch.setattr(ai_config.semantic_review, "retry_pause_s", 0.0)
    calls = []

    async def flaky(gen, body, audio):
        calls.append(1)
        if len(calls) < 3:
            raise GenerationError("provider_error", "hiccup")
        return raw(), object()

    wav = make_wav(tmp_path / "block.wav")
    reviewer = SR.Reviewer(None, 1, runner=flaky)
    first = asyncio.run(reviewer.listen({"tts_text": "x"}, __import__("pathlib").Path(wav), "B1"))
    assert first["verdict"] == "pass" and len(calls) == 3, "two hiccups, then the answer"
    again = asyncio.run(reviewer.listen({"tts_text": "x"}, __import__("pathlib").Path(wav), "B1"))
    assert again == first and len(calls) == 3, "the same audio and context come from the saved answer"
    asyncio.run(reviewer.listen({"tts_text": "another context"}, __import__("pathlib").Path(wav), "B1"))
    assert len(calls) == 4


def test_a_block_that_cannot_be_reviewed_fails_alone_and_the_others_go_on(tmp_path, monkeypatch):
    from app.providers.generation.base import GenerationError

    monkeypatch.setattr(ai_config.semantic_review, "retry_pause_s", 0.0)

    async def down(gen, body, audio):
        raise GenerationError("provider_error", "down")

    s = Scenario(tmp_path, [])
    reviewer = SR.Reviewer(None, 1, runner=down)
    first = asyncio.run(s.speak(block(), 1))
    result = asyncio.run(reviewer.run(first, s.speak, 1))
    record = result["semantic_review"]
    s.spoken.clear()
    assert record["final_status"] == "failed" and "could not be made" in record["failure"] and record["semantic_review_attempts"] == 0, "no correction for a block that was not judged"
    assert SR.failed_blocks({"B": record})[0]["reason"].startswith("the review could not be made")


def test_when_the_owner_is_the_gate_a_failed_block_is_flagged_and_listed_and_the_narration_is_assembled(db_session, tmp_path, monkeypatch):
    from test_voice_render import FakeASR, FakeTTS, _block

    from app.documentary import voice_review as VRV
    from app.documentary.pipeline.errors import AwaitingVoiceReview
    from app.documentary.voice_render import VoiceRenderer

    monkeypatch.setattr(ai_config.semantic_review, "failed_blocks_wait_for_owner", True)
    monkeypatch.setattr(ai_config.voice, "review_languages", ["fa"])
    renderer = VoiceRenderer(provider=FakeTTS(), asr=FakeASR())
    renderer.reviewer = SR.Reviewer(None, 1, runner=Scenario(tmp_path, [raw("fail", [error()])] * (ai_config.semantic_review.max_corrections + 1)).runner)
    plan = {"language": "fa", "blocks": [_block("FA_a_01", "B01", "The house was empty and every room had been scrubbed clean.")]}
    manifest = asyncio.run(renderer.render(plan, case_id=7, story_version_id=6))
    out = VoiceRenderer.out_dir_for(7, "fa", 6)
    assert (out / "narration.mp3").exists() and manifest["semantic_review_failed"][0]["block_id"] == "FA_a_01"
    assert "FA_a_01:semantic_review_failed" in manifest["flags"]
    version = type("V", (), {"case_id": 7, "language": "fa", "id": 6})()
    state = VRV.state(version)
    assert [b["block_id"] for b in state["failed_blocks"]] == ["FA_a_01"] and not state["approved"]
    with pytest.raises(AwaitingVoiceReview, match="1 blocks did not pass the listening review: FA_a_01"):
        VRV.guard(version, "fa")


def test_a_missing_or_wrong_ezafe_fails_the_block_and_is_never_rewritten_into_another_word():
    review = SR.normalise(raw(ezafe_missing=[{"phrase": "X Y", "head": "X", "heard": "x-eye y"}]))
    assert review["verdict"] == "fail" and review["errors"][0]["reason"] == "the ezafe after X is not said"
    fixed, applied, respelled = SR.corrected_block(block(("X Y بود.", "او رفت.")), review)
    assert [s["tts"] for s in fixed["sentences"]] == ["X Y بود.", "او رفت."] and respelled == [], "nothing is rewritten: the app makes another take"



# ---------------------------------------------------------------------------
# the last check: each word that needs one, listened to alone
# ---------------------------------------------------------------------------


def test_the_source_and_the_tone_of_a_block_are_those_of_the_beats_it_tells_not_of_its_section_id(tmp_path):
    reviewer = SR.Reviewer(None, 1, {"B08": "He opened the door.", "B09": "Then he left."}, {"B08": {"purpose": "the door"}})
    blk = {**block(), "section_id": "B08.g02", "beats": [{"beat_id": "B08"}, {"beat_id": "B09"}]}
    context = reviewer.context(blk)
    assert context["source_text"] == "He opened the door.\n\nThen he left." and context["scene_context"] == {"purpose": "the door"}
    assert reviewer.context({**block(), "section_id": "B09.g01"})["source_text"] == "Then he left."
