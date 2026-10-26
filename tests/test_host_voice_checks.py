"""The voice checks for fa and ar: the pronunciation editor per language, the Arabic key and fix
ladder, and the host's voice going through the same checks as the story's blocks."""
import asyncio
import json
from types import SimpleNamespace

from test_studio import FakeVoice, _renderer, _segments, _silence_mp3

from app.core.ai_config import ai_config
from app.documentary import pronunciation as PR

# ---------------------------------------------------------------------------
# configuration and the editor per language
# ---------------------------------------------------------------------------


def test_fa_and_ar_are_checked_and_the_ladder_is_the_same_for_both():
    assert ai_config.pronunciation.languages == ["fa", "ar"]
    assert ai_config.pronunciation.fix_order == ["vowelled", "full", "respell", "synonym"]
    assert ai_config.asr_check.for_language("ar").model == "large-v3-turbo"
    assert ai_config.asr_check.for_language("ar").max_word_error_rate > \
        ai_config.asr_check.for_language("en").max_word_error_rate


def test_the_editor_prompt_follows_the_language(db_session, monkeypatch):
    fa = ai_config.agents["documentary.pronunciation"]
    ar = ai_config.agents["documentary.pronunciation.ar"]
    assert fa.prompt == "documentary/pronunciation/editor_system"
    assert ar.prompt == "documentary/pronunciation/editor_system_ar"
    assert fa.role == ar.role == "pronunciation_editor"
    from app.core.prompts import prompt

    assert "SCOPE AND SPECIAL CASES (Persian)" in prompt(fa.prompt)
    assert "SCOPE AND SPECIAL CASES (Arabic)" in prompt(ar.prompt)
    assert "ezafe" in prompt(fa.prompt).lower() and "ezafe" not in prompt(ar.prompt).lower()

    seen = []

    class Gen:
        def is_configured(self):
            return True

        async def generate_structured(self, role, system, user, images=None):
            from app.providers.generation.base import GenerationResult
            seen.append(system)
            return {"sentences": []}, GenerationResult(text="{}", model="m", provider="p")

    monkeypatch.setattr(PR, "get_generation_provider", lambda: Gen())
    editor = PR.PronunciationEditor()
    case_id = _segments(db_session, "fa").case_id
    for language in ("fa", "ar"):
        _, report = asyncio.run(editor.annotate(db_session, case_id, language, [
            {"i": 0, "beat_id": "b", "text": "x", "source": "x"}]))
        assert report["errors"] == []
    assert "SCOPE AND SPECIAL CASES (Persian)" in seen[0]
    assert "SCOPE AND SPECIAL CASES (Arabic)" in seen[1]


def test_the_pronunciation_editor_runs_on_a_model_that_obeys_the_system_message():
    routing = ai_config.generation_providers["apimaster"].routing
    assert routing["pronunciation_editor"] != "premium" and routing["host_meaning_checker"] == "writer"


# ---------------------------------------------------------------------------
# Arabic keys: harakat, shadda, sukun, tanween, the reading
# ---------------------------------------------------------------------------


def test_arabic_readings_use_the_same_vowel_classes():
    assert PR.reading_vowels("ilm") == ["i"] and PR.reading_vowels("qattala") == ["a", "a", "a"]
    assert PR.reading_vowels("dhahab") == ["a", "a"] and PR.reading_vowels("kutub") == ["u", "u"]
    assert PR.reading_vowels("hukm") == ["u"] and PR.reading_vowels("sijn") == ["i"]
    assert PR.reading_vowels("deen") == ["i"]                # ee is a long ī, as in Persian


def test_arabic_harakat_agree_ignores_shadda_sukun_and_tanween():
    assert PR.harakat_agree("عِلْم", "elm") and not PR.harakat_agree("عَلْم", "elm")
    assert PR.harakat_agree("حُكْم", "hokm") and PR.harakat_agree("قَتَّلَ", "qattala")
    assert PR.harakat_agree("كِتَابًا", "ketaab")          # tanween carries no vowel of the reading
    assert not PR.harakat_agree("ذَهَب", "dhohob")
    assert PR.strip_harakat("قَتَّلَ") == "قتل" and PR.strip_harakat("كِتَابًا") == "كتابا"


def test_an_arabic_key_is_validated_like_a_persian_one_and_a_contradicting_form_drops_the_word():
    sentence = "قَتَلَ الرجل علم الحقيقة"
    words = [
        {"w": "علم", "read": "elm", "meaning": "knowledge", "vowelled": "عِلْم", "full": "عِلْم",
         "respell": "", "synonym": "معرفة", "synonym_read": "ma'rifa"},
        {"w": "الرجل", "read": "rajol", "vowelled": "الرَّجُل", "full": "ar-rajul",
         "respell": "", "synonym": ""},
        {"w": "ليس", "read": "x", "vowelled": ""},
    ]
    keyed, issues = PR.validate_key(sentence, words)
    assert [w["w"] for w in keyed] == ["علم", "الرجل"]
    assert keyed[0]["vowelled"] == "عِلْم" and keyed[0]["synonym"] == "معرفة"
    assert any(i.startswith("not_in_sentence") for i in issues)
    bad, issues = PR.validate_key("ذهب الرجل", [{"w": "ذهب", "read": "dhahab", "vowelled": "ذُهِب"}])
    assert bad == [] and issues == ["key_inconsistent:ذهب"]


def test_the_arabic_fix_ladder_goes_tashkeel_full_respell_synonym():
    sentence = {"speech": "علم الرجل", "tts": None, "risky": [{
        "w": "علم", "read": "elm", "meaning": "knowledge", "vowelled": "عِلم", "full": "عِلْم",
        "respell": "عيلم", "synonym": "معرفة", "synonym_read": "ma'rifa"}]}
    occurrences = PR.risky_occurrences([sentence])
    assert [f["kind"] for f in occurrences[0]["forms"]] == ["vowelled", "full", "respell", "synonym"]
    sentences, wrong = [dict(sentence)], [{"sentence": 0, "word": "علم"}]
    texts = []
    for _ in range(4):
        sentences, applied = PR.apply_fixes(sentences, occurrences, wrong)
        texts.append(sentences[0]["tts"])
    assert texts == ["عِلم الرجل", "عِلْم الرجل", "عيلم الرجل", "معرفة الرجل"]
    assert sentences[0]["speech"] == "معرفة الرجل"                # only a synonym changes what is read
    assert occurrences[0]["read"] == "ma'rifa"


def test_the_listener_can_match_arabic_consonants_and_the_vowels_of_the_key():
    heard = ["ʔ", "i", "l", "m"]
    assert PR.judge("elm", heard)["ok"] is True
    assert PR.judge("elm", ["a", "l", "a", "m"])["ok"] is False        # alam, the flag
    assert PR.word_tokens("dhahab", ["x", "ð", "a", "h", "a", "b", "y"])[:3] == ["ð", "a", "h"]
    assert PR.reading_consonants("thaabit")[0] >= {"θ"}


# ---------------------------------------------------------------------------
# the host's voice goes through the checks
# ---------------------------------------------------------------------------


class FakeEditor:
    def __init__(self, words_by_sentence=None):
        self.calls = 0
        self.words = words_by_sentence or {}

    async def annotate(self, db, case_id, language, items):
        self.calls += 1
        return ({it["i"]: self.words.get(it["i"], []) for it in items},
                {"sentences": len(items), "risky_words": sum(map(len, self.words.values()))})


class CheckingRenderer:
    """Stands in for VoiceRenderer.speak_block: records the block it gets and returns the take a
    real run would (audio, timing, the checks' results)."""

    def __init__(self, tmp_path, asr_passed=True, wrong=(), tts=None):
        self.blocks, self.tmp, self.asr_passed, self.wrong, self.tts = [], tmp_path, asr_passed, wrong, tts
        self.asr = SimpleNamespace(name="fake-whisper")
        self._listener = SimpleNamespace(name="fake-listener")

    async def speak_block(self, block, language, vcfg, settings, seed, blocks_dir, synonyms=True):
        self.blocks.append(block)
        self.synonyms = synonyms
        raw = self.tmp / f"take{len(self.blocks)}.mp3"
        raw.write_bytes(_silence_mp3(3.0))
        text = self.tts or block["text"]
        n = len(text)
        take = {"raw_path": str(raw), "text": text, "request_id": "r1", "character_cost": n,
                "alignment": {"characters": list(text), "starts": [i * .05 for i in range(n)],
                              "ends": [(i + 1) * .05 for i in range(n)]},
                "asr": {"passed": self.asr_passed, "word_error_rate": 0.0 if self.asr_passed else .5,
                        "failures": [], "heard_text": "x"},
                "pronunciation": [{"word": w, "ok": False, "sentence": 0} for w in self.wrong]
                + [{"word": "y", "ok": True, "sentence": 0}]}
        return {"take": take, "fixes": [{"after_round": 0, "fixes": []}] if self.tts else [],
                "attempts": 2 if self.tts else 1}


def _fa_scene(db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import plan_host_scenes

    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    row = _segments(db_session, "fa")
    return plan_host_scenes(db_session, row)[0]


def test_the_host_voice_is_keyed_checked_and_its_text_stays_frozen(db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import run_scene

    scene = _fa_scene(db_session, tmp_path, monkeypatch)
    text, sha = scene.text, scene.text_sha256
    renderer = CheckingRenderer(tmp_path, tts="Guten Abend. Heute geht es um eine Tür.".replace("Tür", "تُر"))
    editor = FakeEditor({1: [{"w": "Tür", "read": "tor", "vowelled": "تُر"}]})
    asyncio.run(run_scene(db_session, scene, "voice", renderer=renderer, editor=editor))
    block = renderer.blocks[0]
    assert [s["speech"] for s in block["sentences"]] == ["Guten Abend.", "Heute geht es um eine Tür."]
    assert block["sentences"][1]["risky"][0]["w"] == "Tür"           # the key reaches the listener
    assert editor.calls == 1
    checks = json.loads(scene.voice_checks_json)
    assert checks["tts_text"].endswith("تُر.") and checks["fixes"] and checks["attempts"] == 2
    assert checks["asr"]["passed"] is True and checks["flags"] == []
    assert checks["asr_engine"] == "fake-whisper" and checks["listener"] == "fake-listener"
    # the frozen text and its hash are untouched: harakat live only in what the voice got
    assert scene.text == text and scene.text_sha256 == sha
    assert scene.voice_seconds and scene.status == "voice_ready"
    assert renderer.synonyms is False        # a synonym would change the frozen words


def test_unresolved_words_and_a_failed_whisper_check_become_flags_for_a_person(
        db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import run_scene, scene_dict

    scene = _fa_scene(db_session, tmp_path, monkeypatch)
    renderer = CheckingRenderer(tmp_path, asr_passed=False, wrong=["Tür"])
    asyncio.run(run_scene(db_session, scene, "voice", renderer=renderer, editor=FakeEditor()))
    flags = json.loads(scene.voice_checks_json)["flags"]
    assert flags == ["asr_check_failed", "pronunciation_unresolved:Tür"]
    body = scene_dict(scene)
    assert body["voice_flags"] == flags and body["voice_checks"]["asr"]["passed"] is False
    assert "key" not in body["voice_checks"]


def test_a_check_that_could_not_run_is_a_flag_not_a_pass(db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import run_scene

    scene = _fa_scene(db_session, tmp_path, monkeypatch)
    renderer = CheckingRenderer(tmp_path)
    renderer.asr, renderer._listener = None, None
    editor = FakeEditor({0: [{"w": "Guten", "read": "gooten"}]})
    asyncio.run(run_scene(db_session, scene, "voice", renderer=renderer, editor=editor))
    assert json.loads(scene.voice_checks_json)["flags"] == ["listener_unavailable", "asr_unavailable"]


def test_the_key_is_asked_once_per_text_and_again_when_the_text_is_rewritten(
        db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import _step_voice

    scene = _fa_scene(db_session, tmp_path, monkeypatch)
    editor, renderer = FakeEditor(), CheckingRenderer(tmp_path)
    asyncio.run(_step_voice(db_session, scene, renderer, editor))
    scene.voice_path = None                                      # a new take of the same text
    asyncio.run(_step_voice(db_session, scene, renderer, editor))
    assert editor.calls == 1 and len(renderer.blocks) == 2
    scene.text = scene.text + " Noch etwas."
    scene.text_sha256 = "f" * 64
    asyncio.run(_step_voice(db_session, scene, renderer, editor))
    assert editor.calls == 2


def test_en_and_de_host_voices_are_not_keyed(db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import plan_host_scenes, run_scene

    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    scene = plan_host_scenes(db_session, _segments(db_session, "de"))[0]
    editor, renderer = FakeEditor(), CheckingRenderer(tmp_path)
    asyncio.run(run_scene(db_session, scene, "voice", renderer=renderer, editor=editor))
    assert editor.calls == 0 and renderer.blocks[0]["sentences"][0]["risky"] == []


def test_a_real_renderer_speaks_the_host_block_through_the_story_path(db_session, tmp_path, monkeypatch):
    """The same speak_block as the story's blocks: a fake provider, no Whisper, no listener."""
    from app.documentary.host_scenes import run_scene

    scene = _fa_scene(db_session, tmp_path, monkeypatch)
    voice = FakeVoice()
    asyncio.run(run_scene(db_session, scene, "voice", renderer=_renderer(voice), editor=FakeEditor()))
    assert voice.calls == 1 and scene.voice_seconds > 0
    checks = json.loads(scene.voice_checks_json)
    assert checks["asr"] is None and checks["pronunciation"] == []
    assert "asr_unavailable" in checks["flags"]


def test_the_host_ladder_has_no_synonym_step_and_the_story_ladder_does():
    sentence = {"speech": "علم", "risky": [{"w": "علم", "read": "elm", "vowelled": "عِلم",
                                              "full": "عِلْم", "respell": "عيلم",
                                              "synonym": "معرفة", "synonym_read": "ma'rifa"}]}
    assert [f["kind"] for f in PR.risky_occurrences([sentence])[0]["forms"]][-1] == "synonym"
    assert [f["kind"] for f in PR.risky_occurrences([sentence], synonyms=False)[0]["forms"]] == \
        ["vowelled", "full", "respell"]
    assert ai_config.pronunciation.max_rounds >= 4        # enough rounds to reach every step


def test_the_arabic_article_is_not_judged_and_hamza_marks_do_not_break_the_key():
    assert PR.reading_vowels("al-asfal") == ["a", "a"] and PR.reading_vowels("el-kalaam") == ["a", "A"]
    assert PR.reading_consonants("al-mashhad")[0] == PR.reading_consonants("mashhad")[0]
    # the recognizer hears 'il' for 'al': that must not fail the word
    assert PR.judge("al-mashhad", ["i", "l", "m", "a", "ʃ", "h", "a", "t"])["ok"] is True
    assert PR.strip_harakat("زبانهٔ") == "زبانه"
    keyed, issues = PR.validate_key("زبانهٔ در", [{"w": "زبانه", "read": "zabaane", "vowelled": "زَبانه"}])
    assert [k["w"] for k in keyed] == ["زبانه"] and issues == []


def test_both_editor_prompts_ask_for_a_fix_of_every_word():
    from app.core.prompts import prompt

    for name in ("documentary/pronunciation/editor_system", "documentary/pronunciation/editor_system_ar"):
        assert "# ADDED: a fix for every word" in prompt(name)


def test_the_arabic_article_without_a_hyphen_in_the_key_is_normalised():
    keyed, _ = PR.validate_key("وضعوا البلطة هنا", [{"w": "البلطة", "read": "albalta",
                                                   "vowelled": "البَلْطة"}])
    assert keyed[0]["read"] == "al-balta" and PR.reading_vowels(keyed[0]["read"]) == ["a", "a"]
    other, _ = PR.validate_key("علم", [{"w": "علم", "read": "alam", "vowelled": "عَلَم"}])
    assert other[0]["read"] == "alam"                 # a word that merely starts with "al"


def test_the_referee_passes_a_word_whose_key_was_wrong_and_keeps_a_voice_error(tmp_path):
    from app.documentary.voice_render import VoiceRenderer

    class Referee:
        async def arbitrate(self, db, case_id, language, items):
            assert {"sentence", "word", "key_read", "heard_phonemes"} <= set(items[0])
            return {0: {"verdict": "key_wrong", "read": "mokhtalefin"},
                    1: {"verdict": "voice_wrong", "read": "sulifaan"}}

    renderer = VoiceRenderer(provider=FakeVoice(), use_asr=False, use_listener=False,
                             budget_case_id=1, arbiter=Referee())
    take = {"pronunciation": [
        {"word": "مختلفين", "read": "mokhtalefayn", "sentence": 0, "ok": False,
         "heard_ipa": "m u q t a l i f i n", "heard": ["u", "a", "i", "i"]},
        {"word": "سوليفان", "read": "sulifaan", "sentence": 0, "ok": False,
         "heard_ipa": "s oː l ɪ v ə n", "heard": ["o", "i", "e"]}]}
    sentences = [{"speech": "كانوا مختلفين مع سوليفان", "risky": [
        {"w": "مختلفين", "meaning": "different"}, {"w": "سوليفان", "meaning": "Sullivan"}]}]
    asyncio.run(renderer._referee(take, sentences, "ar"))
    first, second = take["pronunciation"]
    assert first["ok"] is True and first["referee"] == "key_wrong" and first["read"] == "mokhtalefin"
    assert second["ok"] is False and second["referee"] == "voice_wrong"


def test_the_referee_prompt_and_agent_exist_and_only_valid_verdicts_are_kept(db_session, monkeypatch):
    assert ai_config.agents["documentary.pronunciation.arbiter"].prompt == \
        "documentary/pronunciation/arbiter_system"

    class Gen:
        def is_configured(self):
            return True

        async def generate_structured(self, role, system, user, images=None):
            from app.providers.generation.base import GenerationResult
            return {"items": [{"i": 0, "verdict": "key_wrong", "read": "mokhtalefin"},
                              {"i": 1, "verdict": "maybe", "read": "x"},
                              {"i": 2, "verdict": "key_wrong", "read": "123"}]}, \
                GenerationResult(text="{}", model="m", provider="p")

    monkeypatch.setattr(PR, "get_generation_provider", lambda: Gen())
    case_id = _segments(db_session, "fa").case_id
    got = asyncio.run(PR.PronunciationEditor().arbitrate(db_session, case_id, "ar", [
        {"i": i} for i in range(3)]))
    assert got == {0: {"verdict": "key_wrong", "read": "mokhtalefin"}}


def test_the_arabic_editor_is_told_how_plural_and_dual_endings_are_read():
    from app.core.prompts import prompt

    text = prompt("documentary/pronunciation/editor_system_ar")
    assert "# ADDED: plural, dual and foreign names" in text and "mukhtaliiin" in text


def test_asr_is_strict_for_persian_and_arabic_and_only_warns_on_names_and_numbers_elsewhere():
    from app.documentary.asr import compare_transcript

    cfg = ai_config.asr_check
    assert [cfg.for_language(x).mode for x in ("fa", "ar", "en", "de")] == [
        "strict", "strict", "warn", "warn"]
    script = "Then Detective Harlan found 42 boxes in the old garage near the lake today."
    plain = compare_transcript(script, "Then Detective Harlan found 42 boxes in the old garage "
                               "near the lake", "en", cfg)
    assert plain["passed"] and plain["mode"] == "warn"                # dropped words, no name/number
    wrong_name = compare_transcript(script, "Then Detective Harper found 42 boxes in the old "
                                    "garage near the lake today", "en", cfg)
    assert not wrong_name["passed"] and wrong_name["failures"] == ["name_or_number"]
    wrong_number = compare_transcript(script, "Then Detective Harlan found 14 boxes in the old "
                                      "garage near the lake today", "en", cfg)
    assert wrong_number["sensitive_diffs"]
