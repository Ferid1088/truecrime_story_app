"""Editorial blueprint (validator + director with repair) and the
performance script built from it (styles, pauses, silence director),
including its effect on voice rendering. All model calls are scripted."""
import asyncio
import copy
import json
import uuid

import pytest

from app.core.ai_config import ai_config
from app.documentary.blueprint import (
    NarrativeDirector, usable_blueprint, validate_blueprint,
)
from app.documentary.performance import build_performance, direct_pauses
from app.documentary.voice_blocks import plan_voice_blocks
from app.providers.generation.base import GenerationResult


def _para(tag, words=60):
    return " ".join([f"{tag}"] + ["word"] * (words - 1)) + "."


SECTIONS = [
    {"id": "act1", "text": "\n\n".join(_para(f"a{i}") for i in range(1, 5))},
    {"id": "act2", "text": "\n\n".join(_para(f"b{i}") for i in range(1, 4))},
]
PACK = {
    "facts": [{"id": f"F00{i}", "claim": f"fact {i}"} for i in range(1, 5)],
    "timeline": [{"id": "T001", "claim": "event", "event_date": "2007-07-16"}],
    "contradictions": [{"id": "C001", "topic": "t", "description": "d"}],
}


def _beat(bid, act, first, last, **kw):
    base = {
        "id": bid, "act_id": act, "paragraphs": [first, last],
        "purpose": "timeline", "summary": "s", "reveals": [], "relies_on": [],
        "opens": [], "answers": [], "human_focus": None,
        "emotional_load": "medium", "information_density": "medium",
        "mystery_intensity": "medium", "attention": "listen",
        "visual_intent": "hold_current", "audio_intent": "neutral",
        "pause_after": "none", "music_intent": "none",
    }
    base.update(kw)
    return base


def _good():
    return {
        "central_question": "Where did the family go?",
        "editorial_thesis": "t", "human_thread": "Leela",
        "arcs": {"mystery": "m", "investigation": "i", "emotional": "e"},
        "questions": [
            {"id": "Q1", "question": "Why was the house so clean?", "kind": "mystery"},
            {"id": "Q2", "question": "Who was Tony?", "kind": "human"},
        ],
        "beats": [
            _beat("B01", "act1", 1, 1, purpose="hook", reveals=["F001"], opens=["Q1"],
                  audio_intent="controlled_tension", pause_after="short"),
            _beat("B02", "act1", 2, 3, reveals=["F002"], relies_on=["F001"],
                  opens=["Q2"], audio_intent="controlled_tension"),
            _beat("B03", "act1", 4, 4, purpose="reveal", reveals=["C001"],
                  answers=["Q1"], audio_intent="reveal", pause_after="dramatic"),
            _beat("B04", "act2", 1, 2, reveals=["F003"], answers=["Q2"]),
            _beat("B05", "act2", 3, 3, purpose="chapter_end", reveals=["F004"],
                  audio_intent="reflective", pause_after="silence"),
        ],
    }


def codes(items):
    return {i["code"] for i in items}


# ---------------------------------------------------------------------------
# validator
# ---------------------------------------------------------------------------


def test_valid_blueprint_and_listener_state():
    bp, rep = validate_blueprint(_good(), SECTIONS, PACK)
    assert rep["status"] == "valid", rep
    b = {x["id"]: x for x in bp["beats"]}
    assert b["B02"]["viewer_knows"] == ["F001", "F002"]
    assert b["B02"]["open_questions"] == ["Q1", "Q2"]
    assert b["B03"]["open_questions"] == ["Q2"]
    q = {x["id"]: x for x in bp["questions"]}
    assert (q["Q1"]["opened_in"], q["Q1"]["resolved_in"], q["Q1"]["status"]) == (
        "B01", "B03", "answered")


@pytest.mark.parametrize("mutate, code", [
    (lambda d: d["beats"].pop(1), "coverage_mismatch"),                 # gap
    (lambda d: d["beats"][1].update(paragraphs=[1, 3]), "coverage_mismatch"),  # overlap
    (lambda d: d["beats"][2].update(paragraphs=[4, 5]), "paragraph_out_of_range"),
    (lambda d: d["beats"][0].update(act_id="act9"), "unknown_act"),
    (lambda d: d["beats"][0].update(reveals=["F999"]), "unknown_evidence"),
    (lambda d: d["beats"][3].update(reveals=["F001"]), "revealed_twice"),
    (lambda d: d["beats"][0].update(answers=["Q2"]), "answered_before_asked"),
    (lambda d: d["beats"][0].update(opens=["Q9"]), "unknown_question"),
    (lambda d: d["beats"][1].update(id="B01"), "duplicate_beat_id"),
])
def test_structural_errors_make_it_invalid(mutate, code):
    data = _good()
    mutate(data)
    _, rep = validate_blueprint(data, SECTIONS, PACK)
    assert rep["status"] == "invalid" and code in codes(rep["errors"]), rep["errors"]


def test_relying_on_unrevealed_evidence_is_flagged():
    data = _good()
    data["beats"][0]["relies_on"] = ["F003"]  # only revealed in B04
    _, rep = validate_blueprint(data, SECTIONS, PACK)
    assert rep["status"] == "needs_review"
    assert "relies_on_unrevealed" in codes(rep["warnings"])


def test_listener_load_warnings():
    data = _good()
    data["questions"] += [{"id": f"Q{i}", "question": "?"} for i in range(3, 6)]
    data["beats"][0]["opens"] = ["Q1", "Q3", "Q4", "Q5"]
    for b in data["beats"]:
        b["information_density"] = "high"
    data["beats"][1]["attention"] = "read"
    _, rep = validate_blueprint(data, SECTIONS, PACK)
    w = codes(rep["warnings"])
    assert {"too_many_open_questions", "many_unanswered_questions",
            "dense_run_without_recovery", "read_during_dense_narration"} <= w


def test_unknown_enum_values_are_coerced_with_warning():
    data = _good()
    data["beats"][0]["visual_intent"] = "drone_flyover"
    bp, rep = validate_blueprint(data, SECTIONS, PACK)
    assert bp["beats"][0]["visual_intent"] == "hold_current"
    assert "coerced_enum" in codes(rep["warnings"])


# ---------------------------------------------------------------------------
# director: one repair round, stored row, stale evidence
# ---------------------------------------------------------------------------


class DirectorGen:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def is_configured(self):
        return True

    async def generate_structured(self, role, system, user):
        self.calls.append((role, json.loads(user)))
        return copy.deepcopy(self.outputs.pop(0)), GenerationResult(
            text="{}", model="m/director", provider="fake")


def _story(db, sections=SECTIONS, fingerprint=None):
    from app.db.models import Case, Fact, StoryVersion
    from app.utils import slugify

    title = f"BP {uuid.uuid4().hex[:6]}"
    case = Case(canonical_title=title, slug=slugify(title), language="en")
    db.add(case)
    db.commit()
    for i in range(1, 5):
        db.add(Fact(case_id=case.id, claim=f"fact {i}", confidence=0.9))
    db.commit()
    struct = {"sections": [dict(s, words=len(s["text"].split())) for s in sections]}
    if fingerprint:
        struct["evidence_fingerprint"] = fingerprint
    v = StoryVersion(
        case_id=case.id, version=1, kind="master", language="en",
        narrative_angle="{}", narrative_structure=json.dumps(struct),
        story_text="\n\n".join(s["text"] for s in sections), text_hash="h1",
        engagement_score=80.0, status="ready",
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return case, v


def _director(monkeypatch, outputs):
    gen = DirectorGen(outputs)
    monkeypatch.setattr("app.documentary.blueprint.get_generation_provider", lambda: gen)
    return NarrativeDirector(), gen


def _no_contradiction(data):
    data = copy.deepcopy(data)
    data["beats"][2]["reveals"] = []  # the test case has no contradictions
    return data


def test_director_repairs_structural_errors(db_session, monkeypatch):
    case, v = _story(db_session)
    broken = _no_contradiction(_good())
    broken["beats"].pop(1)  # coverage gap
    director, gen = _director(monkeypatch, [broken, _no_contradiction(_good())])
    row = asyncio.run(director.create(db_session, case, v))
    assert len(gen.calls) == 2
    assert gen.calls[0][0] == "narrative_director"
    assert "coverage_mismatch" in codes(gen.calls[1][1]["errors_to_fix"])
    assert row.status == "valid" and row.evidence_fingerprint
    assert row.story_text_hash == "h1"
    assert json.loads(row.validation_json)["repair_iterations"] == 1
    assert usable_blueprint(db_session, v)["beats"][0]["id"] == "B01"


def test_director_input_numbers_paragraphs_per_act(db_session, monkeypatch):
    case, v = _story(db_session)
    director, gen = _director(monkeypatch, [_no_contradiction(_good())])
    asyncio.run(director.create(db_session, case, v))
    acts = gen.calls[0][1]["acts"]
    assert [a["act_id"] for a in acts] == ["act1", "act2"]
    assert [p["n"] for p in acts[0]["paragraphs"]] == [1, 2, 3, 4]
    assert gen.calls[0][1]["evidence"]["facts"][0]["id"] == "F001"


def test_unusable_blueprints(db_session, monkeypatch):
    case, v = _story(db_session)
    bad = _no_contradiction(_good())
    bad["beats"] = []
    director, _ = _director(monkeypatch, [bad, bad])
    row = asyncio.run(director.create(db_session, case, v))
    assert row.status == "invalid" and usable_blueprint(db_session, v) is None

    director, _ = _director(monkeypatch, [_no_contradiction(_good())])
    asyncio.run(director.create(db_session, case, v))
    assert usable_blueprint(db_session, v) is not None
    v.text_hash = "edited-later"  # blueprint belongs to the old text
    assert usable_blueprint(db_session, v) is None


def test_director_refuses_story_from_older_evidence(db_session, monkeypatch):
    case, v = _story(db_session, fingerprint="0" * 16)
    director, gen = _director(monkeypatch, [_good()])
    with pytest.raises(RuntimeError, match="older evidence"):
        asyncio.run(director.create(db_session, case, v))
    assert gen.calls == []


# ---------------------------------------------------------------------------
# performance script
# ---------------------------------------------------------------------------


def _bp():
    return validate_blueprint(_good(), SECTIONS, PACK)[0]


def test_without_blueprint_one_segment_per_act():
    script = build_performance(SECTIONS, "en", None)
    plain = plan_voice_blocks(SECTIONS, "en")
    assert not script["beats_mapped"]
    assert [b["block_id"] for b in script["blocks"]] == [
        b["block_id"] for b in plain["blocks"]]
    assert {b["style"] for b in script["blocks"]} == {ai_config.voice.default_style}


def test_style_changes_and_long_pauses_are_block_boundaries():
    script = build_performance(SECTIONS, "en", _bp())
    segs = script["segments"]
    # B01+B02 (same tension style) | B03 (reveal) | B04 | B05
    assert [s["beat_ids"] for s in segs] == [["B01", "B02"], ["B03"], ["B04"], ["B05"]]
    style = ai_config.performance.style_for_intent
    assert [s["style"] for s in segs] == [
        style["controlled_tension"], style["reveal"], style["neutral"],
        style["reflective"]]
    for blk in script["blocks"]:
        seg = next(s for s in segs if s["id"] == blk["segment_id"])
        assert blk["style"] == seg["style"]
        assert {b["beat_id"] for b in blk["beats"]} <= set(seg["beat_ids"])


def test_pause_lengths_follow_classes_with_jitter():
    script = build_performance(SECTIONS, "en", _bp())
    perf, voice = ai_config.performance, ai_config.voice
    by_seg = {}
    for blk in script["blocks"]:
        by_seg[blk["segment_id"]] = blk  # last block of each segment
    reveal_end = by_seg["act1.s02"]
    assert reveal_end["pause_after_kind"] == "dramatic"
    lo = perf.pause_ms["dramatic"] * (1 - perf.pause_jitter)
    assert max(lo, voice.between_sections_ms) <= reveal_end["pause_after_ms"]
    assert by_seg["act2.s02"]["pause_after_kind"] == "end"
    again = build_performance(SECTIONS, "en", _bp())
    assert [b["pause_after_ms"] for b in again["blocks"]] == [
        b["pause_after_ms"] for b in script["blocks"]]  # deterministic


def test_silence_director_keeps_long_pauses_rare_and_meaningful():
    beats = [
        {"id": f"B{i:02d}", "purpose": "timeline", "pause_after": "dramatic",
         "emotional_load": "low", "mystery_intensity": "low"}
        for i in range(1, 11)
    ]
    beats[4]["purpose"] = "reveal"
    beats[8]["emotional_load"] = "high"
    pauses, log = direct_pauses(beats)
    longs = [b for b, k in pauses.items() if k in ("dramatic", "silence")]
    assert "B05" in longs  # protected reveal survives
    assert len(longs) <= max(1, int(0.2 * 10))
    ids = [b["id"] for b in beats]
    for a, b in zip(ids, ids[1:]):
        assert not (pauses[a] in ("dramatic", "silence")
                    and pauses[b] in ("dramatic", "silence"))
    assert {e["reason"] for e in log} <= {
        "consecutive_long_pauses", "long_pause_share_cap"}


def test_beat_ranges_reconstruct_beat_text():
    script = build_performance(SECTIONS, "en", _bp())
    pieces = {}
    for blk in script["blocks"]:
        for bt in blk["beats"]:
            pieces.setdefault(bt["beat_id"], []).append(
                blk["text"][bt["start_char"]:bt["end_char"]])
    act1 = SECTIONS[0]["text"].split("\n\n")
    assert " ".join(" ".join(pieces["B02"]).split()) == " ".join(
        " ".join(act1[1:3]).split())


# ---------------------------------------------------------------------------
# voice render honours the performance script
# ---------------------------------------------------------------------------


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    return tmp_path


def test_render_uses_styles_pauses_and_reports_beat_times(workdir):
    from test_voice_render import FakeASR, FakeTTS
    from app.documentary.voice_render import VoiceRenderer

    sections = [
        {"id": "act1", "text": "\n\n".join(
            f"{w} opened the door and saw nothing there." for w in ("Ann", "Ben", "Cal", "Dan"))},
        {"id": "act2", "text": "\n\n".join(
            f"{w} walked to the road and waited for hours." for w in ("Eve", "Fay", "Gus"))},
    ]
    bp = validate_blueprint(_good(), sections, PACK)[0]
    script = build_performance(sections, "en", bp)
    tts = FakeTTS()
    m = asyncio.run(VoiceRenderer(provider=tts, asr=FakeASR()).render(
        script, case_id=9, story_version_id=1))
    speeds = {r.text.split()[0]: r.settings["speed"] for r in tts.requests}
    styles = ai_config.voice.styles
    assert speeds["Dan"] == styles[ai_config.performance.style_for_intent["reveal"]].speed
    tl = m["timeline"]
    reveal_block = next(b for b in tl["blocks"] if b["pause_after_kind"] == "dramatic")
    nxt = tl["blocks"][tl["blocks"].index(reveal_block) + 1]
    assert abs((nxt["start"] - reveal_block["end"]) * 1000
               - reveal_block["pause_after_ms"]) < 25
    beats = tl["beats"]
    assert [b["beat_id"] for b in beats] == ["B01", "B02", "B03", "B04", "B05"]
    assert all(b["start"] < b["end"] for b in beats)
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(beats, beats[1:]))


def test_cache_follows_content_not_block_id(workdir):
    from test_voice_render import FakeASR, FakeTTS, _block
    from app.documentary.voice_render import VoiceRenderer

    tts = FakeTTS()
    text = "The landlords found the house empty and spotless that morning."
    for bid in ("EN_full_01", "EN_act1_s01_01"):
        asyncio.run(VoiceRenderer(provider=tts, asr=FakeASR()).render(
            {"language": "en", "blocks": [_block(bid, "full", text)]},
            case_id=9, story_version_id=2))
    assert len(tts.requests) == 1


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_blueprint_and_performance_api(client, db_session, monkeypatch):
    case, v = _story(db_session)
    _director(monkeypatch, [_no_contradiction(_good())])
    base = f"/api/cases/{case.id}/stories/{v.id}"
    assert client.get(base + "/blueprint").status_code == 404
    assert client.get(base + "/performance").json()["blueprint_used"] is False
    r = client.post(base + "/blueprint")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "valid" and len(body["blueprint"]["beats"]) == 5
    assert client.get(base + "/blueprint").json()["id"] == body["id"]
    perf = client.get(base + "/performance").json()
    assert perf["blueprint_used"] is True and perf["long_pauses"] >= 1


# ---------------------------------------------------------------------------
# honest questions, beat shape, improvement round
# ---------------------------------------------------------------------------


def test_unresolved_questions_are_closed_honestly():
    data = _good()
    data["beats"][3]["answers"] = []
    data["beats"][4]["unresolved"] = ["Q2"]  # the film says it stays unknown
    bp, rep = validate_blueprint(data, SECTIONS, PACK)
    q = {x["id"]: x for x in bp["questions"]}
    assert q["Q2"]["status"] == "unresolved" and q["Q2"]["resolved_in"] == "B05"
    assert rep["unanswered_questions"] == []
    assert bp["beats"][-1]["open_questions"] == []


def test_beat_shape_warnings():
    long_sections = [{"id": "act1", "text": "\n\n".join(
        _para(f"p{i}", words=150) for i in range(1, 5))}]
    data = {"questions": [], "beats": [
        _beat("B01", "act1", 1, 1, purpose="timeline"),
        _beat("B02", "act1", 2, 3, purpose="hook"),          # 300 words, mid-act hook
        _beat("B03", "act1", 4, 4, purpose="chapter_end"),
    ]}
    _, rep = validate_blueprint(data, long_sections, PACK)
    assert {"beat_too_long", "hook_not_at_act_start"} <= codes(rep["warnings"])
    data["beats"][0]["purpose"] = "chapter_end"
    _, rep = validate_blueprint(data, long_sections, PACK)
    assert "chapter_end_not_at_act_end" in codes(rep["warnings"])


def _overloaded():
    data = _no_contradiction(_good())
    data["questions"] += [{"id": f"Q{i}", "question": "?"} for i in range(3, 6)]
    data["beats"][0]["opens"] = ["Q1", "Q3", "Q4", "Q5"]
    data["beats"][4]["unresolved"] = ["Q3", "Q4", "Q5"]
    return data


def test_improvement_round_is_kept_only_when_better(db_session, monkeypatch):
    case, v = _story(db_session)
    director, gen = _director(monkeypatch, [_overloaded(), _no_contradiction(_good())])
    row = asyncio.run(director.create(db_session, case, v))
    rep = json.loads(row.validation_json)
    assert len(gen.calls) == 2 and "warnings_to_improve" in gen.calls[1][1]
    assert rep["revision_log"][0]["accepted"] is True
    assert row.status == "valid"

    worse = _overloaded()
    worse["beats"].pop(1)  # revision introduces a structural error
    director, gen = _director(monkeypatch, [_overloaded(), worse])
    row = asyncio.run(director.create(db_session, case, v))
    rep = json.loads(row.validation_json)
    assert rep["revision_log"][0]["accepted"] is False
    assert row.status == "needs_review"  # the original, not the broken revision
    assert len(json.loads(row.blueprint_json)["beats"]) == 5


def test_director_prompt_states_listener_limits():
    from app.documentary.blueprint import director_system_prompt

    p = " ".join(director_system_prompt().split())
    cfg = ai_config.blueprint
    assert f"at most {cfg.max_beat_words} words" in p
    assert f"at most {cfg.max_open_questions} questions open" in p
    assert '"unresolved"' in p
