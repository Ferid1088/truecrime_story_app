"""The ways besides illustrations that fill a gap in the picture track (visuals.fill_ways)."""

import asyncio
import json
import uuid

from app.core.ai_config import ai_config
from app.db.models import VisualAsset
from app.documentary import storage
from app.documentary.visuals import crops
from app.documentary.visuals import fill_ways as FW
from tests.test_coverage_fill import FakeAuditor, _illustrations, _row, script, shot

BLUEPRINT = {"beats": [
    {"id": "B01", "purpose": "hook", "reveals": ["F001", "F002"], "viewer_knows": ["F001", "F002"]},
    {"id": "B02", "purpose": "contradiction", "reveals": ["F003", "C001"],
     "viewer_knows": ["C001", "F001", "F002", "F003"]},
    {"id": "B03", "purpose": "transition", "reveals": ["F004"],
     "viewer_knows": ["C001", "F001", "F002", "F003", "F004"]}]}
PACK = {"facts": [{"id": f"F00{i}", "claim": f"Fact number {i} was told."} for i in range(1, 5)],
        "contradictions": [{"id": "C001", "topic": "the laughter", "description": "A says x, B says y."}]}


def _real(db, case, entities=("lizzie",)):
    code = f"REAL_{uuid.uuid4().hex[:8]}"
    a = VisualAsset(case_id=case.id, asset_code=code, asset_type="photo", provider="wikimedia",
                    asset_role="evidence", rights_status="public_domain", verification_status="verified",
                    title="Portrait", local_path=f"x/{code}.jpg", width=1200, height=800,
                    entities_json=json.dumps(list(entities)))
    db.add(a)
    db.commit()
    return a


def ways(db_session, tmp_path, monkeypatch, shots, subtitles=(), auditor=None, **config):
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    for k, v in config.items():
        monkeypatch.setattr(ai_config.fill_ways, k, v)
    s = script(*shots, duration=400)
    s["subtitles"] = list(subtitles)
    case, _ = _row(db_session, s)
    for sh in shots:                                   # the earlier pictures belong to this case
        if sh.get("asset_id"):
            db_session.query(VisualAsset).filter_by(asset_code=sh["asset_id"]).update({"case_id": case.id})
    db_session.commit()
    fw = FW.FillWays(db_session, case, s, BLUEPRINT, shots, auditor or FakeAuditor())
    fw._pack = PACK
    return fw


def run(coro):
    return asyncio.run(coro)


def slot(fw, a, b, beat="B01", sentences=("Words.",), names=()):
    return run(fw.try_slot((a, b), beat, list(sentences), {}, None, set(names)))


def test_a_sentence_with_a_quotation_or_a_number_is_worth_setting_large():
    assert FW.quote_worthy("She said, \"I was in the barn.\"") and FW.quote_worthy("It was about 11:15 that morning.")
    assert not FW.quote_worthy("She walked into the room slowly.") and not FW.quote_worthy("Ok 1")


def test_a_close_up_shows_a_different_part_of_the_picture_each_time():
    boxes = {tuple(crops.region_box(1200, 800, n)) for n in range(5)}
    assert len(boxes) == 5 and all(b[2] > b[0] and b[3] > b[1] for b in boxes)


def test_a_real_picture_returns_as_a_close_up_after_the_gap_and_is_audited(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    auditor = FakeAuditor()
    earlier = [shot("image", 0, 10, asset_id="X", role="evidence")]
    case, _ = _row(db_session, script(*earlier, duration=400))
    a = _real(db_session, case)
    earlier[0]["asset_id"] = a.asset_code
    fw = FW.FillWays(db_session, case, script(*earlier, duration=400), BLUEPRINT, earlier, auditor)
    assert run(fw.try_slot((20, 27), "B01", ["Words."], {}, None, {"lizzie"})) is None      # inside the gap
    got = run(fw.try_slot((120, 127), "B01", ["Words."], {}, None, {"lizzie"}))
    assert got["way"] == "reframe" and got["asset"].asset_code == a.asset_code
    assert auditor.calls == [a.asset_code]
    assert run(fw.try_slot((122, 129), "B01", ["Words."], {}, None, {"lizzie"})) is None    # too close to its last return
    assert fw.returns[a.asset_code] == 1


def test_a_picture_comes_back_only_so_often_and_never_when_rejected(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    s0 = [shot("image", 0, 10, asset_id="X", role="evidence")]
    case, _ = _row(db_session, script(*s0, duration=400))
    a = _real(db_session, case)
    s0[0]["asset_id"] = a.asset_code
    fw = FW.FillWays(db_session, case, script(*s0, duration=400), BLUEPRINT, s0, FakeAuditor(reject=[a.asset_code]))
    assert run(fw.try_slot((120, 127), "B01", ["Words."], {}, None, set())) is None
    ok = FW.FillWays(db_session, case, script(*s0, duration=400), BLUEPRINT, s0, FakeAuditor())
    for start in (120, 200, 300):
        got = run(ok.try_slot((start, start + 7), "B01", ["Words."], {}, None, set()))
        assert (got is not None) == (start != 300)                       # max_reuse_per_picture is 2


def test_a_generated_picture_never_returns_as_a_close_up(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    s0 = [shot("image", 0, 10, asset_id="X", role="illustration")]
    case, _ = _row(db_session, script(*s0, duration=400))
    a = _illustrations(db_session, case, "B01", 1)[0]
    s0[0]["asset_id"] = a.asset_code
    fw = FW.FillWays(db_session, case, script(*s0, duration=400), BLUEPRINT, s0, FakeAuditor())
    assert run(fw.try_slot((120, 127), "B01", ["Words."], {}, None, set())) is None


def test_a_spoken_sentence_with_a_number_becomes_a_text_scene_once_per_cap(db_session, tmp_path, monkeypatch):
    cues = [{"start": 100.0, "end": 106.0, "text": "It was about half past ten on the morning of August 4, 1892."},
            {"start": 200.0, "end": 206.0, "text": "She walked into the room slowly and sat."}]
    fw = ways(db_session, tmp_path, monkeypatch, [shot("image", 0, 10, asset_id="Z")], cues)
    got = slot(fw, 100, 106)
    assert got["way"] == "text" and got["asset"].provider == "generated" and got["asset"].asset_role == "context"
    assert slot(fw, 200, 206) is None                                    # nothing quote-worthy is said
    again = slot(fw, 100, 106)
    assert again["asset"].asset_code == got["asset"].asset_code          # the same card is reused, not drawn twice


def test_the_text_scene_cap_per_film_is_kept(db_session, tmp_path, monkeypatch):
    cues = [{"start": 100.0 * i, "end": 100.0 * i + 6, "text": f"At {i} o'clock the bell rang twice."} for i in range(1, 4)]
    fw = ways(db_session, tmp_path, monkeypatch, [shot("image", 0, 10, asset_id="Z")], cues)
    monkeypatch.setitem(ai_config.fill_ways.max_per_film, "text", 2)
    got = [slot(fw, 100.0 * i, 100.0 * i + 6) for i in range(1, 4)]
    assert [g is not None for g in got] == [True, True, False]


def test_two_accounts_come_in_the_beat_that_tells_the_contradiction_once(db_session, tmp_path, monkeypatch):
    fw = ways(db_session, tmp_path, monkeypatch, [shot("image", 0, 10, asset_id="Z")])

    async def sides(c):
        return {"left": {"who": "The maid", "says": "She heard laughter."},
                "right": {"who": "Lizzie", "says": "She denied it."}}

    monkeypatch.setattr(fw, "_sides", sides)
    assert slot(fw, 50, 57, beat="B01") is None                          # B01 tells no contradiction
    got = slot(fw, 100, 107, beat="B02")
    assert got["way"] == "split" and "the laughter" in got["reasons"][0]
    assert slot(fw, 110, 117, beat="B02") is None                        # told once


def test_two_accounts_are_skipped_when_the_case_file_holds_no_two_opposing_accounts(db_session, tmp_path, monkeypatch):
    fw = ways(db_session, tmp_path, monkeypatch, [shot("image", 0, 10, asset_id="Z")])

    async def none(c):
        return None

    monkeypatch.setattr(fw, "_sides", none)
    assert slot(fw, 100, 107, beat="B02") is None


def test_a_gap_with_no_way_falls_through_to_the_illustration(db_session, tmp_path, monkeypatch):
    fw = ways(db_session, tmp_path, monkeypatch, [shot("image", 0, 10, asset_id="Z")])
    assert slot(fw, 50, 57, beat="B01") is None


def test_the_way_shot_of_a_close_up_is_a_justified_return_with_its_own_region(db_session, tmp_path, monkeypatch):
    from app.documentary.visuals import coverage_fill as CF

    monkeypatch.setattr("app.documentary.production.script._asset_info",
                        lambda a: {"asset_id": a.asset_code, "path": a.local_path, "type": a.asset_type,
                                   "role": a.asset_role, "rights": a.rights_status, "credit": None})
    case, _ = _row(db_session, script(shot("image", 0, 10, asset_id="X"), duration=10))
    a = _real(db_session, case)
    out = CF._way_shot({"asset": a, "way": "reframe", "region": 1, "reasons": ["ok"]}, 120, 127, "B01", 0)
    assert out["command"] == "CROP_EXISTING" and out["motion"] == "CROP_FOCUS" and out["reframe"] is True
    assert out["repeat_justified"] is True and out["role"] == "evidence" and len(out["focus"]) == 4
    card = CF._way_shot({"asset": a, "way": "text", "reasons": ["the spoken sentence"]}, 120, 127, "B01", 0)
    assert card["command"] == "NEW_IMAGE" and "reframe" not in card


def test_the_repair_uses_a_fill_way_when_no_unused_real_picture_exists(db_session, tmp_path, monkeypatch):
    from app.documentary.visuals import coverage_fill as CF
    from tests.test_coverage_fill import _fakes

    monkeypatch.setattr(storage, "ROOT", tmp_path)
    s = script(shot("image", 0, 10, asset_id="REAL1", role="evidence"), shot("black", 10, 17, beat_id="B01"),
               shot("image", 17, 40, asset_id="REAL2", role="evidence"), duration=40)
    s["subtitles"] = [{"start": 10.0, "end": 17.0, "text": "At 11:15 the police were called."}]
    case, row = _row(db_session, s)
    _fakes(monkeypatch, FakeAuditor())
    step = asyncio.run(CF.repair(db_session, row, s))
    assert step["filled"] == 1 and step["ways"] == {"text": 1}
    card = next(x for x in s["shots"] if x.get("fill_reason", "").startswith("fill way text"))
    assert card["kind"] == "image" and card["role"] == "context" and (card["start"], card["end"]) == (10, 17)
    assert CF.gate(CF.measure(s)) == []


def test_each_return_of_a_picture_shows_a_region_it_has_not_shown_before(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    s0 = [shot("image", 0, 10, asset_id="X", role="evidence")]
    case, _ = _row(db_session, script(*s0, duration=900))
    a = _real(db_session, case)
    s0[0]["asset_id"] = a.asset_code
    monkeypatch.setattr(ai_config.fill_ways, "max_reuse_per_picture", 4)
    monkeypatch.setattr(ai_config.visual_direction, "max_evidence_appearances", 5)
    fw = FW.FillWays(db_session, case, script(*s0, duration=900), BLUEPRINT, s0, FakeAuditor())
    regions = []
    for start in (100, 200, 300, 400):
        got = run(fw.try_slot((start, start + 7), "B01", ["Words."], {}, None, set()))
        regions.append(got["region"])
    assert len(set(regions)) == 4


def test_a_picture_never_comes_back_beyond_its_appearance_limit(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    monkeypatch.setattr(ai_config.fill_ways, "max_reuse_per_picture", 5)
    monkeypatch.setattr(ai_config.visual_direction, "max_evidence_appearances", 2)
    s0 = [shot("image", 0, 10, asset_id="X", role="evidence")]
    case, _ = _row(db_session, script(*s0, duration=900))
    a = _real(db_session, case)
    s0[0]["asset_id"] = a.asset_code
    fw = FW.FillWays(db_session, case, script(*s0, duration=900), BLUEPRINT, s0, FakeAuditor())
    assert run(fw.try_slot((100, 107), "B01", ["Words."], {}, None, set())) is not None
    assert run(fw.try_slot((200, 207), "B01", ["Words."], {}, None, set())) is None


def test_a_region_a_shot_already_shows_is_not_given_again(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    s0 = [shot("image", 0, 10, asset_id="X", role="evidence"),
          shot("image", 100, 107, asset_id="X", role="evidence", crop_region=0)]
    case, _ = _row(db_session, script(*s0, duration=900))
    a = _real(db_session, case)
    for x in s0:
        x["asset_id"] = a.asset_code
    fw = FW.FillWays(db_session, case, script(*s0, duration=900), BLUEPRINT, s0, FakeAuditor())
    got = run(fw.try_slot((200, 207), "B01", ["Words."], {}, None, set()))
    assert got["region"] == 1


def test_a_picture_the_check_found_a_watermark_on_is_never_cropped_to_hide_it(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    s0 = [shot("image", 0, 10, asset_id="X", role="evidence")]
    case, _ = _row(db_session, script(*s0, duration=400))
    a = _real(db_session, case)
    a.verification_json = json.dumps({"watermark": True})
    db_session.commit()
    s0[0]["asset_id"] = a.asset_code
    fw = FW.FillWays(db_session, case, script(*s0, duration=400), BLUEPRINT, s0, FakeAuditor())
    assert run(fw.try_slot((120, 127), "B01", ["Words."], {}, None, set())) is None


def test_the_close_up_carries_the_words_it_illustrates_as_its_anchor(db_session, tmp_path, monkeypatch):
    from app.documentary.visuals import coverage_fill as CF

    monkeypatch.setattr(storage, "ROOT", tmp_path)
    monkeypatch.setattr("app.documentary.production.script._asset_info",
                        lambda a: {"asset_id": a.asset_code, "path": a.local_path, "type": a.asset_type,
                                   "role": a.asset_role, "rights": a.rights_status, "credit": None})
    case, _ = _row(db_session, script(shot("image", 0, 10, asset_id="X"), duration=10))
    a = _real(db_session, case)
    out = CF._way_shot({"asset": a, "way": "reframe", "region": 2, "reasons": ["ok"],
                        "anchor": "She walked to the barn."}, 120, 127, "B01", 0)
    assert out["crop_anchor"] == "She walked to the barn." and out["crop_region"] == 2


def test_no_what_we_know_so_far_card_is_ever_made():
    """Owner decision 2026-10-21: that fact-list card is trash; the detective board is the way to show facts."""
    assert "board" not in ai_config.fill_ways.order and "board" not in ai_config.fill_ways.max_per_film
    assert not hasattr(FW.FillWays, "_board") and not hasattr(FW, "known_facts")
    from app.documentary.visuals import scene_cards

    assert not hasattr(scene_cards, "board")
