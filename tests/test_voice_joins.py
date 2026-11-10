"""The joins of a narration: a block is cut by its sound, the silence between two blocks is the configured one (shorter inside a beat of the story), and every
join is measured and checked at the assembly. Only a language that is configured is cut this way."""

from __future__ import annotations

import asyncio

from test_voice_render import FakeTTS, _block

from app.core.ai_config import ai_config
from app.documentary.voice_render import VoiceRenderer


def plan(kinds=("paragraph", "paragraph", "breath")):
    texts = ["او آمد و نشست و گفت.", "بعد رفت و آمد و رفت.", "دورِ تخت نبود و رفت.", "آخرین جمله."]
    sections = ["B01.g01", "B01.g02", "B02.g01", "B02.g02"]
    blocks = []
    for i, (t, sec) in enumerate(zip(texts, sections, strict=True)):
        blocks.append({**_block(f"FA_{sec.replace('.', '_')}_01", sec, t), "pause_after_ms": 629 if i < 3 else None, "pause_after_kind": (kinds + (None,))[i]})
    return {"language": "fa", "blocks": blocks}


def render(tmp_path, monkeypatch, language="fa"):
    monkeypatch.setattr(ai_config.voice, "work_dir", str(tmp_path))
    p = plan()
    p["language"] = language
    return asyncio.run(VoiceRenderer(provider=FakeTTS(), use_asr=False).render(p, case_id=1, story_version_id=1))


def test_the_silence_of_a_join_is_the_configured_one_inside_a_beat_and_between_beats_and_a_breath(tmp_path, monkeypatch):
    manifest = render(tmp_path, monkeypatch)
    edge, join = ai_config.voice.edges["fa"], ai_config.voice.joins["fa"]
    pads = edge.tail_keep_ms + edge.lead_keep_ms
    gaps = {b["block_id"]: b["pause_after_ms"] for b in manifest["timeline"]["blocks"]}
    assert gaps["FA_B01_g01_01"] == join.silence_ms["same_beat"] - pads, "the next block goes on in the same beat: the short silence"
    assert gaps["FA_B01_g02_01"] == join.silence_ms["paragraph"] - pads, "a new beat: the paragraph silence"
    assert gaps["FA_B02_g01_01"] == join.silence_ms["same_beat"] - pads or gaps["FA_B02_g01_01"] == join.silence_ms["breath"] - pads
    assert gaps["FA_B02_g02_01"] == 0, "the last block has no pause after it"


def test_a_block_is_cut_by_its_sound_with_the_silence_around_it_kept_and_every_join_is_measured_and_checked(tmp_path, monkeypatch):
    manifest = render(tmp_path, monkeypatch)
    joins = manifest["joins"]
    edge, config = ai_config.voice.edges["fa"], ai_config.voice.joins["fa"]
    assert joins["checked"] == 3 and joins["flagged"] == [], "a cut by the sound has silence around its sound at every join"
    assert joins["silence_ms"]["max"] <= config.max_silence_ms and joins["silence_ms"]["min"] >= edge.tail_keep_ms + edge.lead_keep_ms
    assert not [f for f in manifest["flags"] if "join_" in f]


def test_a_join_with_a_hard_start_or_too_much_silence_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.voice.joins["fa"], "min_lead_ms", 10_000)          # no block can have this much silence before its sound
    monkeypatch.setattr(ai_config.voice.joins["fa"], "max_silence_ms", 100)
    manifest = render(tmp_path, monkeypatch)
    flags = {f for j in manifest["joins"]["flagged"] for f in j["flags"]}
    assert {"join_hard_start", "join_too_long"} <= flags and any("join_hard_start" in f for f in manifest["flags"])


def test_a_language_that_is_not_configured_keeps_its_cut_and_its_pauses(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.voice, "edges", {})
    monkeypatch.setattr(ai_config.voice, "joins", {})
    manifest = render(tmp_path, monkeypatch)
    assert manifest["joins"]["checked"] == 0
    assert [b["pause_after_ms"] for b in manifest["timeline"]["blocks"][:3]] == [round(629 * ai_config.voice.breath_gap_scale["fa"])] * 3, "the planned pauses (a paragraph and a breath are both scaled by breath_gap_scale), as before"
