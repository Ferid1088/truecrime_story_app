"""The voice
performance director (ElevenLabs v3 audio tags + tension arc), tag-aware
voice rendering and subtitles, and parallel execution (limits, batches,
partial jobs).

Model calls are scripted fakes."""
import asyncio
import json
import re

import pytest

from app.core.ai_config import ai_config
from app.core.concurrency import gather_limited, limiter, slot, usage
from app.documentary import spoken as SP
from app.documentary import voice_performance as VP
from app.documentary.asr import compare_transcript, persian_tokens
from app.documentary.performance import attach_speech, build_directed_performance
from app.documentary.production.script import display_words, sentence_subtitles
from app.documentary.voice_render import (
    VoiceRenderer, beat_times, sentence_times, words_from_alignment,
)
from app.providers.generation.base import GenerationResult
from app.providers.voice import VoiceRequest
from test_spoken_audio import SpokenGen, _good_plan, _with_blueprint

PERSIAN = re.compile(r"[؀-ۿ]")


def _res(role):
    return GenerationResult(text="{}", model=f"m/{role}", provider="fake")


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


def test_all_languages_use_v3_with_the_new_voices():
    want = {"en": "UF84IGrTBtegPkgbbrS2", "de": "02KhC7wycOLwuF6sc5Qu",
            "fa": "I3gMKh0nwZ8NQXKqUg6F", "ar": "EFlRMcr2Nd9ah6iW85Z4"}
    for lang, voice in want.items():
        cfg = ai_config.voice.for_language(lang)
        assert cfg.voice_id == voice and cfg.model_id == "eleven_v4"
        assert cfg.language_code == lang
    for lv in "0123":
        assert ai_config.voice_performance.level_styles[lv] in ai_config.voice.styles


def test_elevenlabs_body_language_code_and_context():
    from app.providers.voice.elevenlabs import ElevenLabsVoiceProvider

    p = object.__new__(ElevenLabsVoiceProvider)
    p.cfg = ai_config.voice.elevenlabs
    v3 = p._body(VoiceRequest(text="[pause] Salaam.", voice_id="x", model_id="eleven_v4",
                              settings={}, previous_text="a", language_code="fa"))
    assert v3["language_code"] == "fa" and "previous_text" not in v3
    v2 = p._body(VoiceRequest(text="Hi.", voice_id="x", model_id="eleven_multilingual_v2",
                              settings={}, previous_text="a", language_code="en"))
    assert "language_code" not in v2 and v2["previous_text"] == "a"


def test_elevenlabs_waits_when_too_many_requests_run(monkeypatch):
    from app.providers.voice.elevenlabs import ElevenLabsVoiceProvider

    p = object.__new__(ElevenLabsVoiceProvider)
    p.cfg = ai_config.voice.elevenlabs
    p.api_key = "k"
    calls = []

    async def post(url, body):
        calls.append(1)
        if len(calls) < 4:
            return 429, {"detail": {"status": "concurrent_limit_exceeded",
                                    "message": "Too many concurrent requests"}}, {}
        return 200, {"audio_base64": "AAAA", "alignment": {
            "characters": ["a"], "character_start_times_seconds": [0.0],
            "character_end_times_seconds": [0.1]}}, {}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(p, "_post", post)
    monkeypatch.setattr("app.providers.voice.elevenlabs.asyncio.sleep", no_sleep)
    res = asyncio.run(p.synthesize(VoiceRequest(text="a", voice_id="v", model_id="eleven_v4",
                                                settings={})))
    assert res.characters == ["a"] and len(calls) == 4  # more than max_retries


# ---------------------------------------------------------------------------
# concurrency
# ---------------------------------------------------------------------------


def test_limits_cap_parallel_work(monkeypatch):
    monkeypatch.setattr(ai_config.concurrency, "llm", 2)
    peak = {"now": 0, "max": 0}

    async def work(i):
        async with slot("llm"):
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
            await asyncio.sleep(0.01)
            peak["now"] -= 1
            return i

    async def main():
        out = await gather_limited(None, [work(i) for i in range(7)])
        assert usage()["llm"] == {"active": 0, "limit": 2, "configured": 2, "throttled": False}
        assert limiter("llm") is limiter("llm")
        return out

    assert asyncio.run(main()) == list(range(7))
    assert peak["max"] == 2


def test_gather_limited_returns_errors_but_not_control_flow():
    class Stop(BaseException):
        pass

    async def ok():
        return 1

    async def bad():
        raise ValueError("x")

    async def stop():
        raise Stop()

    res = asyncio.run(gather_limited("llm", [ok(), bad()], return_exceptions=True))
    assert res[0] == 1 and isinstance(res[1], ValueError)
    with pytest.raises(Stop):
        asyncio.run(gather_limited(None, [ok(), stop()], return_exceptions=True))


# ---------------------------------------------------------------------------
# Persian speech-to-text normalization
# ---------------------------------------------------------------------------


def test_persian_normalization_ignores_spelling_not_speech():
    script = "اینگا در سال دو هزار و پانزده ناپدید شد. خانه‌ی آن‌ها می‌گفتند کتاب‌ها"
    heard = "اینگا در سال ۲۰۱۵ ناپدید شد خانه آنها میگفتند کتاب ها"
    assert persian_tokens(script) == persian_tokens(heard)
    assert compare_transcript(script, heard, "fa")["word_error_rate"] == 0.0
    # a really missing phrase is still caught
    bad = compare_transcript(script, "اینگا در سال ۲۰۱۵", "fa")
    assert not bad["passed"]
    # "نه" (no) alone is a word, inside a number it is nine
    assert persian_tokens("نه، او نرفت") [0] == "نه"
    assert persian_tokens("بیست و نه سال") == ["29", "سال"]


# ---------------------------------------------------------------------------
# voice performance: validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tag", ["whispers", "whispering", "hushed", "barely audible", "softly",
                                 "quietly", "gasps", "fearful", "ominous"])
def test_the_narrator_never_whispers_or_overacts_at_any_level(tag):
    for level in range(4):
        assert VP.validate_line("She was gone.", f"[{tag}] She was gone.", level, "en")[0] == "She was gone."


def test_scrub_forbidden_removes_whispers_from_a_stored_performance():
    text = "[slowly] And then [whispering, slowly] she was gone [hushed] , gone. [pause] Nothing."
    assert VP.scrub_forbidden(text) == "[slowly] And then she was gone, gone. [pause] Nothing."


def test_line_validator_keeps_words_and_palette():
    v = VP.validate_line
    tts, issues = v("And then she was gone.", "[slowly] And then... [deliberate] she was GONE.",
                    3, "en")
    assert tts == "[slowly] And then... [deliberate] she was GONE." and not issues
    # forbidden tags and tags at the end are removed
    tts, issues = v("She was gone.", "[laughs] She was gone [pause].", 3, "en")
    assert tts == "She was gone." and {i.split(":")[0] for i in issues} == {
        "tag_removed", "trailing_tag"}
    # a level-0 sentence cannot slow down
    tts, _ = v("It was May.", "[slowly] It was May.", 0, "en")
    assert tts == "It was May."
    # changed words -> the plain sentence (+ its first valid tag)
    tts, issues = v("And then she was gone.", "[slowly] And then she vanished.", 3, "en")
    assert tts == "[slowly] And then she was gone." and "words_changed" in issues
    # a question stays a question
    assert v("Did she run?", "[serious] Did she run.", 2, "en")[0] == "[serious] Did she run?"
    # emphasis capitals: English only, not at level 0, one per sentence
    assert "words_changed" in v("Va baad Inga raft.", "Va baad Inga RAFT.", 2, "fa")[1]
    assert "words_changed" in v("It was May.", "It was MAY.", 0, "en")[1]
    assert "too_much_emphasis" in v("She was gone.", "SHE was GONE.", 2, "en")[1]
    # at most N tags; combined tags allowed when every part is on the palette
    tts, issues = v("Then the door opened slowly.",
                    "[pause] Then [measured, slowly] the door [deliberate] opened slowly.", 3, "en")
    assert tts.count("[") == 2 and any(i.startswith("too_many_tags") for i in issues)
    assert v("Then it rang.", "[measured, giggles] Then it rang.", 3, "en")[0] == "Then it rang."
    # too many ellipses
    assert "too_many_ellipses" in v("So it ended.", "So... it... ended...", 1, "en")[1]


def _beats(*purposes):
    return [{"id": f"B0{i + 1}", "purpose": p, "emotional_load": "high",
             "mystery_intensity": "high"} for i, p in enumerate(purposes)]


def test_arc_starts_neutral_and_keeps_climaxes_rare():
    beats = _beats("hook", "orientation", "timeline", "reveal", "recovery", "evidence",
                   "reveal", "chapter_end", "timeline", "reveal")
    raw = {"incident_beat": "B03", "beats": [
        {"beat_id": b["id"], "level": 3, "peak": 3} for b in beats]}
    arc, incident, log = VP.validate_arc(beats, raw)
    assert incident == "B03"
    assert arc["B01"] == {"level": 0, "peak": 0} and arc["B02"] == {"level": 0, "peak": 0}
    assert arc["B03"]["level"] == 2  # no jump from 0 to 3
    assert arc["B05"]["peak"] <= 1  # recovery breathes
    assert sum(1 for a in arc.values() if a["peak"] == 3) == 2  # 20 % of 10 beats
    # unknown incident -> guessed after the set-up
    arc2, inc2, log2 = VP.validate_arc(beats[:3], {})
    assert inc2 == "B03" and "incident_guessed" in log2 and arc2["B01"]["level"] == 0


def test_sentence_levels_follow_the_arc_and_tags_stay_sparse():
    arc = {"B01": {"level": 0, "peak": 0}, "B02": {"level": 2, "peak": 3}}
    recs = [{"beat_id": "B01", "level": 3} for _ in range(5)] + [
        {"beat_id": "B02", "level": 3} for _ in range(20)]
    VP.enforce_levels(recs, arc)
    assert all(r["level"] == 0 for r in recs[:5])
    assert sum(1 for r in recs if r["level"] == 3) == int(0.12 * 25)
    assert all(r["level"] >= 1 for r in recs[5:])
    tagged = [{"level": 0, "tts": "[pause] Hello."} for _ in range(10)]
    removed = VP.thin_tags(tagged)
    assert removed == 8 and sum(1 for r in tagged if "[" in r["tts"]) == 2


# ---------------------------------------------------------------------------
# voice performance: the director
# ---------------------------------------------------------------------------


class PerformanceGen:
    def __init__(self, change_words_for=()):
        self.calls: list[tuple[str, dict]] = []
        self.change = set(change_words_for)

    def is_configured(self):
        return True

    async def generate_structured(self, role, system, user):
        assert role == "voice_performance_director"
        if "INPUT:\n" not in user:  # arc
            payload = json.loads(user)
            self.calls.append(("arc", payload))
            return {"incident_beat": "B02", "beats": [
                {"beat_id": b["beat_id"], "level": 2, "peak": 3}
                for b in payload["beats"]]}, _res(role)
        payload = json.loads(user.split("INPUT:\n", 1)[1])
        self.calls.append(("sentences", payload))
        assert "THE WORDS NEVER CHANGE" in system
        out = []
        for b in payload["beats"]:
            for s in b["sentences"]:
                text = s["text"]
                if s["i"] in self.change:
                    text = text.replace("old", "new")
                tag = "[slowly]" if b["arc_peak"] == 3 else "[serious]"
                out.append({"i": s["i"], "level": b["arc_peak"], "tts": f"{tag} {text}"})
        return {"sentences": out}, _res(role)


def _spoken_en(db, monkeypatch):
    gen = SpokenGen()
    case, master = _with_blueprint(db, monkeypatch)
    monkeypatch.setattr("app.agents.spoken.get_generation_provider", lambda: gen)
    sv = asyncio.run(SP.SpokenNarrator().create(db, case, master, "en"))
    return case, master, sv


def test_director_builds_the_arc_and_validates_every_sentence(db_session, monkeypatch):
    case, master, sv = _spoken_en(db_session, monkeypatch)
    gen = PerformanceGen(change_words_for={4})
    monkeypatch.setattr("app.agents.voice_performance.get_generation_provider", lambda: gen)
    bp = SP.spoken_blueprint(db_session, sv)
    row = asyncio.run(VP.VoicePerformanceDirector().create(
        db_session, case, sv, bp, beat_ids=["B01", "B02"]))
    data = json.loads(row.performance_json)
    assert row.status == "partial"  # a pilot: only the first beats
    assert data["incident_beat"] == "B02"
    b1, b2 = data["beats"][0], data["beats"][1]
    assert b1["arc_level"] == 0  # before the incident: neutral
    recs1 = [r for p in b1["paragraphs"] for r in p]
    assert all(r["level"] == 0 and "[slowly]" not in r["tts"] for r in recs1)
    recs2 = [r for p in b2["paragraphs"] for r in p]
    assert all(r["directed"] for r in recs2) and any("[serious]" in r["tts"] for r in recs2)
    # only one beat in five may reach a climax: the reveal (B03) keeps it
    assert b2["arc_peak"] == 2 and data["beats"][2]["arc_peak"] == 3
    assert all(not r["directed"] for p in data["beats"][2]["paragraphs"] for r in p)
    # the sentence whose words were changed fell back to its own words
    changed = [r for b in data["beats"] for p in b["paragraphs"] for r in p
               if r.get("raw") and "new" in r["raw"]]
    assert changed and all("new" not in r["tts"] for r in changed)
    assert data["stats"]["issues"].get("words_changed", 0) >= 1
    # only the pilot beats were sent; a second call reuses everything
    sent = {b["beat_id"] for k, p in gen.calls if k == "sentences" for b in p["beats"]}
    assert sent == {"B01", "B02"}
    n = len(gen.calls)
    again = asyncio.run(VP.VoicePerformanceDirector().create(
        db_session, case, sv, bp, beat_ids=["B01"]))
    assert again.id == row.id and len(gen.calls) == n
    # the whole film later: only the missing beats are directed
    full = asyncio.run(VP.VoicePerformanceDirector().create(db_session, case, sv, bp))
    sent2 = {b["beat_id"] for k, p in gen.calls[n:] if k == "sentences" for b in p["beats"]}
    assert sent2 == {"B03", "B04", "B05"} and full.status == "ready"


def test_directed_blocks_carry_tags_display_and_level_styles(db_session, monkeypatch):
    from app.documentary.audio_director import AudioDirector
    from app.documentary.performance import performance_for_version

    case, master, sv = _spoken_en(db_session, monkeypatch)
    gen = PerformanceGen()
    monkeypatch.setattr("app.agents.voice_performance.get_generation_provider", lambda: gen)
    bp = SP.spoken_blueprint(db_session, sv)
    asyncio.run(VP.VoicePerformanceDirector().create(db_session, case, sv, bp))

    class PlanGen:
        def is_configured(self):
            return True

        async def generate_structured(self, role, system, user):
            return _good_plan(), _res(role)

    monkeypatch.setattr("app.agents.audio_plan.get_generation_provider", PlanGen)
    from app.documentary.blueprint import latest_blueprint
    asyncio.run(AudioDirector().create(db_session, case, latest_blueprint(db_session, master.id)))
    script = performance_for_version(db_session, sv)
    assert script["audio_tags"] and script["voice_performance_id"]
    tagged = [b for b in script["blocks"] if b.get("tts_text")]
    assert tagged and not script["speech_flags"]
    for b in tagged:
        assert VP.strip_tags(b["tts_text"]).split() == b["text"].split()
        assert b["style"] == ai_config.voice_performance.style_for_level(b["level"])
        assert b["beats"][0]["end_char"] == len(b["tts_text"])
    assert script["blocks"][0]["level"] == 0
    assert script["blocks"][0]["style"] == "v3_neutral"


def test_attach_speech_refuses_misaligned_records():
    blocks = [{"block_id": "X", "text": "One. Two.", "sentence_count": 2}]
    recs = [{"speech": "One.", "tts": "[pause] One.", "display": "One.", "level": 1},
            {"speech": "Three.", "tts": "Three.", "display": "Three.", "level": 1}]
    assert attach_speech(blocks, recs, True) == ["X:speech_not_aligned"]
    assert "tts_text" not in blocks[0]


# ---------------------------------------------------------------------------
# tag-aware voice timing, subtitles
# ---------------------------------------------------------------------------


def _alignment(text, per=0.05, lead=0.2):
    chars = list(text)
    starts = [lead + i * per for i in range(len(chars))]
    return {"characters": chars, "starts": starts, "ends": [s + per for s in starts]}


def test_tags_are_neither_words_nor_speech_time():
    text = "[whispers] And then... she was gone. [pause] Nothing."
    al = _alignment(text)
    words = words_from_alignment(al["characters"], al["starts"], al["ends"])
    assert [w["word"] for w in words] == ["And", "then...", "she", "was", "gone.", "Nothing."]
    assert words[0]["start"] == pytest.approx(0.2 + 11 * 0.05)
    bt = beat_times([{"beat_id": "B1", "start_char": 0, "end_char": len(text)}], text, al, 0.0)
    assert bt[0]["start"] == pytest.approx(words[0]["start"])
    sents = sentence_times(
        [{"speech": "And then she was gone.", "tts": "[whispers] And then... she was gone.",
          "display": "And then she was gone."},
         {"speech": "Nothing.", "tts": "[pause] Nothing.", "display": "Nothing."}],
        text, al, 0.1)
    assert sents[0]["start"] == pytest.approx(words[0]["start"] - 0.1)
    assert sents[1]["start"] == pytest.approx(words[-1]["start"] - 0.1)
    assert sents[1]["display"] == "Nothing."


def test_subtitles_show_the_display_text():
    sentences = [
        {"start": 1.0, "end": 5.0, "speech": "Va baad Inga napadid shod.",
         "display": "و بعد اینگا ناپدید شد."},
        {"start": 5.5, "end": 7.0, "speech": "She was gone.", "display": "She was gone."},
    ]
    words = [{"word": "She", "start": 5.5, "end": 5.8}, {"word": "was", "start": 5.9, "end": 6.2},
             {"word": "GONE.", "start": 6.3, "end": 6.9}]
    subs = sentence_subtitles(sentences, words)
    assert subs[0]["text"] == "و بعد اینگا ناپدید شد." and subs[0]["start"] == 1.0
    assert subs[1] == {"start": 5.5, "end": 6.9, "text": "She was gone."}
    long = sentence_subtitles([{"start": 0, "end": 10, "speech": "x " * 60,
                                "display": " ".join(["word"] * 60)}], [])
    assert len(long) > 1 and all(len(s["text"]) <= 84 + 5 for s in long)
    assert all(a["end"] <= b["start"] for a, b in zip(long, long[1:]))
    # a one-word tail joins the line before (no orphan subtitle)
    orphan = sentence_subtitles([{"start": 0, "end": 4, "speech": "x",
                                  "display": " ".join(["abcdefgh"] * 9) + " end."}], [])
    assert len(orphan) == 1 and orphan[0]["text"].endswith("abcdefgh end.")
    tl = {"timeline": {"sentences": sentences, "words": words}}
    dw = display_words(tl)
    assert dw[0]["word"] == "و" and dw[-1]["word"] == "gone."


def test_voice_render_sends_tags_checks_display_and_runs_in_parallel(tmp_path, monkeypatch):
    from test_voice_render import FakeASR, FakeTTS

    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    monkeypatch.setattr(ai_config.semantic_review, "languages", [])
    tts = FakeTTS()
    active = {"now": 0, "max": 0}
    inner = tts.synthesize

    async def slow(req):
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.02)
        active["now"] -= 1
        return await inner(req)

    tts.synthesize = slow
    blocks = []
    for k in range(3):
        speech = f"Block {k} starts here. It ends here."
        blocks.append({
            "block_id": f"FA_B0{k}_01", "section_id": f"B0{k}", "text": speech,
            "tts_text": f"[slowly] Block {k} starts here. [measured] It ends here.",
            "display_text": f"Block {k} starts here. It ends here.",
            "sentences": [{"speech": f"Block {k} starts here.", "display": f"Block {k} starts here.",
                           "tts": f"[slowly] Block {k} starts here.", "level": 1},
                          {"speech": "It ends here.", "display": "It ends here.",
                           "tts": "[measured] It ends here.", "level": 3}],
            "word_count": 7, "est_seconds": 3.0, "style": "v3_tension", "level": 2,
            "beats": [{"beat_id": f"B0{k}", "start_char": 0, "end_char": 60}],
        })
    m = asyncio.run(VoiceRenderer(provider=tts, asr=FakeASR()).render(
        {"language": "fa", "blocks": blocks}, case_id=1, story_version_id=2))
    assert active["max"] > 1  # blocks synthesized together
    assert all(r.text.startswith("[slowly]") and r.language_code == "fa" for r in tts.requests)
    assert all(b["asr"]["passed"] for b in m["blocks"])
    words = [w["word"] for w in m["timeline"]["words"]]
    assert not any("[" in w for w in words)
    sents = m["timeline"]["sentences"]
    assert len(sents) == 6 and [s["level"] for s in sents[:2]] == [1, 3]
    assert all(a["start"] <= b["start"] for a, b in zip(sents, sents[1:]))
    assert m["blocks"][0]["level"] == 2 and m["blocks"][0]["tts_text"].startswith("[slowly]")


def test_build_directed_performance_breath_grows_with_level():
    sections = [{"id": "B01", "text": "One here now. Two here now.\n\nThree here now."}]
    blueprint = {"beats": [{"id": "B01", "act_id": "B01", "paragraphs": [1, 2],
                            "audio_intent": "neutral"}]}
    plan = {"beats": [{"beat_id": "B01", "paragraph_breath": "normal",
                       "after": {"type": "breath", "seconds": 1.2}}]}

    def recs(level):
        return {"B01": [[{"speech": "One here now.", "display": "One here now.",
                          "tts": "One here now.", "level": level},
                         {"speech": "Two here now.", "display": "Two here now.",
                          "tts": "Two here now.", "level": level}],
                        [{"speech": "Three here now.", "display": "Three here now.",
                          "tts": "Three here now.", "level": level}]]}

    # tiny paragraphs join one breath group, so compare whole scripts
    calm = build_directed_performance(sections, "en", blueprint, plan, speech=recs(0),
                                      use_tags=True)
    tense = build_directed_performance(sections, "en", blueprint, plan, speech=recs(3),
                                       use_tags=True)
    assert calm["blocks"][0]["style"] == "v3_neutral"
    assert tense["blocks"][0]["style"] == "v3_climax"


# ---------------------------------------------------------------------------
# parallel pipeline: partial jobs, batches, from zero
# ---------------------------------------------------------------------------


def test_job_stages_include_performance_and_from_zero():
    from app.documentary.jobs import plan_stages

    names = [s["name"] for s in plan_stages(["en", "fa"], from_zero=True)]
    assert names[:5] == ["research", "language_pipeline", "final_approval", "blueprint", "audio_plan"]
    assert names.index("performance:fa") < names.index("voice:fa") < names.index("render:fa")


def test_batch_api_starts_one_job_per_case(client, db_session, monkeypatch):
    from test_blueprint_performance import _story

    launched = []
    monkeypatch.setattr("app.documentary.jobs.launch", lambda job_id: launched.append(job_id))
    c1, m1 = _story(db_session)
    c2, m2 = _story(db_session)
    from app.db.models import Case
    from app.utils import slugify
    c3 = Case(canonical_title="Fresh case", slug=slugify("Fresh case x"), language="en")
    db_session.add(c3)
    db_session.commit()
    r = client.post("/api/documentary/batch", json={
        "items": [{"case_id": c1.id}, {"case_id": c2.id},
                  {"case_id": c3.id, "from_zero": True, "target_minutes": 50},
                  {"case_id": 999999}],
        "languages": ["en", "fa"], "mode": "pilot", "pilot_seconds": 180})
    assert r.status_code == 200, r.text
    body = r.json()
    # one job per case AND language; c1 and c2 only have an English final text
    assert len(body["jobs"]) == 4 and len(launched) == 4
    assert {(j["case_id"], tuple(j["languages"])) for j in body["jobs"]} == {
        (c1.id, ("en",)), (c2.id, ("en",)), (c3.id, ("en",)), (c3.id, ("fa",))}
    assert [r["reason"] for r in body["rejected"] if r["case_id"] == 999999] == ["case not found"]
    assert {(r["case_id"], r.get("language")) for r in body["rejected"]} >= {(c1.id, "fa"), (c2.id, "fa")}
    fresh = next(j for j in body["jobs"] if j["case_id"] == c3.id)
    assert fresh["from_zero"] and fresh["final_version_id"] is None
    # In pilot mode the pilot is the editorial target too: 180 seconds is a
    # three-minute master target, even when the batch item supplied the old
    # full-film target field.
    assert fresh["stages"][0]["name"] == "research" and fresh["target_minutes"] == 3.0
    batch = client.get(f"/api/documentary/batches/{body['batch_id']}").json()
    assert len(batch["jobs"]) == 4 and batch["statuses"] == {"queued": 4}
    sched = client.get("/api/documentary/scheduler").json()
    assert sched["max_parallel_jobs"] == ai_config.concurrency.jobs
    assert len(sched["queued"]) >= 4
    listed = client.get("/api/documentary/jobs?status=queued").json()
    assert {j["case_title"] for j in listed} >= {"Fresh case"}
    # without from_zero a case needs a master story
    r2 = client.post("/api/documentary/batch", json={"items": [{"case_id": c3.id}]})
    assert r2.status_code == 409


def test_parallel_languages_never_lose_each_others_overlay_texts(db_session, monkeypatch):
    """Two languages localize at the same time; both results stay."""
    from test_blueprint_performance import _story

    from app.db.models import VisualPlan
    from app.documentary.production import script as PS

    case, master = _story(db_session)
    from app.documentary.blueprint import NarrativeDirector  # noqa: F401
    row = VisualPlan(case_id=case.id, blueprint_id=1, plan_json=json.dumps({"beats": []}),
                     status="planned")
    db_session.add(row)
    db_session.commit()
    monkeypatch.setattr(PS, "overlay_texts", lambda plan: {"date|2 May 2015": "2 May 2015"})

    async def fake_localize(db, case_id, texts, language, excerpt):
        await asyncio.sleep(0.01 if language == "de" else 0.02)
        return {k: f"{v} [{language}]" for k, v in texts.items()}

    monkeypatch.setattr(PS, "localize_texts", fake_localize)

    class V:
        def __init__(self, lang):
            self.language, self.case_id = lang, case.id

    monkeypatch.setattr(PS, "stored_sections", lambda v: [{"id": "B01", "text": "x"}])

    async def both():
        return await asyncio.gather(PS.plan_texts_in_language(db_session, V("de"), row),
                                    PS.plan_texts_in_language(db_session, V("fa"), row))

    asyncio.run(both())
    db_session.refresh(row)
    assert set(json.loads(row.plan_json)["texts"]) == {"de", "fa"}


def test_cuts_vary_and_land_on_sentences():
    from app.documentary.production.script import plan_cuts

    cuts = plan_cuts(0.0, 64.0, [3, 9, 14.5, 21, 26, 33, 40, 45, 51, 58])
    sentences = {9, 14.5, 21, 26, 33, 40, 45, 51}
    # cuts land on sentence starts unless none is near (or it is too late)
    assert cuts[:3] == [9, 21, 26] and set(cuts[:3]) <= sentences
    gaps = [b - a for a, b in zip([0.0] + cuts, cuts + [64.0])]
    assert min(gaps) >= ai_config.visual_direction.min_cut_seconds
    assert len({round(g) for g in gaps}) > 1
