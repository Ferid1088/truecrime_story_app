"""Music/Audio director: clean narration, music only in the gaps,
silence as a choice, a track library with variety across films, and the
audit trail of every cue (acceptance tests K, L, M).

Sound generation is a fake (FakeSound from test_spoken_audio, sine
tones); FFmpeg normalizes the generated tracks like in production."""
import asyncio
import json
import shutil
import uuid

import pytest

from app.core.ai_config import ai_config
from app.db.models import AudioPlan, Case, MusicTrack, MusicUsage, StoryVersion
from app.documentary.audio_director import (
    MOOD_ALIASES, director_system_prompt, normalize_mood, validate_audio_plan,
)
from app.documentary.music import (
    DocumentaryMixer, MusicLibrary, _plan, film_key_for, plan_placements, select_track,
)
from test_spoken_audio import FakeSound, _bp, _plan_item, _sine_mp3

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="ffmpeg is required for audio tests")

_TONES: dict[bool, bytes] = {}


class QuickSound(FakeSound):
    """FakeSound with a short cached tone: the library still normalizes
    and stores every track, the tests just do not wait for 20 s cues."""

    async def generate(self, prompt, seconds, loop, influence):
        self.calls.append((prompt, seconds, loop))
        if loop not in _TONES:
            _TONES[loop] = _sine_mp3(1.5, 330 if loop else 550)
        return _TONES[loop], int(seconds * 11)


@pytest.fixture
def library(db_session, tmp_path, monkeypatch):
    """An empty track library in a temporary directory."""
    monkeypatch.setattr(ai_config.music_library, "dir", str(tmp_path / "lib"))

    def wipe():
        db_session.query(MusicUsage).delete()
        db_session.query(MusicTrack).delete()
        db_session.commit()

    wipe()
    sound = QuickSound()
    yield MusicLibrary(provider=sound), sound
    wipe()


def _case(db) -> Case:
    title = f"Music {uuid.uuid4().hex[:8]}"
    case = Case(canonical_title=title, slug=title.lower().replace(" ", "-"), language="en")
    db.add(case)
    db.commit()
    return case


# ---------------------------------------------------------------------------
# a narrated film: beats with real word timings
# ---------------------------------------------------------------------------

# (beat, first word at, words); words every 0.5 s, each 0.4 s long.
# Pauses after: B01 2.1 s, B02 10.1 s, B03 3.1 s, B04 4.1 s.
BEATS = [("B01", 0.0, 8), ("B02", 6.0, 8), ("B03", 20.0, 8),
         ("B04", 27.0, 8), ("B05", 35.0, 8)]


def _manifest(case_id=None, story_version_id=None, language="en") -> dict:
    words, spans = [], []
    for bid, t0, n in BEATS:
        ws = [{"word": f"{bid.lower()}w{i}", "start": round(t0 + 0.5 * i, 3),
               "end": round(t0 + 0.5 * i + 0.4, 3)} for i in range(n)]
        words += ws
        spans.append({"beat_id": bid, "start": ws[0]["start"], "end": ws[-1]["end"]})
    # alignment drift: B03's span ends before its last word does
    spans[2]["end"] = round(spans[2]["end"] - 0.9, 3)
    return {"case_id": case_id, "story_version_id": story_version_id,
            "language": language, "duration_seconds": words[-1]["end"],
            "timeline": {"beats": spans, "words": words}}


def _script(blueprint_id=None, **moods) -> dict:
    """beat_audio as the directed performance script carries it. Beds are
    requested on purpose: they must never reach the mix."""
    m = {"bridge": "investigation", "sting": "tension", **moods}
    return {"blueprint_id": blueprint_id, "beat_audio": {
        "B01": {"bed": "mystery", "bed_level": "low",
                "after": {"type": "breath", "seconds": 1.2}},
        "B02": {"bed": "tension", "bed_level": "very_low",
                "after": {"type": "music_bridge", "seconds": 10.0, "mood": m["bridge"],
                          "why": "the story moves to the police station"}},
        "B03": {"bed": "none", "bed_level": "very_low",
                "after": {"type": "silence", "seconds": 3.0, "mood": "melancholy",
                          "why": "the body is found; silence lets the fact land"}},
        "B04": {"bed": "none", "bed_level": "very_low",
                "after": {"type": "sting", "seconds": 3.0, "mood": m["sting"],
                          "why": "the alibi collapses"}},
        "B05": {"bed": "none", "after": {"type": "end", "seconds": 0.0}},
    }}


def _bridge_only(blueprint_id, mood="tension") -> dict:
    s = _script(blueprint_id, bridge=mood)
    for bid in ("B03", "B04"):
        s["beat_audio"][bid]["after"] = {"type": "breath", "seconds": 1.2}
    return s


def _assign(lib, db, manifest, script):
    return asyncio.run(DocumentaryMixer(lib).assign_tracks(manifest, script, db))


def _codes(chosen, roles=("music_bridge", "emotional_moment", "chapter_break", "sting")):
    return {p["track_code"] for p in chosen["placements"] if p["role"] in roles}


# ---------------------------------------------------------------------------
# K: no music under narration
# ---------------------------------------------------------------------------


def test_k_no_placement_overlaps_a_spoken_word():
    manifest = _manifest()
    places = plan_placements(manifest, _script())
    assert [p["role"] for p in places] == ["music_bridge", "silence", "sting"]
    words = manifest["timeline"]["words"]
    for p in places:
        start, end = p["start"], p["start"] + p["duration"]
        for w in words:
            assert end <= w["start"] + 1e-6 or start >= w["end"] - 1e-6, (p, w)


def test_k_cues_start_after_the_last_word_and_end_before_the_next():
    d = ai_config.audio_direction
    manifest = _manifest()
    words = manifest["timeline"]["words"]
    first = {bid: next(w for w in words if w["word"].startswith(bid.lower()))
             for bid, _, _ in BEATS}
    last = {bid: [w for w in words if w["word"].startswith(bid.lower())][-1]
            for bid, _, _ in BEATS}
    places = {p["after_beat"]: p for p in plan_placements(manifest, _script())}
    for bid, nxt in (("B02", "B03"), ("B04", "B05")):
        p = places[bid]
        assert p["start"] == pytest.approx(last[bid]["end"] + d.music_start_after_word_seconds)
        assert p["start"] + p["duration"] == pytest.approx(
            first[nxt]["start"] - d.music_end_before_word_seconds)
        assert p["fade_out"] <= p["duration"]  # fully faded before the next word
    # the drifted span: room tone starts after B03's real last word
    assert places["B03"]["start"] == pytest.approx(last["B03"]["end"])


def test_k_no_beds_when_narration_stays_clean(monkeypatch):
    assert ai_config.audio_direction.beds_under_narration is False
    places = plan_placements(_manifest(), _script())
    assert not [p for p in places if p["role"] == "bed"]
    # the older layout is still available as an explicit choice
    monkeypatch.setattr(ai_config.audio_direction, "beds_under_narration", True)
    assert [p for p in plan_placements(_manifest(), _script()) if p["role"] == "bed"]


def test_k_a_gap_too_short_for_a_cue_stays_empty():
    manifest = _manifest()
    script = _script()
    script["beat_audio"]["B01"]["after"] = {"type": "sting", "seconds": 2.0,
                                            "mood": "tension"}
    # B01 → B02: the next beat starts speaking 0.9 s after the last word
    b01, b02 = manifest["timeline"]["beats"][:2]
    first = next(w for w in manifest["timeline"]["words"] if w["word"] == "b02w0")
    first["start"] = b02["start"] = round(b01["end"] + 0.9, 3)
    first["end"] = round(first["start"] + 0.3, 3)
    places, skipped = _plan(manifest, script)
    assert "B01" not in {p["after_beat"] for p in places}
    assert skipped and skipped[0]["after_beat"] == "B01"
    assert skipped[0]["reason"].startswith("gap_too_short")


# ---------------------------------------------------------------------------
# L: the director's plan — gaps, silence, moods, reasons
# ---------------------------------------------------------------------------


def test_l_a_requested_bed_is_removed_with_an_adjustment():
    raw = {"beats": [_plan_item(b, "breath", 1.2) for b in ("B01", "B02", "B03", "B04")]
           + [_plan_item("B05", "end")]}
    raw["beats"][1]["bed"] = "tension"
    plan, rep = validate_audio_plan(raw, _bp())
    assert {pb["bed"] for pb in plan["beats"]} == {"none"}
    removed = [a for a in rep["adjustments"] if a.get("reason") == "no_music_under_narration"]
    assert len(removed) == 5 and removed[1]["bed"] == ["tension", "none"]
    assert rep["beds_removed"] == 5 and rep["status"] == "valid"
    # beds stay possible when the configuration allows them
    cfg = ai_config.audio_direction.model_copy(update={"beds_under_narration": True})
    plan, rep = validate_audio_plan(raw, _bp(), cfg)
    assert plan["beats"][1]["bed"] == "tension" and rep["beds_removed"] == 0


def test_l_every_music_or_silence_choice_keeps_its_reason(monkeypatch):
    # (chapter cards force chapter breaks at act ends: tested separately)
    monkeypatch.setattr(ai_config.chapters, "enabled", False)
    raw = {"beats": [
        _plan_item("B01", "breath", 1.2),
        _plan_item("B02", "silence", 3),
        {**_plan_item("B03", "sting", 3, mood="discovery"), "why": "the reveal"},
        {**_plan_item("B04", "emotional_moment", 10, mood="emotional"),
         "why": "grief for Leela"},
        _plan_item("B05", "end"),
    ]}
    raw["beats"][1]["after"]["why"] = "an unanswered question; silence is stronger"
    cfg = ai_config.audio_direction.model_copy(update={
        "min_seconds_between_music_moments": 0.0, "max_music_only_share": 0.5})
    plan, rep = validate_audio_plan(raw, _bp(), cfg)
    after = {pb["beat_id"]: pb["after"] for pb in plan["beats"]}
    assert "why" not in after["B01"] and "why" not in after["B05"]
    assert after["B02"]["why"] == "an unanswered question; silence is stronger"
    assert after["B03"]["why"] == "the reveal" and after["B03"]["mood"] == "discovery"
    # old vocabulary still validates: emotional → melancholy
    assert after["B04"]["mood"] == "melancholy"
    assert {"beat": "B04", "mood": ["emotional", "melancholy"],
            "reason": "mood_alias"} in rep["adjustments"]
    # silence is not music: it never counts toward the music-only share
    assert rep["silences"] == 1 and rep["music_moments"] == 2
    assert rep["music_only_seconds"] == pytest.approx(
        after["B03"]["seconds"] + after["B04"]["seconds"])


def test_l_a_guard_rail_change_is_part_of_the_reason(monkeypatch):
    # (chapter cards force chapter breaks at act ends: tested separately)
    monkeypatch.setattr(ai_config.chapters, "enabled", False)
    raw = {"beats": [_plan_item("B01", "breath", 1.2),
                     {**_plan_item("B02", "emotional_moment", 10), "why": "loss"},
                     _plan_item("B03", "breath", 1.2), _plan_item("B04", "breath", 1.2),
                     _plan_item("B05", "end")]}
    bp = _bp()
    bp["beats"][1]["emotional_load"] = "low"
    plan, _ = validate_audio_plan(raw, bp)
    after = plan["beats"][1]["after"]
    assert after["type"] == "music_bridge"
    assert after["why"].startswith("loss") and "emotional_moment_needs_emotional_beat" in after["why"]


def test_l_moods_follow_the_catalogue():
    moods = ai_config.audio_direction.moods
    assert {"suspense", "investigation", "melancholy", "discovery", "relief",
            "resolution", "uncertainty"} <= set(moods)
    assert MOOD_ALIASES == {"emotional": "melancholy", "reflective": "uncertainty"}
    assert normalize_mood("reflective") == "uncertainty"
    assert normalize_mood("Danger") == "danger"
    assert normalize_mood("epic", "emotional_moment") == "melancholy"
    assert normalize_mood(None, "music_bridge") == "mystery"


def test_l_director_prompt_is_a_music_and_audio_director():
    prompt = director_system_prompt()
    for needle in ("Music and Audio Director", "when the narrator speaks, there is no music",
                   "BEFORE a revelation", "SILENCE is a decision", "unanswered questions",
                   'never "suspense because it is true crime"', "chapter transitions",
                   "silent visual sequence", "three or four minutes", "scene changes",
                   '"why"'):
        assert needle in prompt, needle
    for mood in ai_config.audio_direction.moods:
        assert f"- {mood}:" in prompt
    assert 'always "none"' in prompt
    cfg = ai_config.audio_direction.model_copy(update={"beds_under_narration": True})
    assert "bed_level: very_low | low" in director_system_prompt(cfg)


@needs_ffmpeg
def test_l_silence_is_room_tone_and_cues_sit_in_gaps(db_session, library):
    lib, _ = library
    case = _case(db_session)
    manifest = _manifest(case.id)
    chosen = _assign(lib, db_session, manifest, _script(910001))
    spans = {s["beat_id"]: s for s in manifest["timeline"]["beats"]}
    order = [b for b, _, _ in BEATS]
    for p in chosen["placements"]:
        nxt = spans[order[order.index(p["after_beat"]) + 1]]
        assert p["start"] >= spans[p["after_beat"]]["end"] - 1e-6
        assert p["start"] + p["duration"] <= nxt["start"] + 1e-6
    silence = next(p for p in chosen["placements"] if p["role"] == "silence")
    track = db_session.get(MusicTrack, silence["track_id"])
    assert track.kind == "room_tone" and silence["mood"] == "neutral"
    assert silence["level_db"] == ai_config.audio_direction.room_tone_level_db
    music = [p for p in chosen["placements"] if p["role"] != "silence"]
    assert {db_session.get(MusicTrack, p["track_id"]).kind for p in music} == {"bridge", "sting"}


# ---------------------------------------------------------------------------
# M: variety across films, one theme within a film
# ---------------------------------------------------------------------------


@needs_ffmpeg
def test_m_second_film_avoids_the_first_films_tracks(db_session, library, monkeypatch):
    lib, sound = library
    monkeypatch.setattr(ai_config.music_library, "reuse_after_videos", 10)
    case_a, case_b = _case(db_session), _case(db_session)
    a_en = _assign(lib, db_session, _manifest(case_a.id), _script(910101, bridge="tension"))
    assert _codes(a_en) == {"bridge-tension-v1", "sting-tension-v1"}  # config cues
    calls = len(sound.calls)
    # every language of a film hears the same tracks, without new generation
    a_de = _assign(lib, db_session, _manifest(case_a.id, language="de"),
                   _script(910101, bridge="tension"))
    assert _codes(a_de) == _codes(a_en) and len(sound.calls) == calls
    assert all("film theme" in p["selection_reason"] for p in a_de["placements"]
               if p["role"] != "silence")

    # an alternative bridge already exists: film B takes it ...
    alt = lib.new_variant(db_session, "bridge", "tension")
    b = _assign(lib, db_session, _manifest(case_b.id), _script(910102, bridge="tension"))
    by_role = {p["role"]: p for p in b["placements"]}
    assert by_role["music_bridge"]["track_code"] == alt.track_code == "bridge-tension-v2"
    assert "skipped bridge-tension-v1" in by_role["music_bridge"]["selection_reason"]
    # ... and gets a new sting variant, since no unused sting exists
    assert by_role["sting"]["track_code"] == "sting-tension-v2"
    assert "new variant generated" in by_role["sting"]["selection_reason"]
    assert not _codes(b) & _codes(a_en)
    new_prompt = sound.calls[-1][0]
    assert ai_config.music_library.mood_prompts["tension"] in new_prompt
    assert any(style in new_prompt for style in ai_config.music_library.variant_styles)
    # room tone is not music: shared
    assert by_role["silence"]["track_code"] == next(
        p["track_code"] for p in a_en["placements"] if p["role"] == "silence")
    assert db_session.query(MusicTrack).filter_by(kind="bridge", mood="tension").count() == 2


@needs_ffmpeg
def test_m_a_track_returns_after_the_reuse_window(db_session, library, monkeypatch):
    lib, _ = library
    monkeypatch.setattr(ai_config.music_library, "reuse_after_videos", 1)
    monkeypatch.setattr(ai_config.music_library, "max_variants_per_mood", 2)
    case = _case(db_session)

    def film(bp):
        chosen = _assign(lib, db_session, _manifest(case.id), _bridge_only(bp))
        (p,) = chosen["placements"]
        return p["track_code"], p["selection_reason"]

    assert film(910201)[0] == "bridge-tension-v1"
    code, why = film(910202)
    assert code == "bridge-tension-v2" and "new variant" in why
    # one other film later, v1 is free again
    code, why = film(910203)
    assert code == "bridge-tension-v1" and "not used by the last 1 other films" in why
    # a wider window and the variant cap reached: least recently used returns
    monkeypatch.setattr(ai_config.music_library, "reuse_after_videos", 5)
    code, why = film(910204)
    assert code == "bridge-tension-v2"
    assert "REUSED" in why and "least recently used" in why and "bp910202" in why
    assert db_session.query(MusicTrack).filter_by(kind="bridge", mood="tension").count() == 2


def test_m_config_cues_keep_their_generated_files(db_session, library):
    lib, sound = library
    case = _case(db_session)
    # the library already holds the file generated for a config cue
    cue = next(c for c in ai_config.music_library.cues if c.id == "bridge_emotional")
    lib.path_for(cue).parent.mkdir(parents=True)
    lib.path_for(cue).write_bytes(b"generated before the track library")
    # an old mood name still finds the imported cue: emotional → melancholy
    chosen = _assign(lib, db_session, _manifest(case.id), _bridge_only(910301, "emotional"))
    (p,) = chosen["placements"]
    assert p["mood"] == "melancholy" and p["cue_id"] == "bridge_emotional"
    assert p["track_code"] == "bridge-melancholy-v1" and p["path"] == lib.path_for(cue)
    assert sound.calls == [] and chosen["tracks_generated"] == []


def test_film_key_follows_the_blueprint_across_languages(db_session):
    case = _case(db_session)

    def version(lang, struct):
        v = StoryVersion(case_id=case.id, version=1, kind="spoken", language=lang,
                         narrative_angle="{}", story_text="t",
                         narrative_structure=json.dumps(struct))
        db_session.add(v)
        db_session.commit()
        return v

    en, de = version("en", {"blueprint_id": 4242}), version("de", {"blueprint_id": 4242})
    other = version("en", {})
    assert film_key_for(db_session, {}, {"story_version_id": en.id}) == ("bp4242", 4242)
    assert film_key_for(db_session, {}, {"story_version_id": de.id})[0] == "bp4242"
    assert film_key_for(db_session, {}, {"story_version_id": other.id}) == (
        f"sv{other.id}", None)
    assert film_key_for(db_session, {"blueprint_id": 7}, {})[0] == "bp7"


# ---------------------------------------------------------------------------
# audit: MusicUsage
# ---------------------------------------------------------------------------


@needs_ffmpeg
def test_usage_rows_carry_why_and_selection_reason_without_duplicates(db_session, library):
    lib, _ = library
    case = _case(db_session)
    script = _script(910401)
    first = _assign(lib, db_session, _manifest(case.id), script)
    rows = db_session.query(MusicUsage).filter_by(film_key="bp910401").all()
    assert len(rows) == len(first["placements"]) == 3
    by_purpose = {r.purpose: r for r in rows}
    assert set(by_purpose) == {"music_bridge", "silence", "sting"}
    assert by_purpose["silence"].why == "the body is found; silence lets the fact land"
    assert by_purpose["silence"].mood == "neutral"
    assert db_session.get(MusicTrack, by_purpose["silence"].track_id).kind == "room_tone"
    assert by_purpose["music_bridge"].why == "the story moves to the police station"
    assert by_purpose["music_bridge"].mood == "investigation"
    for r in rows:
        assert r.selection_reason and r.case_id == case.id and r.language == "en"
        assert r.beat_id in {"B02", "B03", "B04"}
        assert r.end > r.start and r.production_script_id is None
    # re-mixing the same film and language replaces its rows
    _assign(lib, db_session, _manifest(case.id), script)
    assert db_session.query(MusicUsage).filter_by(film_key="bp910401").count() == 3
    # another language adds its own
    _assign(lib, db_session, _manifest(case.id, language="fa"), script)
    assert db_session.query(MusicUsage).filter_by(film_key="bp910401").count() == 6


@needs_ffmpeg
def test_usage_why_falls_back_to_the_stored_audio_plan(db_session, library):
    lib, _ = library
    case = _case(db_session)
    bp_id = 910501
    db_session.add(AudioPlan(case_id=case.id, blueprint_id=bp_id, version=1, status="valid",
                             plan_json=json.dumps({"beats": [
                                 {"beat_id": "B02", "why": "a change of place",
                                  "after": {"type": "music_bridge"}}]})))
    db_session.commit()
    script = _bridge_only(bp_id)
    script["beat_audio"]["B02"]["after"].pop("why")
    _assign(lib, db_session, _manifest(case.id), script)
    (row,) = db_session.query(MusicUsage).filter_by(film_key=f"bp{bp_id}").all()
    assert row.why == "a change of place"


@needs_ffmpeg
def test_music_api_lists_tracks_with_usage(client, db_session, library):
    lib, _ = library
    case = _case(db_session)
    _assign(lib, db_session, _manifest(case.id), _script(910601, bridge="tension"))
    _assign(lib, db_session, _manifest(case.id, language="de"), _script(910601, bridge="tension"))
    tracks = {t["id"]: t for t in client.get("/api/documentary/music").json()}
    bridge = tracks["bridge-tension-v1"]
    assert bridge["usage_count"] == 2 and bridge["films"] == ["bp910601"]
    assert bridge["last_used_at"] and bridge["generated"] and bridge["cue_id"] == "bridge_tension"
    assert tracks["bridge-mystery-v1"]["usage_count"] == 0
    r = client.get(bridge["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    # old cue ids still resolve
    assert client.get("/api/documentary/music/bridge_tension/file").status_code == 200
    assert client.get("/api/documentary/music/no-such-track/file").status_code == 404


# ---------------------------------------------------------------------------
# the full mix
# ---------------------------------------------------------------------------


@needs_ffmpeg
def test_mix_reports_tracks_and_reasons(db_session, library, tmp_path):
    import subprocess

    lib, _ = library
    case = _case(db_session)
    out = tmp_path / "v1"
    out.mkdir()
    manifest = _manifest(case.id)
    sr = ai_config.loudness.sample_rate
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"sine=frequency=200:duration={manifest['duration_seconds']}"
                    f":sample_rate={sr}",
                    "-ac", "1", "-c:a", "pcm_s16le", str(out / "narration.wav")], check=True)
    result = asyncio.run(DocumentaryMixer(lib).mix(manifest, _script(910701), out))
    assert result["film_key"] == "bp910701" and result["beds"] == 0
    assert result["music_moments"] == 2 and result["silences"] == 1
    for p in result["placements"]:
        assert p["cue_id"] and p["track_code"] and p["selection_reason"]
    assert [r["beat_id"] for r in result["selection_reasons"]] == ["B02", "B03", "B04"]
    assert result["music_characters_paid"] > 0 and result["tracks_generated"]
    assert (out / "documentary.wav").exists()
    # the mixer opened (and closed) its own session: rows are stored
    assert db_session.query(MusicUsage).filter_by(film_key="bp910701").count() == 3


@needs_ffmpeg
def test_select_track_reuses_the_film_theme(db_session, library):
    lib, _ = library
    case = _case(db_session)
    _assign(lib, db_session, _manifest(case.id), _bridge_only(910801, "danger"))
    track, why = select_track(db_session, lib, "bridge", "danger", "bp910801")
    assert track.track_code == "bridge-danger-v1" and "film theme" in why
