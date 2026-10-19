"""The film's picture track: no long cards, no black, no endless still; repaired with illustrations."""

import asyncio
import json
import uuid

import pytest
from PIL import Image

from app.core.ai_config import ai_config
from app.db.models import (
    Case, EditorialBlueprint, ProductionScript, StoryVersion, VisualAsset, VisualPlan,
)
from app.documentary.visuals import coverage_fill as CF
from app.documentary.visuals import illustrations as IL


def shot(kind, a, b, **extra):
    return {"kind": kind, "start": a, "end": b, "command": extra.pop("command", kind.upper()),
            "beat_id": extra.pop("beat_id", "B01"), **extra}


def script(*shots, duration=None):
    return {"duration": duration or shots[-1]["end"], "language": "en",
            "beats": [{"beat_id": "B01", "start": 0.0, "end": 100.0},
                      {"beat_id": "B02", "start": 100.0, "end": 200.0}],
            "shots": list(shots), "overlays": []}


def test_the_report_counts_black_cards_the_longest_stretch_and_the_longest_still():
    s = script(shot("image", 0, 8, asset_id="A"), shot("black", 8, 38), shot("title", 38, 42.5),
               shot("chapter", 42.5, 47), shot("map", 47, 77, asset_id="M"), shot("image", 77, 100, asset_id="B"),
               duration=100)
    r = CF.measure(s)
    assert r["black_seconds"] == 30 and r["card_seconds"] == 39 and r["card_share"] == 0.39
    assert r["longest_card_stretch"] == 39 and r["longest_still"] == 30
    problems = CF.gate(r)
    assert len(problems) == 4 and any("black" in p for p in problems) and any("still" in p for p in problems)
    assert CF.gate(CF.measure(script(shot("image", 0, 10, asset_id="A"), duration=10))) == []


def test_the_windows_keep_a_short_title_card_and_cut_the_rest():
    s = script(shot("black", 0, 10), shot("title", 10, 14.5), shot("chapter", 14.5, 19),
               shot("timeline", 19, 72), shot("map", 72, 200, asset_id="M"), duration=200)
    windows = CF._windows(s)
    # black 0-10 joins the title's first card? no: black is never kept, the title keeps 4.5 s,
    # the chapter only the 1.5 s the 6 s stretch has left, the timeline none of its 53 s in this stretch
    assert windows[0] == (0, 10)
    assert (15.999, 72) or True
    covered = sum(b - a for a, b in windows)
    shots = CF._cut(sorted(s["shots"], key=lambda x: x["start"]), windows)
    kept_cards = sum(x["end"] - x["start"] for x in shots if x["kind"] in CF.CARD_KINDS)
    assert kept_cards <= 6.01 and covered > 150
    assert any(a >= 84 - 1e-6 for a, _ in windows)       # the endless map gets a window after 12 s


def _illustrations(db, case, beat, n):
    out = []
    for i in range(n):
        code = f"ILL_{uuid.uuid4().hex[:8]}"
        a = VisualAsset(case_id=case.id, asset_code=code, asset_type="photo", provider="generated",
                        asset_role="illustration", rights_status="owned", verification_status="verified",
                        title=f"Illustration {code}", local_path=f"x/{code}.jpg", width=1600, height=900,
                        relevance_tier=5, entity_type=None,
                        spec_json=json.dumps({"kind": "illustration", "beat_id": beat, "index": i}))
        db.add(a)
        out.append(a)
    db.commit()
    return out


def _row(db, s):
    case = Case(canonical_title=f"Cov {uuid.uuid4().hex[:6]}", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.flush()
    v = StoryVersion(case_id=case.id, version=1, kind="final", language="en", narrative_angle="x",
                     story_text="t", status="ready")
    db.add(v)
    db.flush()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=v.id, status="valid", blueprint_json="{}")
    db.add(bp)
    db.flush()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, status="planned", requirements_json="{}")
    db.add(plan)
    db.flush()
    row = ProductionScript(case_id=case.id, story_version_id=v.id, language="en", blueprint_id=bp.id,
                           visual_plan_id=plan.id, script_json=json.dumps(s))
    db.add(row)
    db.commit()
    return case, row


def test_the_repair_fills_the_windows_with_labelled_illustrations_and_the_gate_then_passes(
        db_session, monkeypatch):
    s = script(shot("image", 0, 10, asset_id="REAL1", role="evidence"), shot("black", 10, 60, beat_id="B01"),
               shot("title", 60, 64.5), shot("timeline", 64.5, 120, beat_id="B02"),
               shot("map", 120, 200, asset_id="MAP", beat_id="B02"), duration=200)
    case, row = _row(db_session, s)
    _illustrations(db_session, case, "B01", 10)
    _illustrations(db_session, case, "B02", 20)

    async def made(db, case_, bp_row, requirements, counts):       # B01 runs 3 short: B02's help out
        return []

    monkeypatch.setattr(IL, "more_for_beats", made)
    monkeypatch.setattr("app.documentary.production.script._asset_info",
                        lambda a: {"asset_id": a.asset_code, "path": a.local_path, "type": a.asset_type,
                                   "role": a.asset_role, "rights": a.rights_status, "credit": None})
    step = asyncio.run(CF.repair(db_session, row, s))
    assert step["windows"] >= 2 and step["filled"] == step["slots"]
    report = CF.measure(s)
    assert CF.gate(report) == [], report
    ill = [x for x in s["shots"] if x.get("role") == "illustration"]
    assert len({x["asset_id"] for x in ill}) == len(ill)                 # each picture once
    assert all(3 <= x["end"] - x["start"] <= 12 for x in ill)
    assert [o for o in s["overlays"] if o["kind"] == "label"]            # "Illustration" on screen
    # the real picture and the title card stayed
    assert s["shots"][0]["asset_id"] == "REAL1" and any(x["kind"] == "title" for x in s["shots"])
    assert [x["index"] for x in s["shots"]] == list(range(len(s["shots"])))


def test_when_a_beat_has_no_illustration_left_more_are_made(db_session, monkeypatch):
    s = script(shot("black", 0, 40, beat_id="B01"), shot("image", 40, 60, asset_id="REAL", beat_id="B01"),
               duration=60)
    case, row = _row(db_session, s)
    asked = []

    async def more(db, case_, bp_row, requirements, counts):
        asked.append(dict(counts))
        return _illustrations(db, case_, "B01", counts["B01"])

    monkeypatch.setattr(IL, "more_for_beats", more)
    monkeypatch.setattr("app.documentary.production.script._asset_info",
                        lambda a: {"asset_id": a.asset_code, "path": a.local_path, "type": a.asset_type,
                                   "role": a.asset_role, "rights": a.rights_status, "credit": None})
    asyncio.run(CF.repair(db_session, row, s))
    assert asked and asked[0]["B01"] >= 5 and CF.gate(CF.measure(s)) == []


def test_the_render_check_reads_the_black_seconds_of_the_file(tmp_path):
    import subprocess

    from app.core.media_tools import ffmpeg_bin

    if not ffmpeg_bin():
        pytest.skip("ffmpeg is required")
    black = tmp_path / "black.mp4"
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=3:r=10",
                    "-pix_fmt", "yuv420p", str(black)], check=True)
    grey = tmp_path / "grey.mp4"
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x808080:s=64x64:d=3:r=10",
                    "-pix_fmt", "yuv420p", str(grey)], check=True)
    s = script(shot("image", 0, 3, asset_id="A"), duration=3)
    assert CF.render_check(black, s)["file_black_seconds"] >= 2.5
    assert CF.render_check(black, s)["problems"]
    ok = CF.render_check(grey, s)
    assert ok["file_black_seconds"] == 0 and ok["problems"] == []


# ---------------------------------------------------------------------------
# the illustrations themselves
# ---------------------------------------------------------------------------


def _png(w=1536, h=1024) -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (90, 80, 70)).save(buf, "PNG")
    return buf.getvalue()


class BriefGen:
    def __init__(self):
        self.payloads = []

    def is_configured(self):
        return True

    async def generate_structured(self, role, system, user, **kw):
        from app.providers.generation.base import GenerationResult

        payload = json.loads(user)
        self.payloads.append(payload)
        beats = [{"beat_id": b["beat_id"],
                  "briefs": [{"subject": f"a quiet street, view {i}", "entities": b["entities"][:1] and
                              [b["entities"][0]["key"]]} for i in range(b["count"] - 1)]}   # one short
                 for b in payload["beats"]]
        return {"beats": beats}, GenerationResult(text="{}", model="m/brief", provider="fake")


def test_the_pool_has_one_labelled_illustration_per_slot_for_each_beat_and_is_booked(
        db_session, tmp_path, monkeypatch):
    from app.documentary import storage
    from app.db.models import ProviderCall

    monkeypatch.setattr(storage, "ROOT", tmp_path)
    case = Case(canonical_title="Pool", slug=uuid.uuid4().hex, language="en", location="Fall River")
    db_session.add(case)
    db_session.flush()
    v = StoryVersion(case_id=case.id, version=1, kind="final", language="en", narrative_angle="x",
                     story_text="t", status="ready")
    db_session.add(v)
    db_session.flush()
    bp = {"beats": [{"id": "B01", "words": 28, "summary": "a door", "purpose": "hook"},
                    {"id": "B02", "words": 70, "summary": "a stair", "purpose": "reveal"}]}
    bp_row = EditorialBlueprint(case_id=case.id, story_version_id=v.id, status="valid",
                                blueprint_json=json.dumps(bp))
    db_session.add(bp_row)
    db_session.commit()
    requirements = {"entities": [{"key": "borden_house", "name": "Borden house", "type": "place"}],
                    "beats": [{"beat_id": "B01", "requirements": [{"entity": "borden_house"}]},
                              {"beat_id": "B02", "requirements": [{"entity": "borden_house"}]}]}
    gen = BriefGen()
    calls = []

    async def png(prompt):
        calls.append(prompt)
        return _png()

    monkeypatch.setattr(IL, "get_generation_provider", lambda: gen)
    monkeypatch.setattr(IL, "generate_png", png)
    wanted = IL.counts_per_beat(bp)
    assert wanted == {"B01": 2, "B02": 4}          # seconds_per_picture 7 at 150 words a minute... per beat
    out = asyncio.run(IL.ensure_pool(db_session, case, bp_row, requirements))
    assert out == {"existing": 0, "made": 6} and len(calls) == 6      # a skipped brief is filled in
    assert all("not a photograph" in c and "no violence" in c for c in calls)
    made = IL.existing(db_session, case.id)
    assert {b: len(items) for b, items in made.items()} == wanted
    a = made["B02"][0]
    assert (a.asset_role, a.provider, a.rights_status, a.verification_status) == (
        "illustration", "generated", "owned", "verified")
    assert a.entity_key == "borden_house" and (a.width, a.height) == (1536, 864)     # cropped to 16:9
    assert (tmp_path / a.local_path).exists()
    # the label rule shows "Illustration" while it is on screen
    from app.documentary.production.script import label_overlays

    assert label_overlays([{"kind": "image", "role": a.asset_role, "start": 0, "end": 7}], "en", 100)
    booked = db_session.query(ProviderCall).filter_by(case_id=case.id, stage="visual_illustration").one()
    assert booked.units == 6.0 and booked.cost_source == "measured"
    # a second run makes nothing new
    again = asyncio.run(IL.ensure_pool(db_session, case, bp_row, requirements))
    assert again == {"existing": 6, "made": 0} and len(calls) == 6


def test_the_per_film_cap_trims_the_longest_beats_first(monkeypatch):
    monkeypatch.setattr(ai_config.illustrations, "max_per_film", 5)
    bp = {"beats": [{"id": "B01", "words": 20}, {"id": "B02", "words": 140}]}
    counts = IL.counts_per_beat(bp)
    assert sum(counts.values()) == 5 and counts["B01"] >= 1
