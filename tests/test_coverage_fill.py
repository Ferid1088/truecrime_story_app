"""The film's picture track: no long cards, no black, no endless still; repaired with real material."""

import asyncio
import json
import uuid

import pytest

from app.core.ai_config import ai_config
from app.db.models import (
    Case,
    EditorialBlueprint,
    ProductionScript,
    StoryVersion,
    VisualAsset,
    VisualPlan,
)
from app.documentary.visuals import coverage_fill as CF


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
    assert windows[1] == (16.0, 72)
    covered = sum(b - a for a, b in windows)
    shots = CF._cut(sorted(s["shots"], key=lambda x: x["start"]), windows)
    kept_cards = sum(x["end"] - x["start"] for x in shots if x["kind"] in CF.CARD_KINDS)
    assert kept_cards <= 6.01 and covered > 150
    assert any(a >= 84 - 1e-6 for a, _ in windows)       # the endless map gets a window after 12 s


def _illustrations(db, case, beat, n):
    """Old generated illustrations as stored by earlier films (they are read and rendered, never made)."""
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


class FakeAuditor:
    """Approves a picture as symbolic unless its asset code is in `reject`."""

    def __init__(self, reject=()):
        self.reject, self.calls = set(reject), []

    async def check(self, db, case, asset, sentences, story, firewall):
        self.calls.append(asset.asset_code)
        if asset.asset_code in self.reject or "reject" in (asset.title or ""):
            return "rejected", None, ["the picture shows another place than the words name"]
        return "approved", "symbolic", ["an accurate depiction of the place the words name"]


def _fakes(monkeypatch, auditor):
    import app.agents.visual_auditor as VA

    monkeypatch.setattr(VA, "VisualAuditor", lambda *a, **k: auditor)
    monkeypatch.setattr("app.documentary.production.script._asset_info",
                        lambda a: {"asset_id": a.asset_code, "path": a.local_path, "type": a.asset_type,
                                   "role": a.asset_role, "rights": a.rights_status, "credit": None})


def _real_pictures(db, case, n, role="context", **kw):
    """Verified real photos of the case that no shot uses yet."""
    out = []
    for _ in range(n):
        code = f"REAL_{uuid.uuid4().hex[:8]}"
        a = VisualAsset(case_id=case.id, asset_code=code, asset_type="photo", provider="wikimedia",
                        asset_role=role, rights_status="public_domain", verification_status="verified",
                        title=f"Real {code}", local_path=f"x/{code}.jpg", width=1600, height=900,
                        relevance_tier=3, entity_type=None, **kw)
        db.add(a)
        out.append(a)
    db.commit()
    return out


def test_the_repair_fills_the_windows_with_audited_real_pictures_and_the_gate_passes(
        db_session, tmp_path, monkeypatch):
    from app.documentary import storage

    monkeypatch.setattr(storage, "ROOT", tmp_path)
    s = script(shot("image", 0, 10, asset_id="REAL1", role="evidence"), shot("black", 10, 60, beat_id="B01"),
               shot("title", 60, 64.5), shot("timeline", 64.5, 120, beat_id="B02"),
               shot("map", 120, 200, asset_id="MAP", beat_id="B02"), duration=200)
    case, row = _row(db_session, s)
    _real_pictures(db_session, case, 30)
    auditor = FakeAuditor()
    _fakes(monkeypatch, auditor)
    step = asyncio.run(CF.repair(db_session, row, s))
    assert step["filled"] == step["slots"] and step["missing_seconds"] == 0
    report = CF.measure(s)
    assert CF.gate(report) == [], report
    fills = [x for x in s["shots"] if x.get("why", "").startswith("coverage repair")]
    assert len({x["asset_id"] for x in fills}) == len(fills)                  # each picture once
    assert all(3 <= x["end"] - x["start"] <= ai_config.coverage.max_still_seconds for x in fills)
    assert all(x["audit"]["verdict"] == "approved" for x in fills)
    assert not [o for o in s["overlays"] if o["kind"] == "label"]             # nothing is a drawing
    assert s["shots"][0]["asset_id"] == "REAL1" and any(x["kind"] == "title" for x in s["shots"])
    assert [x["index"] for x in s["shots"]] == list(range(len(s["shots"])))
    assert set(auditor.calls) >= {x["asset_id"] for x in fills}


def test_the_beats_own_pictures_come_first_then_any_other_real_picture(db_session, tmp_path, monkeypatch):
    from app.documentary import storage

    monkeypatch.setattr(storage, "ROOT", tmp_path)
    s = script(shot("black", 0, 9.5, beat_id="B01"), shot("image", 9.5, 20, asset_id="REAL", beat_id="B01"),
               duration=20)
    case, row = _row(db_session, s)
    own, other = _real_pictures(db_session, case, 2)
    plan = db_session.get(VisualPlan, row.visual_plan_id)
    plan.plan_json = json.dumps({"candidates": {"B01": [own.asset_code]}})
    db_session.commit()
    _fakes(monkeypatch, FakeAuditor())
    asyncio.run(CF.repair(db_session, row, s))
    assert [x["asset_id"] for x in s["shots"] if x.get("why", "").startswith("coverage repair")] == [own.asset_code]


def test_a_rejected_real_picture_is_not_used_and_the_next_one_is_tried(db_session, tmp_path, monkeypatch):
    from app.documentary import storage

    monkeypatch.setattr(storage, "ROOT", tmp_path)
    s = script(shot("black", 0, 9.5, beat_id="B01"), shot("image", 9.5, 21, asset_id="REAL", beat_id="B01"),
               duration=21)
    case, row = _row(db_session, s)
    bad, good = _real_pictures(db_session, case, 2)
    bad.title = "reject: the wrong street"
    db_session.commit()
    plan = db_session.get(VisualPlan, row.visual_plan_id)
    plan.plan_json = json.dumps({"candidates": {"B01": [bad.asset_code, good.asset_code]}})
    db_session.commit()
    auditor = FakeAuditor()
    _fakes(monkeypatch, auditor)
    step = asyncio.run(CF.repair(db_session, row, s))
    assert step["filled"] == 1 and auditor.calls == [bad.asset_code, good.asset_code]
    assert [x["asset_id"] for x in s["shots"] if x.get("why", "").startswith("coverage repair")] == [good.asset_code]


def test_a_gap_nothing_approves_is_held_by_its_neighbour_never_black_and_its_seconds_are_reported(
        db_session, tmp_path, monkeypatch):
    from app.documentary import storage

    monkeypatch.setattr(storage, "ROOT", tmp_path)
    s = script(shot("image", 0, 12, asset_id="REAL", beat_id="B01"), shot("black", 12, 19, beat_id="B01"),
               shot("image", 19, 31, asset_id="REAL2", beat_id="B01"), duration=31)
    case, row = _row(db_session, s)

    class Never(FakeAuditor):
        async def check(self, db, case, asset, sentences, story, firewall):
            return "rejected", None, ["no"]

    _fakes(monkeypatch, Never())
    step = asyncio.run(CF.repair(db_session, row, s))
    assert step["filled"] == 0 and step["missing_seconds"] == 7 and step["held"] == 1
    assert CF.measure(s)["black_seconds"] == 0
    assert any("no approved picture" in str(h) for x in s["shots"] for h in x.get("held", []))


def test_with_no_real_picture_for_a_beat_nothing_is_generated_and_the_shortage_is_reported(
        db_session, tmp_path, monkeypatch):
    """No generation call exists any more: the repair never reaches for a drawing."""
    import httpx

    from app.documentary import storage

    monkeypatch.setattr(storage, "ROOT", tmp_path)
    calls = []
    real_post = httpx.AsyncClient.post

    async def watch(self, url, *a, **k):
        calls.append(str(url))
        return await real_post(self, url, *a, **k)

    monkeypatch.setattr(httpx.AsyncClient, "post", watch)
    s = script(shot("image", 0, 12, asset_id="REAL", beat_id="B01"), shot("black", 12, 40, beat_id="B01"),
               duration=40)
    case, row = _row(db_session, s)
    _fakes(monkeypatch, FakeAuditor())
    step = asyncio.run(CF.repair(db_session, row, s))
    assert calls == [] and step["filled"] == 0 and step["missing_seconds"] == 28
    assert not [x for x in s["shots"] if x.get("provider") == "generated"]
    assert CF.measure(s)["black_seconds"] == 0


def test_the_production_path_has_no_image_generation_left():
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    assert not (root / "app/documentary/visuals/illustrations.py").exists()
    text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in (root / "app").rglob("*.py"))
    assert "images/generations" not in text and "ai_config.illustrations" not in text
    assert "illustration_briefs" not in (root / "config/agents.json").read_text()
    assert "illustrations" not in json.loads((root / "config/parameters/visuals.json").read_text())
    assert "images" not in json.loads((root / "config/parameters/pricing.json").read_text())


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


def test_a_picture_never_fades_in_from_black_but_a_card_keeps_its_short_fade():
    s = script(shot("image", 0, 8, asset_id="A", transition_in="FADE_BLACK"),
               shot("title", 8, 12.5, transition_in="FADE_BLACK"),
               shot("map", 12.5, 20, asset_id="M", transition_in="FADE_BLACK"), duration=20)
    assert CF.no_black_fades(s) == 2
    assert [x["transition_in"] for x in s["shots"]] == ["CROSSFADE", "FADE_BLACK", "CROSSFADE"]
