"""The host's voice goes through the same speech check as the story's blocks, and a check that failed or could not run is a flag for a person."""
import asyncio
import json
from types import SimpleNamespace

from test_studio import FakeVoice, _renderer, _segments, _silence_mp3

from app.core.ai_config import ai_config


class CheckingRenderer:
    """Stands in for VoiceRenderer.speak_block: records the block it gets and returns the take a real run would (audio, timing, the check's result)."""

    def __init__(self, tmp_path, asr_passed=True):
        self.blocks, self.tmp, self.asr_passed = [], tmp_path, asr_passed
        self.asr = SimpleNamespace(name="fake-whisper")

    async def speak_block(self, block, language, vcfg, settings, seed, blocks_dir):
        self.blocks.append(block)
        raw = self.tmp / f"take{len(self.blocks)}.mp3"
        raw.write_bytes(_silence_mp3(3.0))
        text = block["text"]
        n = len(text)
        take = {"raw_path": str(raw), "text": text, "request_id": "r1", "character_cost": n,
                "alignment": {"characters": list(text), "starts": [i * .05 for i in range(n)],
                              "ends": [(i + 1) * .05 for i in range(n)]},
                "asr": {"passed": self.asr_passed, "word_error_rate": 0.0 if self.asr_passed else .5,
                        "failures": [], "heard_text": "x"}}
        return {"take": take, "attempts": 1}


def _fa_scene(db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import plan_host_scenes

    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.semantic_review, "languages", [])
    row = _segments(db_session, "fa")
    return plan_host_scenes(db_session, row)[0]


def test_the_host_voice_is_checked_and_its_text_stays_frozen(db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import run_scene

    scene = _fa_scene(db_session, tmp_path, monkeypatch)
    text, sha = scene.text, scene.text_sha256
    renderer = CheckingRenderer(tmp_path)
    asyncio.run(run_scene(db_session, scene, "voice", renderer=renderer))
    block = renderer.blocks[0]
    assert [s["speech"] for s in block["sentences"]] == ["Guten Abend.", "Heute geht es um eine Tür."]
    checks = json.loads(scene.voice_checks_json)
    assert checks["asr"]["passed"] is True and checks["flags"] == [] and checks["asr_engine"] == "fake-whisper"
    assert scene.text == text and scene.text_sha256 == sha
    assert scene.voice_seconds and scene.status == "voice_ready"


def test_a_failed_whisper_check_becomes_a_flag_for_a_person(db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import run_scene, scene_dict

    scene = _fa_scene(db_session, tmp_path, monkeypatch)
    asyncio.run(run_scene(db_session, scene, "voice", renderer=CheckingRenderer(tmp_path, asr_passed=False)))
    flags = json.loads(scene.voice_checks_json)["flags"]
    assert flags == ["asr_check_failed"]
    body = scene_dict(scene)
    assert body["voice_flags"] == flags and body["voice_checks"]["asr"]["passed"] is False


def test_a_check_that_could_not_run_is_a_flag_not_a_pass(db_session, tmp_path, monkeypatch):
    from app.documentary.host_scenes import run_scene

    scene = _fa_scene(db_session, tmp_path, monkeypatch)
    renderer = CheckingRenderer(tmp_path)
    renderer.asr = None
    asyncio.run(run_scene(db_session, scene, "voice", renderer=renderer))
    assert json.loads(scene.voice_checks_json)["flags"] == ["asr_unavailable"]


def test_a_real_renderer_speaks_the_host_block_through_the_story_path(db_session, tmp_path, monkeypatch):
    """The same speak_block as the story's blocks: a fake provider, no Whisper."""
    from app.documentary.host_scenes import run_scene

    scene = _fa_scene(db_session, tmp_path, monkeypatch)
    voice = FakeVoice()
    asyncio.run(run_scene(db_session, scene, "voice", renderer=_renderer(voice)))
    assert voice.calls == 1 and scene.voice_seconds > 0
    checks = json.loads(scene.voice_checks_json)
    assert checks["asr"] is None and "asr_unavailable" in checks["flags"]


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


def test_the_writer_and_the_critic_read_the_same_register_note_of_a_language():
    from app.core.ai_config import ai_config
    from app.documentary.host import critic_system_prompt, register_of, writer_system_prompt

    note = ai_config.host.language_rules["fa"].register_note
    assert note and "می‌دونی" in note and "شما" in note
    for language in ("fa", "ar", "de"):
        assert ai_config.host.language_rules[language].register_note in writer_system_prompt(language)
        assert ai_config.host.language_rules[language].register_note in critic_system_prompt(language)
    assert register_of("en") == "" and "register of" not in writer_system_prompt("en") and "register of" not in critic_system_prompt("en")
