"""Story order for pictures and video pieces: nothing on screen may give
away what the story tells only later — the director is told where the
film reveals what, compose and every fill keep the claim firewall, and
the auditors judge each picture/piece at its point in the story (a pause
too). A cut of a video is shown once in a film, never again."""
import asyncio
import json
import uuid

from test_visual_production import _asset

from app.db.models import Case, EditorialBlueprint, ProductionScript, VisualPlan
from app.documentary.production.script import compose
from app.documentary.visuals import auditor as AU
from app.documentary.visuals import spoilers as SP
from app.documentary.visuals.usage import UsageTracker
from app.providers.generation.base import GenerationResult

BP = {"beats": [
    {"id": "B01", "purpose": "orientation", "summary": "The farm outside the village, 1998.",
     "reveals": [], "viewer_knows": []},
    {"id": "B02", "purpose": "investigation", "summary": "Officers search the fields.",
     "reveals": [], "viewer_knows": []},
    {"id": "B03", "purpose": "reveal", "summary": "Police arrest the neighbour.",
     "reveals": ["F002"], "viewer_knows": []},
]}
SENT = {"B01": "The farm lay outside the village.", "B02": "Officers searched the fields.",
        "B03": "Then the police arrested the neighbour."}


def _case(db) -> Case:
    title = f"Order {uuid.uuid4().hex[:6]}"
    case = Case(canonical_title=title, slug=title.lower().replace(" ", "-"), language="en")
    db.add(case)
    db.commit()
    return case


def test_story_point_and_claim_firewall():
    p1, p3 = SP.story_point(BP, "B01"), SP.story_point(BP, "B03")
    assert p1["told_later"] == ["Police arrest the neighbour."]
    assert p3["told_later"] == [] and p3["told_so_far"][-1] == "Police arrest the neighbour."
    assert SP.reveal_blocks(BP) == {"B01": ["F002"], "B02": ["F002"]}
    fw = SP.Firewall(reveals=SP.reveal_blocks(BP))
    arrest = _asset("VIS_000041", reveals_json='["F002"]', description="Neighbour led away.")
    assert "reveals only later (F002)" in fw.why("B01", arrest)
    assert fw.why("B03", arrest) is None                      # told by then
    assert SP.Firewall.from_json(fw.as_json()).reveals == fw.reveals


def test_compose_never_shows_a_later_reveal_early():
    arrest = _asset("VIS_000041", reveals_json='["F002"]', description="Neighbour led away.")
    farm = _asset("VIS_000042", description="The farmhouse.")
    m = {"duration_seconds": 30.0, "files": {"narration_wav": "n.wav"},
         "timeline": {"beats": [{"beat_id": b, "start": k * 10.0, "end": k * 10.0 + 9.5}
                                for k, b in enumerate(("B01", "B02", "B03"))],
                      "blocks": [], "words": [], "sentences": []}}
    plan = {"candidates": {"B01": ["VIS_000041", "VIS_000042"], "B02": ["VIS_000042"],
                           "B03": ["VIS_000041"]},
            "beats": [{"beat_id": b, "shots": [{"command": "NEW_IMAGE", "asset_id": c,
                                                "share": 1.0, "motion": "SLOW_PUSH"}]}
                      for b, c in (("B01", "VIS_000041"), ("B02", "VIS_000042"),
                                   ("B03", "VIS_000041"))]}
    s = compose(m, plan, {"VIS_000041": arrest, "VIS_000042": farm}, {}, "en",
                reveal_blocks=SP.reveal_blocks(BP))
    first = s["shots"][0]
    assert first["asset_id"] == "VIS_000042"
    assert "reveals only later" in first["fill_reason"]
    assert s["shots"][-1]["asset_id"] == "VIS_000041"         # at its own reveal
    assert s["firewall"]["reveals"] == {"B01": ["F002"], "B02": ["F002"]}


def test_a_video_cut_is_shown_only_once():
    tr = UsageTracker({"P1": {"asset_id": "P1", "type": "video", "tier": 2,
                              "entity_type": "person", "entities": ["anna"],
                              "parent": "SRC", "window": [0, 8]}})
    tr.add("P1", 0, 8)
    ok, why = tr.allows("P1", 200, 206)
    assert not ok and "only once" in why
    assert tr.allows("P1", 8.0, 12)[0]                        # still the same playback
    assert tr.pick(["P1"], 200, 206, names={"anna"}) is None  # never a repeat, even named


def test_a_reframed_clip_plays_on_instead_of_again():
    clip = _asset("VIS_000043", asset_type="video", clip_start=10.0, clip_end=30.0,
                  duration_seconds=20.0, local_path="c.mp4")
    m = {"duration_seconds": 16.0, "files": {"narration_wav": "n.wav"},
         "timeline": {"beats": [{"beat_id": "B01", "start": 0.0, "end": 16.0}],
                      "blocks": [], "words": [], "sentences": []}}
    plan = {"candidates": {}, "beats": [{"beat_id": "B01", "shots": [
        {"command": "SHOW_CLIP", "asset_id": "VIS_000043", "share": 0.5},
        {"command": "CROP_EXISTING", "share": 0.5}]}]}
    s = compose(m, plan, {"VIS_000043": clip}, {}, "en")
    videos = [x for x in s["shots"] if x.get("kind") == "video"]
    assert videos[0]["clip_start"] == 10.0
    if len(videos) > 1:                     # (a merged shot simply plays on)
        assert videos[1]["clip_start"] >= 10.0 + (videos[0]["end"] - videos[0]["start"]) - 0.01


class StoryGen:
    """Rejects a picture titled ARREST while the arrest is still to come."""

    def __init__(self):
        self.calls = []

    async def generate_structured(self, role, system, user, images=None):
        data = json.loads(user)
        claim = (data.get("what_the_picture_is_claimed_to_be")
                 or data.get("what_the_piece_is_claimed_to_be"))
        story = data.get("story") or {}
        self.calls.append((claim["title"], tuple(data["narration_while_on_screen"]),
                           tuple(story.get("told_later") or ())))
        assert "STORY ORDER" in system
        spoils = "ARREST" in claim["title"] and any("arrest" in t for t in
                                                    story.get("told_later") or [])
        v = {"verdict": "rejected" if spoils else "approved", "as": "context",
             "fits_words": 0.9 if data["narration_while_on_screen"] else 1.0,
             "specific_kind_ok": True, "tone_ok": True, "person_ok": True,
             "spoiler_free": not spoils,
             "reasons": ["shows the arrest before the story tells it"] if spoils else []}
        return v, GenerationResult(text="{}", model="m/audit", provider="fake")


def _row(db, case, shots, sentences):
    bp = EditorialBlueprint(case_id=case.id, story_version_id=1,
                            blueprint_json=json.dumps(BP), status="valid")
    db.add(bp)
    db.commit()
    plan = {"beats": [{"beat_id": b, "sentences": [{"n": 0, "text": t}]} for b, t in SENT.items()]}
    vp = VisualPlan(case_id=case.id, blueprint_id=bp.id, plan_json=json.dumps(plan),
                    requirements_json=json.dumps({"entities": []}), status="planned")
    db.add(vp)
    db.commit()
    script = {"language": "en", "duration": 30.0, "shots": shots, "overlays": [],
              "candidates": {}, "firewall": {}, "sentence_entities": sentences}
    row = ProductionScript(case_id=case.id, story_version_id=1, language="en",
                           blueprint_id=bp.id, visual_plan_id=vp.id,
                           script_json=json.dumps(script))
    db.add(row)
    db.commit()
    return row


def _db_asset(db, case, code, reveals="[]"):
    from app.db.models import VisualAsset

    a = VisualAsset(case_id=case.id, asset_code=f"{code}_{uuid.uuid4().hex[:4]}",
                    asset_type="photo", provider="source_page", rights_status="public_domain",
                    asset_role="context", verification_status="verified", local_path="x.jpg",
                    sha256=uuid.uuid4().hex, reveals_json=reveals, relevance_tier=3)
    a.title = code
    db.add(a)
    db.commit()
    return a


def test_the_auditor_judges_each_picture_at_its_point_in_the_story(db_session, monkeypatch):
    monkeypatch.setattr(AU, "_picture", lambda a: b"jpg")
    monkeypatch.setattr("app.documentary.visuals.images.data_url", lambda b: "data:image/jpeg;base64,eA==")
    case = _case(db_session)
    arrest = _db_asset(db_session, case, "ARREST")
    tagged = _db_asset(db_session, case, "TAGGED", reveals='["F002"]')
    sentences = [{"beat_id": "B01", "n": 0, "start": 0.0, "end": 9.0},
                 {"beat_id": "B03", "n": 0, "start": 20.0, "end": 29.0}]
    shots = [{"beat_id": "B01", "start": 0.0, "end": 9.0, "kind": "image",
              "command": "NEW_IMAGE", "asset_id": arrest.asset_code, "path": "x.jpg"},
             {"beat_id": "B02", "start": 9.0, "end": 20.0, "kind": "image",
              "command": "NEW_IMAGE", "asset_id": tagged.asset_code, "path": "x.jpg"},
             {"beat_id": "B03", "start": 20.0, "end": 29.0, "kind": "image",
              "command": "NEW_IMAGE", "asset_id": arrest.asset_code, "path": "x.jpg"}]
    row = _row(db_session, case, shots, sentences)
    gen = StoryGen()
    report = asyncio.run(AU.audit_script(db_session, row, AU.VisualAuditor(gen=gen)))
    out = json.loads(row.script_json)["shots"]
    # B01: the arrest picture would give the reveal away -> never shown
    assert all(not (s.get("asset_id") == arrest.asset_code and s["start"] < 20) for s in out)
    assert any("before the story tells it" in w or "later" in w
               for x in report["left_out"] for w in x["why"])
    # B02: a picture whose check found the later-revealed fact is stopped
    # deterministically — the model never even sees it
    assert not any(c[0] == "TAGGED" for c in gen.calls)
    # B03: the same arrest picture is fine once the story has told it
    assert any(s.get("asset_id") == arrest.asset_code and s["start"] >= 20 for s in out)
    assert ("ARREST", (SENT["B03"],), ()) in gen.calls
    # at B01 the auditor was told what is still to come
    assert ("ARREST", (SENT["B01"],), ("Police arrest the neighbour.",)) in gen.calls


def test_audit_verdicts_are_per_point_in_the_story():
    a = _asset("VIS_000044")
    k1 = AU.audit_key(a, ["x"], {"told_later": ["Police arrest the neighbour."]})
    k2 = AU.audit_key(a, ["x"], {"told_later": []})
    assert k1 != k2
    v = {"verdict": "approved", "as": "context", "fits_words": 0.9, "specific_kind_ok": True,
         "tone_ok": True, "person_ok": True, "spoiler_free": False}
    verdict, _as, reasons = AU.decide(v)
    assert verdict == "rejected" and "gives away what the story tells only later" in reasons
