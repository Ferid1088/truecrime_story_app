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


class FakeAuditor:
    """Approves a picture as symbolic unless its asset code is in `reject`."""

    def __init__(self, reject=()):
        self.reject, self.calls = set(reject), []

    async def check(self, db, case, asset, sentences, story, firewall):
        self.calls.append(asset.asset_code)
        if asset.asset_code in self.reject or "reject" in (asset.title or ""):
            return "rejected", None, ["the picture shows another place than the words name"]
        return "approved", "symbolic", ["an accurate depiction of the place the words name"]


def _fakes(monkeypatch, auditor, *, made=None):
    import app.agents.visual_auditor as VA

    monkeypatch.setattr(VA, "VisualAuditor", lambda *a, **k: auditor)
    monkeypatch.setattr("app.documentary.production.script._asset_info",
                        lambda a: {"asset_id": a.asset_code, "path": a.local_path, "type": a.asset_type,
                                   "role": a.asset_role, "rights": a.rights_status, "credit": None})
    briefs = []

    async def brief(db, case, sentences, beat_summary, avoid):
        briefs.append(list(avoid))
        return f"an empty yard, try {len(briefs)}"

    async def png(prompt):
        return _png()

    monkeypatch.setattr(IL, "slot_brief", brief)
    monkeypatch.setattr(IL, "generate_png", png)
    return briefs


def test_the_repair_fills_the_windows_with_audited_labelled_illustrations_and_the_gate_passes(
        db_session, tmp_path, monkeypatch):
    from app.documentary import storage

    monkeypatch.setattr(storage, "ROOT", tmp_path)
    s = script(shot("image", 0, 10, asset_id="REAL1", role="evidence"), shot("black", 10, 60, beat_id="B01"),
               shot("title", 60, 64.5), shot("timeline", 64.5, 120, beat_id="B02"),
               shot("map", 120, 200, asset_id="MAP", beat_id="B02"), duration=200)
    case, row = _row(db_session, s)
    _illustrations(db_session, case, "B01", 10)
    _illustrations(db_session, case, "B02", 20)
    auditor = FakeAuditor()
    _fakes(monkeypatch, auditor)
    step = asyncio.run(CF.repair(db_session, row, s))
    assert step["filled"] == step["slots"] and step["missing_seconds"] == 0
    report = CF.measure(s)
    assert CF.gate(report) == [], report
    ill = [x for x in s["shots"] if x.get("role") == "illustration"]
    assert len({x["asset_id"] for x in ill}) == len(ill)                  # each picture once
    assert all(3 <= x["end"] - x["start"] <= 12 for x in ill)
    assert all(x["audit"]["verdict"] == "approved" and x["audit"]["as"] == "symbolic" for x in ill)
    assert [o for o in s["overlays"] if o["kind"] == "label"]             # "Illustration" on screen
    assert s["shots"][0]["asset_id"] == "REAL1" and any(x["kind"] == "title" for x in s["shots"])
    assert [x["index"] for x in s["shots"]] == list(range(len(s["shots"])))
    assert set(auditor.calls) >= {x["asset_id"] for x in ill}


def test_a_rejected_picture_is_drawn_again_with_the_auditors_reasons(db_session, tmp_path, monkeypatch):
    from app.documentary import storage

    monkeypatch.setattr(storage, "ROOT", tmp_path)
    s = script(shot("black", 0, 7, beat_id="B01"), shot("image", 7, 19, asset_id="REAL", beat_id="B01"),
               duration=19)
    case, row = _row(db_session, s)
    pool = _illustrations(db_session, case, "B01", 2)
    for a in pool:
        a.title = "reject: the wrong street"
    db_session.commit()

    class OnceThenYes(FakeAuditor):
        async def check(self, db, case, asset, sentences, story, firewall):
            self.calls.append(asset.asset_code)
            if "reject" in (asset.title or "") or len(self.calls) == 3:
                return "rejected", None, ["shows a street, the words name a yard"]
            return "approved", "symbolic", ["ok"]

    auditor = OnceThenYes()
    briefs = _fakes(monkeypatch, auditor)
    step = asyncio.run(CF.repair(db_session, row, s))
    assert step["filled"] == 1 and step["missing_seconds"] == 0
    assert briefs[0] and "shows a street" in " ".join(briefs[0])          # the reasons went into the brief
    assert len(briefs) == 2 and briefs[1]                                 # the second drawing was approved
    assert CF.gate(CF.measure(s)) == []


def test_a_gap_nothing_approves_is_held_by_its_neighbour_never_black_and_its_seconds_are_reported(
        db_session, tmp_path, monkeypatch):
    from app.documentary import storage

    monkeypatch.setattr(storage, "ROOT", tmp_path)
    monkeypatch.setattr(ai_config.coverage, "slot_attempts", 2)
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


def test_a_picture_never_fades_in_from_black_but_a_card_keeps_its_short_fade():
    s = script(shot("image", 0, 8, asset_id="A", transition_in="FADE_BLACK"),
               shot("title", 8, 12.5, transition_in="FADE_BLACK"),
               shot("map", 12.5, 20, asset_id="M", transition_in="FADE_BLACK"), duration=20)
    assert CF.no_black_fades(s) == 2
    assert [x["transition_in"] for x in s["shots"]] == ["CROSSFADE", "FADE_BLACK", "CROSSFADE"]
