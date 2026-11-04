"""The controlled voice experiment: the selection, the estimate, the cap, and that the arms differ only where they should. No real provider is called."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from test_voice_render import FakeTTS, _block

from app.core.ai_config import ai_config
from app.documentary import voice_experiment as VE
from app.documentary.pronunciation_planner import Planner
from app.documentary.reading.voicing import Voicing, WordSignal

HIGH = WordSignal("low", True, False)


def blocks():
    return [_block("FA_a_01", "act1", "این کتاب بود"), _block("FA_b_01", "act1", "او رفت خانه"), _block("FA_c_01", "act1", "از گلوله گفت"), _block("FA_d_01", "act1", "من گرگ زدم")]


def voicing():
    readings = {"FA_a_01": ["in", "ketāb", "bud"], "FA_b_01": ["u", "raft", "khāne"], "FA_c_01": ["az", "gulle", "goft"], "FA_d_01": ["man", "gorg", "zadam"]}
    signals = {"FA_a_01": [WordSignal(), HIGH, WordSignal()], "FA_b_01": [WordSignal()] * 3, "FA_c_01": [WordSignal(), HIGH, WordSignal()], "FA_d_01": [WordSignal()] * 3}
    return Voicing({"گرگ": "gorg"}, readings, signals)


def selection(db_session):
    planner = Planner(db_session, "fa-t-exp", "v1", "m1")
    return VE.select(blocks(), voicing(), planner, frozenset(), {"control": 1, "candidate": 1, "escalated": 1, "approved": 1})


def test_every_group_is_chosen_and_the_arms_differ_only_where_the_planner_decided(db_session):
    by = {c.group: c for c in selection(db_session).blocks}
    assert set(by) == {"control", "candidate", "escalated", "approved"}
    assert by["control"].texts == {"A": "او رفت خانه", "B": "او رفت خانه", "C": "او رفت خانه"}
    assert by["candidate"].texts["A"] == "این کتاب بود" and by["candidate"].texts["C"] == "این کِتاب بود" and by["candidate"].texts["B"] == "این کتاب بود"
    assert set(by["escalated"].texts.values()) == {"از گلوله گفت"}, "an escalated word is left as written in every arm"
    assert by["approved"].texts["A"] == "من گرگ زدم" and by["approved"].texts["B"] == by["approved"].texts["C"] == "من گُرگ زدم"


def test_the_estimate_counts_the_characters_of_every_arm_and_prices_them(db_session):
    sel = selection(db_session)
    est = VE.estimate(sel, "eleven_v4")
    assert est["characters"] == sum(len(t) for c in sel.blocks for t in c.texts.values()) and est["takes"] == len(sel.blocks) * 3
    assert est["estimated_eur"] == pytest.approx(est["characters"] * est["eur_per_1m_characters"] / 1e6, abs=1e-4)
    assert est["cap_eur"] >= est["worst_case_eur"] >= est["estimated_eur"]


def test_a_run_needs_the_owners_confirmation_a_cap_over_the_worst_case_and_room_in_the_case():
    est = {"worst_case_eur": 1.4}
    with pytest.raises(PermissionError):
        VE.guard(est, 2.0, 10.0, 300.0, confirmed=False)
    with pytest.raises(ValueError, match="below the worst-case"):
        VE.guard(est, 1.0, 10.0, 300.0, confirmed=True)
    with pytest.raises(ValueError, match="no room"):
        VE.guard(est, 2.0, 299.0, 300.0, confirmed=True)
    with pytest.raises(ValueError, match="no room"):
        VE.guard(est, 2.0, 300.0, 300.0, confirmed=True)
    VE.guard(est, 2.0, 100.0, 300.0, confirmed=True)


def test_the_counting_provider_stops_the_request_that_would_pass_the_cap():
    tts = FakeTTS()
    provider = VE.CountingProvider(tts, max_chars=20)
    from app.providers.voice import VoiceRequest

    def request(text):
        return VoiceRequest(text=text, voice_id="v", model_id="m", settings={}, seed=1, language="fa", language_code="fa")

    asyncio.run(provider.synthesize(request("a" * 12)))
    with pytest.raises(VE.CapReached):
        asyncio.run(provider.synthesize(request("b" * 12)))
    assert provider.used == 12 and provider.requests == 1 and len(tts.requests) == 1


def test_the_arms_are_voiced_with_the_same_voice_model_settings_and_seed_into_a_folder_of_their_own(db_session, tmp_path):
    sel = selection(db_session)
    plan = {"language": "fa", "blocks": []}
    tts = FakeTTS()
    seen, folders = {}, {}
    production = Path(ai_config.voice.work_dir)
    for arm in VE.ARMS:
        before = len(tts.requests)
        result = asyncio.run(VE.run_arm(arm, db=None, case_id=7, version_id=3, language="fa", plan=plan, selection=sel, voicing=voicing(),
                                        planner_for=lambda: Planner(db_session, "fa-t-exp", "v1", "m1"), provider=tts, work_dir=tmp_path / arm, locators=[], rules=[]))
        seen[arm] = tts.requests[before:]
        folders[arm] = Path(result["folder"])
    assert ai_config.voice.work_dir == str(production), "the work folder is restored"
    for arm in VE.ARMS:
        assert str(tmp_path) in str(folders[arm]) and (folders[arm] / "manifest.json").exists()
        assert {(r.voice_id, r.model_id, r.seed, str(r.settings)) for r in seen[arm]} == {(seen["A"][0].voice_id, seen["A"][0].model_id, seen["A"][0].seed, str(seen["A"][0].settings))}
    texts = {arm: sorted(r.text for r in rs) for arm, rs in seen.items()}
    assert texts["A"] != texts["C"] and "این کِتاب بود" in texts["C"] and "این کتاب بود" in texts["A"]
    report = VE.prosody_report(folders, sel)
    assert set(report["features"]) == {"A", "B", "C"} and report["noise_from_control_blocks"] == 3


def test_the_counting_provider_passes_every_other_call_to_the_real_provider():
    class Inner(FakeTTS):
        async def create_pronunciation_dictionary(self, name, rules):
            return {"id": "d1", "name": name}

    provider = VE.CountingProvider(Inner(), max_chars=10)
    assert asyncio.run(provider.create_pronunciation_dictionary("x", [])) == {"id": "d1", "name": "x"} and provider.used == 0
