"""Spoken storytelling adaptation, review groups, audio director,
directed performance, music placement and the documentary mix.

Model calls are scripted fakes; FFmpeg runs for the mix (system
requirement of the documentary engine)."""
import asyncio
import copy
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from app.agents.story import _parse_sections, stored_sections
from app.core.ai_config import CONFIG_PATH, AIConfig, ai_config
from app.documentary import spoken as SP
from app.documentary.audio_director import (
    AudioDirector, director_system_prompt, validate_audio_plan,
)
from app.documentary.blueprint import latest_blueprint, validate_blueprint
from app.documentary.music import (
    DocumentaryMixer, MusicLibrary, _tone_shaping, plan_placements,
)
from app.documentary.performance import build_directed_performance
from app.providers.generation.base import GenerationResult
from test_blueprint_performance import (
    PACK, SECTIONS, _director, _good, _no_contradiction, _story,
)

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="ffmpeg is required for audio tests")


def _raw() -> dict:
    return json.loads(Path(CONFIG_PATH).read_text(encoding="utf-8"))


def _routing(raw):
    return raw["generation_providers"]["apimaster"]["routing"]


# ---------------------------------------------------------------------------
# review independence: one group per pipeline
# ---------------------------------------------------------------------------


def test_spoken_critics_never_judge_with_the_writer_model():
    writer = ai_config.model_for("spoken_writer")
    critics = {ai_config.model_for(r)
               for r in ("spoken_meaning_checker", "spoken_style_critic")}
    assert writer not in critics
    for role in ("spoken_meaning_checker", "spoken_style_critic"):
        assert writer not in ai_config.fallback_models_for(role)
    # strict group: the writer never falls back to a critic's model either
    assert not critics & set(ai_config.fallback_models_for("spoken_writer"))


def test_group_reviewer_on_its_author_model_is_a_config_error():
    raw = _raw()
    _routing(raw)["spoken_style_critic"] = _routing(raw)["spoken_writer"]
    with pytest.raises(Exception, match="also writes"):
        AIConfig.model_validate(raw)


def test_groups_are_independent_of_each_other():
    raw = _raw()
    # A story reviewer may share the spoken writer's model: it never
    # judges spoken text.
    _routing(raw)["spoken_writer"] = _routing(raw)["native_language_critic"]
    cfg = AIConfig.model_validate(raw)
    assert cfg.model_for("spoken_writer") == cfg.model_for("native_language_critic")


@pytest.mark.parametrize("mutate, match", [
    (lambda ri: ri["groups"][0]["reviewers"].append("no_such_role"), "unknown roles"),
    (lambda ri: ri["groups"][0]["authors"].append("engagement_critic"),
     "both author and reviewer"),
])
def test_group_config_errors(mutate, match):
    raw = _raw()
    mutate(raw["review_independence"])
    with pytest.raises(Exception, match=match):
        AIConfig.model_validate(raw)


def test_non_strict_group_keeps_the_authors_normal_fallback():
    raw = _raw()
    raw["review_independence"]["groups"][0]["strict"] = False
    cfg = AIConfig.model_validate(raw)
    gp = cfg.generation_provider()
    alias = gp.routing["spoken_writer"]
    expected = [gp.models[a] for a in gp.fallbacks.get(alias, []) if a != alias]
    assert cfg.fallback_models_for("spoken_writer") == expected


# ---------------------------------------------------------------------------
# spoken helpers
# ---------------------------------------------------------------------------


def test_clean_writer_output_drops_comments_to_the_user():
    raw = ("Your message had no instruction, so I assumed...\n\n---\n\n"
           "[[ACT:B01]]\n\nFirst beat.\n\n[[ACT:B02]]\n\nSecond beat.\n\n---\n\n"
           "**Changes**\n- B01: tightened")
    text, issues = SP.clean_writer_output(raw)
    assert text == "[[ACT:B01]]\n\nFirst beat.\n\n[[ACT:B02]]\n\nSecond beat."
    assert issues == ["lead_in_removed", "trailing_notes_removed"]
    assert not SP.has_meta_text(text)
    assert SP.has_meta_text("Fine.\n\n**Changes**")
    assert SP.clean_writer_output("[[ACT:B01]]\n\nClean.") == ("[[ACT:B01]]\n\nClean.", [])


def test_read_aloud_metrics():
    long = " ".join(["word"] * 30) + "."
    m = SP.read_aloud_metrics(
        f"It is understood that he left. {long}\n\nShe stayed home.", "en")
    assert m["sentences"] == 3 and len(m["long_sentences"]) == 1
    assert m["long_sentence_share"] == round(1 / 3, 3)
    assert "It is understood" in m["stiff_phrases"]
    ar = SP.read_aloud_metrics("تمّ تفتيش البيت من قِبَل الشرطة. تمكّن الرجل من الهرب.", "ar")
    assert set(ar["stiff_phrases"]) == {"تمّ", "من قِبَل"}  # not "تمكّن"


@pytest.mark.parametrize("lang, needle", [
    ("en", "Here's the thing"), ("de", "Die Polizei ermittelte lange"),
    ("fa", "Polis moddathaa ruye in maajaraa"), ("ar", "حقّقت الشرطة طويلًا"),
])
def test_writer_prompt_is_native_per_language(lang, needle):
    prompt = SP.writer_system_prompt(lang)
    assert needle in prompt
    assert "REBUILD, DON'T POLISH" in prompt
    limit = ai_config.spoken.max_sentence_words[lang]
    assert f"{limit} words" in prompt
    assert "if_mentioned_keep_uncertain" in prompt
    assert ("never as a translation" in prompt) == (lang != "en")


def test_persian_script_prompt_still_available():
    prompt = SP.writer_system_prompt("fa", script="native")
    assert "پلیس مدت‌ها" in prompt and "FINGLISH" not in prompt
    fin = SP.writer_system_prompt("fa")
    assert "FINGLISH" in fin and "khunevaade" in fin and "molk" in fin


def test_beat_sections_follow_the_blueprint():
    bp = validate_blueprint(_good(), SECTIONS, PACK)[0]
    beats = SP.beat_sections(SECTIONS, bp)
    assert [b["id"] for b in beats] == ["B01", "B02", "B03", "B04", "B05"]
    assert beats[1]["text"].split()[0] == "a2" and "a3" in beats[1]["text"]
    assert beats[3]["text"].startswith("b1")


# ---------------------------------------------------------------------------
# SpokenNarrator with scripted models
# ---------------------------------------------------------------------------


def _told(text: str, extra: str = "") -> str:
    """Same facts 'told': short sentences, same word count per paragraph."""
    out = []
    for para in [p for p in text.split("\n\n") if p.strip()]:
        n = len(para.split())
        tag = para.split()[0]
        sents = [f"{tag} walked to the old house." for _ in range(max(n // 6, 1))]
        out.append(" ".join(sents))
    if extra:
        out[-1] += " " + extra
    return "\n\n".join(out)


class SpokenGen:
    """Writer: tells each beat; can open with a comment to the 'user' once.
    Meaning check: always fine. Style critic: verdicts from a script
    (per call), default storyteller."""

    def __init__(self, style_rounds=(), lead_in=False, short=False):
        self.style_rounds = list(style_rounds)
        self.lead_in = lead_in
        self.short = short
        self.calls: list[tuple[str, str, str]] = []

    def is_configured(self):
        return True

    @staticmethod
    def _payload(user: str) -> dict:
        return json.loads(user.split("INPUT:\n", 1)[1] if "INPUT:\n" in user else user)

    async def generate_text(self, role, system, user):
        self.calls.append((role, system, user))
        payload = self._payload(user)
        if "beats" in payload:  # repair
            pairs = [(b["beat_id"], _told(b["source"], "That part is fixed now."))
                     for b in payload["beats"]]
        else:
            pairs = [(s["id"], _told(s["text"]))
                     for s in _parse_sections(payload["script"])]
        if self.short:
            pairs = [(i, " ".join(t.split()[: len(t.split()) // 2]) + ".") for i, t in pairs]
        text = "\n\n".join(f"[[ACT:{i}]]\n\n{t}" for i, t in pairs)
        if self.lead_in:
            self.lead_in = False
            text = f"Sure! Here is the narration.\n\n{text}\n\n---\n\n**Changes**\n- all"
        return GenerationResult(text=text, model="m/spoken-writer", provider="fake")

    async def generate_structured(self, role, system, user):
        self.calls.append((role, system, user))
        payload = json.loads(user)
        if role == "spoken_meaning_checker":
            data = {"beats": [{"beat_id": b["beat_id"], "missing": [], "added": [],
                               "changed": [], "certainty": []}
                              for b in payload["beats"]]}
        else:
            ids = [s["id"] for s in _parse_sections(payload["narration"])]
            verdicts = self.style_rounds.pop(0) if self.style_rounds else {}
            beats = []
            for i in ids:
                v = verdicts.get(i, "storyteller")
                problems = [] if v == "storyteller" else [
                    {"quote": "walked", "why": "stiff", "suggestion": "went"}]
                beats.append({"beat_id": i, "verdict": v, "problems": problems})
            worst = ("newsreader" if "newsreader" in verdicts.values() else
                     "mixed" if verdicts else "storyteller")
            data = {"overall": worst, "beats": beats, "notes": "ok"}
        return data, GenerationResult(text="{}", model=f"m/{role}", provider="fake")


def _with_blueprint(db, monkeypatch):
    case, v = _story(db)
    _director(monkeypatch, [_no_contradiction(_good())])
    from app.documentary.blueprint import NarrativeDirector
    asyncio.run(NarrativeDirector().create(db, case, v))
    return case, v


def _narrate(db, monkeypatch, gen, language="en", case_v=None):
    case, v = case_v or _with_blueprint(db, monkeypatch)
    monkeypatch.setattr("app.documentary.spoken.get_generation_provider", lambda: gen)
    return case, v, asyncio.run(SP.SpokenNarrator().create(db, case, v, language))


def test_spoken_version_is_told_beat_by_beat(db_session, monkeypatch):
    gen = SpokenGen(style_rounds=[{"B02": "mixed"}], lead_in=True)
    case, master, sv = _narrate(db_session, monkeypatch, gen)
    notes = json.loads(sv.critic_notes)
    spoken = notes["spoken"]
    assert sv.kind == "spoken" and sv.master_version_id == master.id
    assert [s["id"] for s in stored_sections(sv)] == ["B01", "B02", "B03", "B04", "B05"]
    assert notes["quality_gates"] == {"pass": True, "failures": []}
    # the writer's comment to the 'user' never reaches the narration
    assert "Sure!" not in sv.story_text and "**" not in sv.story_text
    assert spoken["write_log"] == ["meta_output_retry_B01"]
    # the mixed beat was repaired and re-checked
    assert spoken["repair_iterations"] == 1
    assert spoken["per_beat"]["B02"]["verdict"] == "storyteller"
    assert "That part is fixed now." in stored_sections(sv)[1]["text"]
    writer_msgs = [u for r, _, u in gen.calls if r == "spoken_writer"]
    assert all(u.startswith("TASK:") for u in writer_msgs)
    repair = json.loads(writer_msgs[-1].split("INPUT:\n", 1)[1])
    assert [b["beat_id"] for b in repair["beats"]] == ["B02"]
    assert repair["beats"][0]["style_problems"][0]["suggestion"] == "went"
    assert sv.native_quality_score == 100.0
    struct = json.loads(sv.narrative_structure)
    assert struct["blueprint_id"] == latest_blueprint(db_session, master.id).id


def test_newsreader_tone_fails_the_gates(db_session, monkeypatch):
    stubborn = {"B03": "newsreader", "B04": "newsreader"}
    gen = SpokenGen(style_rounds=[stubborn, stubborn, stubborn])
    _, _, sv = _narrate(db_session, monkeypatch, gen)
    gates = json.loads(sv.critic_notes)["quality_gates"]
    assert not gates["pass"]
    assert {"newsreader_tone", "not_enough_storytelling"} <= set(gates["failures"])
    spoken = json.loads(sv.critic_notes)["spoken"]
    assert spoken["repair_iterations"] == ai_config.spoken.max_repair_iterations


def test_lost_content_fails_the_duration_gate(db_session, monkeypatch):
    _, _, sv = _narrate(db_session, monkeypatch, SpokenGen(short=True))
    assert "duration_out_of_range" in json.loads(sv.critic_notes)["quality_gates"]["failures"]


def test_spoken_needs_a_blueprint_and_a_configured_language(db_session, monkeypatch):
    case, v = _story(db_session)
    gen = SpokenGen()
    monkeypatch.setattr("app.documentary.spoken.get_generation_provider", lambda: gen)
    with pytest.raises(RuntimeError, match="blueprint"):
        asyncio.run(SP.SpokenNarrator().create(db_session, case, v, "en"))
    with pytest.raises(ValueError, match="not configured"):
        asyncio.run(SP.SpokenNarrator().create(db_session, case, v, "xx"))
    assert gen.calls == []


def test_spoken_blueprint_makes_every_beat_an_act(db_session, monkeypatch):
    _, _, sv = _narrate(db_session, monkeypatch, SpokenGen())
    bp = SP.spoken_blueprint(db_session, sv)
    assert [(b["id"], b["act_id"]) for b in bp["beats"]] == [
        (f"B0{i}", f"B0{i}") for i in range(1, 6)]
    assert all(b["paragraphs"][0] == 1 for b in bp["beats"])


# ---------------------------------------------------------------------------
# audio director: validator + create
# ---------------------------------------------------------------------------


def _bp():
    return validate_blueprint(_good(), SECTIONS, PACK)[0]


def _plan_item(bid, kind, seconds=None, **kw):
    return {"beat_id": bid, "paragraph_breath": "normal", "bed": "mystery",
            "bed_level": "very_low",
            "after": {"type": kind, "seconds": seconds, "mood": kw.get("mood", "mystery")}}


def test_audio_plan_structural_rules_and_clamps():
    raw = {"beats": [
        _plan_item("B01", "sting", 3),              # hook is not a turn
        _plan_item("B02", "emotional_moment", 30),  # load medium: ok, clamped
        _plan_item("B03", "end"),                   # end only at the last beat
        _plan_item("B04", "breath", 0.2),           # clamped up
        _plan_item("B05", "breath", 1),             # last beat ends the film
    ]}
    plan, rep = validate_audio_plan(raw, _bp())
    after = {pb["beat_id"]: pb["after"] for pb in plan["beats"]}
    lo, hi = ai_config.audio_direction.transitions["emotional_moment"]
    assert after["B01"]["type"] == "breath"
    assert after["B02"]["type"] == "emotional_moment" and after["B02"]["seconds"] == hi
    assert after["B03"]["type"] == "breath"
    assert after["B04"]["seconds"] == ai_config.audio_direction.transitions["breath"][0]
    assert after["B05"] == {"type": "end", "seconds": 0.0, "mood": "mystery"}
    reasons = {a.get("reason") for a in rep["adjustments"]}
    assert {"sting_needs_a_turn", "end_only_at_last_beat", "last_beat_ends_film"} <= reasons
    assert rep["status"] == "valid"


def test_audio_plan_missing_beats_and_empty_plans():
    plan, rep = validate_audio_plan({"beats": [_plan_item("B01", "breath", 1)]}, _bp())
    assert rep["status"] == "needs_review"
    assert {w["code"] for w in rep["warnings"]} == {"beat_missing_in_plan"}
    assert plan["beats"][1]["after"]["type"] == "breath"
    _, rep = validate_audio_plan({}, _bp())
    assert rep["status"] == "invalid" and rep["errors"][0]["code"] == "no_plan"


def test_music_moments_stay_special():
    cfg = ai_config.audio_direction.model_copy(update={
        "min_seconds_between_music_moments": 1000.0, "max_music_only_share": 0.5})
    raw = {"beats": [_plan_item("B01", "music_bridge", 5),
                     _plan_item("B02", "music_bridge", 5),   # too soon
                     _plan_item("B03", "music_bridge", 5),   # reveal: protected
                     _plan_item("B04", "breath", 1), _plan_item("B05", "end")]}
    plan, _ = validate_audio_plan(raw, _bp(), cfg)
    kinds = [pb["after"]["type"] for pb in plan["beats"]]
    assert kinds == ["music_bridge", "breath", "music_bridge", "breath", "end"]


def test_director_prompt_asks_for_rhythm():
    prompt = director_system_prompt()
    assert "three or four minutes" in prompt and "scene changes" in prompt


class PlanGen:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def is_configured(self):
        return True

    async def generate_structured(self, role, system, user):
        self.calls.append((role, json.loads(user)))
        return copy.deepcopy(self.outputs.pop(0)), GenerationResult(
            text="{}", model="m/audio", provider="fake")


def _good_plan():
    return {"notes": "arc", "beats": [
        _plan_item("B01", "breath", 1.2), _plan_item("B02", "breath", 1.2),
        _plan_item("B03", "sting", 2.5), _plan_item("B04", "music_bridge", 5),
        _plan_item("B05", "end")]}


def test_audio_director_repairs_an_empty_plan(db_session, monkeypatch):
    case, v = _with_blueprint(db_session, monkeypatch)
    gen = PlanGen([{}, _good_plan()])
    monkeypatch.setattr("app.documentary.audio_director.get_generation_provider",
                        lambda: gen)
    row = asyncio.run(AudioDirector().create(
        db_session, case, latest_blueprint(db_session, v.id)))
    assert [r for r, _ in gen.calls] == ["audio_director", "audio_director"]
    assert gen.calls[1][1]["errors_to_fix"][0]["code"] == "no_plan"
    assert row.status == "valid" and row.version == 1
    assert json.loads(row.validation_json)["repair_iterations"] == 1


# ---------------------------------------------------------------------------
# directed performance
# ---------------------------------------------------------------------------


def test_directed_performance_breathes_and_follows_the_plan():
    plan = {"beats": [
        _plan_item("B01", "breath", 0.5),            # older plan: clamped up
        _plan_item("B02", "breath", 1.2),
        _plan_item("B03", "music_bridge", 5),
        _plan_item("B04", "silence", 2.5),
        _plan_item("B05", "end"),
    ]}
    plan["beats"][1]["bed"] = "tension"
    script = build_directed_performance(SECTIONS, "en", _bp(), plan)
    blocks = script["blocks"]
    by_beat = {}
    for b in blocks:
        by_beat.setdefault(b["beats"][0]["beat_id"], []).append(b)
    breath_lo = ai_config.audio_direction.transitions["breath"][0]
    jitter = ai_config.performance.pause_jitter
    b01_end = by_beat["B01"][-1]
    assert b01_end["pause_after_kind"] == "breath"
    assert b01_end["transition"]["seconds"] == breath_lo
    assert breath_lo * 1000 * (1 - jitter) - 1 <= b01_end["pause_after_ms"] \
        <= breath_lo * 1000 * (1 + jitter) + 1
    # B02 spans two paragraphs (each ~24 s): a speaker's breath between
    normal = ai_config.audio_direction.paragraph_breath_ms["normal"]
    para = [b for b in by_beat["B02"] if b["pause_after_kind"] == "paragraph"]
    assert para and all(normal * (1 - jitter) - 1 <= b["pause_after_ms"]
                        <= normal * (1 + jitter) + 1 for b in para)
    bridge_lo = ai_config.audio_direction.transitions["music_bridge"][0]
    assert by_beat["B03"][-1]["pause_after_kind"] == "music_bridge"
    assert by_beat["B03"][-1]["pause_after_ms"] == int(max(5.0, bridge_lo) * 1000)
    assert by_beat["B04"][-1]["pause_after_kind"] == "silence"
    assert blocks[-1]["pause_after_kind"] == "end" and blocks[-1]["pause_after_ms"] == 0
    assert script["directed"] and script["beat_audio"]["B02"]["bed"] == "tension"
    # no word lost or added
    told = " ".join(b["text"] for b in blocks).split()
    assert told == " ".join(s["text"] for s in SECTIONS).split()


# ---------------------------------------------------------------------------
# music placement + mix
# ---------------------------------------------------------------------------


def _timeline():
    return {"duration_seconds": 100.0, "timeline": {"beats": [
        {"beat_id": "B01", "start": 0.0, "end": 20.0},
        {"beat_id": "B02", "start": 21.0, "end": 40.0},
        {"beat_id": "B03", "start": 46.0, "end": 70.0},
        {"beat_id": "B04", "start": 72.5, "end": 90.0},
        {"beat_id": "B05", "start": 93.0, "end": 100.0},
    ]}}


def _beat_audio():
    return {"beat_audio": {
        "B01": {"bed": "mystery", "bed_level": "very_low",
                "after": {"type": "breath", "seconds": 1.0}},
        "B02": {"bed": "mystery", "bed_level": "very_low",
                "after": {"type": "music_bridge", "seconds": 6.0, "mood": "mystery"}},
        "B03": {"bed": "none", "bed_level": "very_low",
                "after": {"type": "silence", "seconds": 2.5}},
        "B04": {"bed": "tension", "bed_level": "low",
                "after": {"type": "sting", "seconds": 3.0, "mood": "tension"}},
        "B05": {"bed": "none", "after": {"type": "end", "seconds": 0.0}},
    }}


def test_plan_placements():
    places = plan_placements(_timeline(), _beat_audio())
    roles = [(p["role"], p.get("beats") or p.get("after_beat")) for p in places]
    assert roles == [("bed", ["B01", "B02"]), ("music_bridge", "B02"),
                     ("silence", "B03"), ("bed", ["B04"]), ("sting", "B04")]
    bed, bridge, silence, bed2, sting = places
    assert bed["start"] == 0.0 and bed["duration"] == pytest.approx(40.6)
    lv = ai_config.audio_direction.bed_levels_db
    assert bed["level_db"] == lv["very_low"] and bed2["level_db"] == lv["low"]
    # the bridge starts softly under the last words and fades under the next
    lead = ai_config.audio_direction.music_lead_seconds
    tail = ai_config.audio_direction.music_tail_seconds
    assert bridge["start"] == pytest.approx(40.0 - lead)
    assert bridge["duration"] == pytest.approx(6.0 + lead + tail)
    assert silence["level_db"] == ai_config.audio_direction.room_tone_level_db
    assert silence["duration"] == pytest.approx(2.5)
    assert sting["start"] == 90.0 and sting["fade_in"] < 0.1


def test_tone_shaping_leaves_room_for_the_voice():
    assert "equalizer=f=2500" in _tone_shaping("bed")
    assert "lowpass" in _tone_shaping("silence")
    assert _tone_shaping("music_bridge") == ""


def _sine_mp3(seconds: float, freq: int) -> bytes:
    out = Path(f"/tmp/cue_{uuid.uuid4().hex}.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={seconds}:sample_rate=44100",
                    "-ac", "2", "-c:a", "libmp3lame", "-b:a", "128k", str(out)],
                   check=True)
    data = out.read_bytes()
    out.unlink()
    return data


class FakeSound:
    def __init__(self):
        self.calls = []

    async def generate(self, prompt, seconds, loop, influence):
        self.calls.append((prompt, seconds, loop))
        return _sine_mp3(seconds, 330 if loop else 550), int(seconds * 11)


@needs_ffmpeg
def test_documentary_mix(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.music_library, "dir", str(tmp_path / "lib"))
    out = tmp_path / "v1"
    out.mkdir()
    sr = ai_config.loudness.sample_rate
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"sine=frequency=200:duration=30:sample_rate={sr}",
                    "-ac", "1", "-c:a", "pcm_s16le", str(out / "narration.wav")],
                   check=True)
    manifest = {"duration_seconds": 30.0, "timeline": {"beats": [
        {"beat_id": "B01", "start": 0.0, "end": 12.0},
        {"beat_id": "B02", "start": 17.0, "end": 30.0}]}}
    script = {"beat_audio": {
        "B01": {"bed": "mystery", "bed_level": "very_low",
                "after": {"type": "music_bridge", "seconds": 5.0, "mood": "mystery"}},
        "B02": {"bed": "none", "after": {"type": "end", "seconds": 0.0}}}}
    sound = FakeSound()
    mixer = DocumentaryMixer(MusicLibrary(provider=sound))
    result = asyncio.run(mixer.mix(manifest, script, out))
    from app.documentary import audio as A

    assert [p["role"] for p in result["placements"]] == ["bed", "music_bridge"]
    assert result["music_characters_paid"] > 0 and len(sound.calls) == 2
    final = out / "documentary.wav"
    assert final.exists() and (out / "documentary.mp3").exists()
    assert A.probe_duration(final) == pytest.approx(30.0, abs=0.2)
    assert result["loudness_lufs"] == pytest.approx(
        ai_config.loudness.narration_target_lufs, abs=1.0)
    # cues are generated once and shared
    again = asyncio.run(mixer.mix(manifest, script, out))
    assert again["music_characters_paid"] == 0 and len(sound.calls) == 2


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_spoken_and_audio_plan_api(client, db_session, monkeypatch):
    case, v = _story(db_session)
    base = f"/api/cases/{case.id}/stories/{v.id}"
    assert client.post(base + "/spoken?language=en").status_code == 409
    assert client.post(base + "/audio-plan").status_code == 409
    assert client.get(base + "/audio-plan").status_code == 404

    _director(monkeypatch, [_no_contradiction(_good())])
    assert client.post(base + "/blueprint").status_code == 200
    gen = PlanGen([_good_plan()])
    monkeypatch.setattr("app.documentary.audio_director.get_generation_provider",
                        lambda: gen)
    r = client.post(base + "/audio-plan")
    assert r.status_code == 200, r.text
    plan_id = r.json()["id"]
    assert client.get(base + "/audio-plan").json()["id"] == plan_id

    monkeypatch.setattr("app.documentary.spoken.get_generation_provider",
                        lambda: SpokenGen())
    r = client.post(base + "/spoken?language=en")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "spoken" and body["spoken_checks"]["beats"] == 5
    # a spoken version shares its blueprint's audio plan
    sp = f"/api/cases/{case.id}/stories/{body['id']}"
    assert client.get(sp + "/audio-plan").json()["id"] == plan_id
    perf = client.get(sp + "/performance").json()
    assert perf["directed"] is True
