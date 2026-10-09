"""The visual auditor gate: nothing goes on screen without "approved" for
the words spoken over it; rejected pictures are replaced (a clip first),
the replacement is audited again, and what stays rejected is left out.
Plus the stricter verifier (stand-ins, tone) and video uploads."""
import asyncio
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from PIL import Image

from app.core.ai_config import ai_config
from app.db.models import Case, ProductionScript, VisualAsset, VisualAudit, VisualPlan
from app.documentary.visuals import auditor as AU
from app.documentary.visuals import verification as VER
from app.providers.generation.base import GenerationResult

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")

SENT_DOG = "The police dog picked up a scent at the edge of the forest."
SENT_HOUSE = "The farmhouse stood empty for weeks."


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    return tmp_path


def _case(db) -> Case:
    title = f"Audit {uuid.uuid4().hex[:6]}"
    case = Case(canonical_title=title, slug=title.lower().replace(" ", "-"), language="en")
    db.add(case)
    db.commit()
    return case


def _jpg(path: Path, color=(120, 90, 60)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 200), color).save(path, "JPEG")
    return path


def _asset(db, case, tmp, code, kind="photo", status="verified", role="evidence",
           entities=("police_dog",), clip_len=None) -> VisualAsset:
    if kind == "video":
        clip = tmp / f"{code}.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"testsrc=size=320x240:rate=25:duration={clip_len or 12}",
                        "-pix_fmt", "yuv420p", str(clip)], check=True)
        local = clip
    else:
        local = _jpg(tmp / f"{code}.jpg", (hash(code) % 200, 80, 90))
    a = VisualAsset(case_id=case.id, asset_code=f"{code}_{uuid.uuid4().hex[:4]}",
                    asset_type=kind, provider="source_page", rights_status="public_domain",
                    asset_role=role, verification_status=status, local_path=str(local),
                    thumbnail_path=str(local) if kind == "photo" else None,
                    width=320, height=200, sha256=uuid.uuid4().hex,
                    entities_json=json.dumps(list(entities)), relevance_tier=2,
                    duration_seconds=clip_len, clip_start=0.0 if clip_len else None,
                    clip_end=clip_len)
    db.add(a)
    db.commit()
    return a


def _script(db, case, shots, candidates=None, sentences=None):
    plan = {"beats": [{"beat_id": "B01", "sentences": [
        {"n": 0, "at": 0.0, "text": SENT_HOUSE, "entities": ["the_house"]},
        {"n": 1, "at": 0.5, "text": SENT_DOG, "entities": ["police_dog"]}]}]}
    vp = VisualPlan(case_id=case.id, blueprint_id=-1, plan_json=json.dumps(plan),
                    requirements_json=json.dumps({"entities": []}), status="planned")
    db.add(vp)
    db.commit()
    script = {"language": "en", "duration": 20.0, "shots": shots, "overlays": [],
              "candidates": candidates or {}, "firewall": {},
              "sentence_entities": sentences or [
                  {"beat_id": "B01", "n": 0, "start": 0.0, "end": 10.0, "entities": ["the_house"]},
                  {"beat_id": "B01", "n": 1, "start": 10.0, "end": 20.0,
                   "entities": ["police_dog"]}]}
    row = ProductionScript(case_id=case.id, story_version_id=1, language="en",
                           visual_plan_id=vp.id, script_json=json.dumps(script))
    db.add(row)
    db.commit()
    return row


def _shot(a, start, end, beat="B01"):
    return {"beat_id": beat, "start": start, "end": end, "kind": "image",
            "command": "NEW_IMAGE", "asset_id": a.asset_code, "path": a.local_path,
            "motion": "SLOW_PUSH", "role": a.asset_role}


class Gen:
    """Rejects the codes in `reject` for the dog sentence, approves the rest."""

    def __init__(self, reject=(), symbolic=()):
        self.reject, self.symbolic, self.calls, self.roles = set(reject), set(symbolic), [], []

    async def generate_structured(self, role, system, user, images=None):
        # photos go to the picture auditor, clips (all frames) to the video auditor
        assert role in ("visual_auditor", "video_auditor") and images
        data = json.loads(user)
        claim = (data.get("what_the_picture_is_claimed_to_be")
                 or data.get("what_the_piece_is_claimed_to_be"))
        code = claim["title"]
        self.roles.append((code, role, len(images)))
        self.calls.append((code, tuple(data["narration_while_on_screen"])))
        if code in self.reject and SENT_DOG in data["narration_while_on_screen"]:
            v = {"verdict": "rejected", "as": None, "fits_words": 0.2,
                 "specific_kind_ok": False, "tone_ok": False, "person_ok": True,
                 "reasons": ["two pet dogs on a sofa, not a police dog"]}
        else:
            v = {"verdict": "approved", "as": "symbolic" if code in self.symbolic else "evidence",
                 "fits_words": 0.9, "specific_kind_ok": True, "tone_ok": True,
                 "person_ok": True, "reasons": []}
        return v, GenerationResult(text="{}", model="m/audit", provider="fake")


def _titled(*assets):
    for a in assets:
        a.title = a.asset_code


# ---------------------------------------------------------------------------
# decisions
# ---------------------------------------------------------------------------


def test_auditor_decision_is_strict():
    ok = {"verdict": "approved", "as": "evidence", "fits_words": 0.9,
          "specific_kind_ok": True, "tone_ok": True, "person_ok": True}
    assert AU.decide(ok)[0] == "approved"
    assert AU.decide({**ok, "tone_ok": False})[0] == "rejected"
    assert AU.decide({**ok, "specific_kind_ok": False})[0] == "rejected"
    assert AU.decide({**ok, "fits_words": 0.4})[0] == "rejected"
    assert AU.decide({**ok, "as": "maybe"})[0] == "rejected"
    assert AU.decide({})[0] == "rejected"           # no answer is no approval


def test_verifier_rejects_wrong_tone_and_labels_every_stand_in():
    base = {"matches_claim": "yes", "confidence": 0.9, "period_ok": "yes"}
    assert VER.decide({**base, "tone_ok": False})[0] == "rejected"
    assert VER.decide({**base, "matches_claim": "stand_in", "subject_type": "animal"})[0] == \
        "verified"
    assert "pet or a family dog" in VER.VERIFIER_SYSTEM
    assert "tone_ok" in VER.VERIFIER_SYSTEM


def test_shot_sentences_are_the_english_plan_sentences():
    texts = {("B01", 0): SENT_HOUSE, ("B01", 1): SENT_DOG}
    spans = [{"beat_id": "B01", "n": 0, "start": 0.0, "end": 10.0},
             {"beat_id": "B01", "n": 1, "start": 10.0, "end": 20.0}]
    assert AU.shot_sentences({"start": 11, "end": 18}, spans, texts) == [SENT_DOG]
    assert AU.shot_sentences({"start": 5, "end": 15}, spans, texts) == [SENT_HOUSE, SENT_DOG]


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


@needs_ffmpeg
def test_a_pet_for_the_police_dog_is_replaced_by_an_approved_clip(db_session, env):
    case = _case(db_session)
    house = _asset(db_session, case, env, "HOUSE", entities=("the_house",))
    pets = _asset(db_session, case, env, "PETS")
    photo = _asset(db_session, case, env, "K9PHOTO")
    clip = _asset(db_session, case, env, "K9CLIP", kind="video", clip_len=12)
    _titled(house, pets, photo, clip)
    db_session.commit()
    row = _script(db_session, case, [_shot(house, 0, 10), _shot(pets, 10, 20)],
                  candidates={"B01": [pets.asset_code, photo.asset_code, clip.asset_code]})
    gen = Gen(reject={pets.asset_code})
    report = asyncio.run(AU.audit_script(db_session, row, AU.VisualAuditor(gen=gen)))
    script = json.loads(row.script_json)
    dog = script["shots"][1]
    assert dog["asset_id"] == clip.asset_code and dog["kind"] == "video"   # a clip first
    assert dog["audit"]["verdict"] == "approved"
    assert report["replaced"][0]["from"] == pets.asset_code
    assert "pet dogs" in report["replaced"][0]["why"][0]
    assert report["left_out"] == [] and row.status == "audited"
    # the replacement was audited again before it was accepted — as VIDEO,
    # frame by frame (about one frame per second of the 10 s it plays)
    assert (clip.asset_code, (SENT_DOG,)) in gen.calls
    role = next(r for r in gen.roles if r[0] == clip.asset_code)
    assert role[1] == "video_auditor" and role[2] >= 8
    # verdicts are stored per picture + exact words: another language reuses them
    n = len(gen.calls)
    row2 = _script(db_session, case, [_shot(house, 0, 10), _shot(clip, 10, 20)])
    asyncio.run(AU.audit_script(db_session, row2, AU.VisualAuditor(gen=gen)))
    assert len(gen.calls) == n
    assert db_session.query(VisualAudit).filter(VisualAudit.case_id == case.id).count() >= 3


def test_symbolic_approval_puts_the_label_on_screen(db_session, env):
    case = _case(db_session)
    house = _asset(db_session, case, env, "HOUSE2", entities=("the_house",))
    k9 = _asset(db_session, case, env, "K9GENERIC")
    _titled(house, k9)
    db_session.commit()
    row = _script(db_session, case, [_shot(house, 0, 10), _shot(k9, 10, 20)])
    asyncio.run(AU.audit_script(db_session, row,
                                AU.VisualAuditor(gen=Gen(symbolic={k9.asset_code}))))
    script = json.loads(row.script_json)
    assert script["shots"][1]["role"] == "illustration"
    label = [o for o in script["overlays"] if o["kind"] == "label"]
    assert label and label[0]["start"] == 10 and label[0]["end"] == 20


def test_unapproved_picture_is_never_shown(db_session, env):
    """No replacement exists: the previous picture holds — after being
    audited again for the words it now covers."""
    case = _case(db_session)
    house = _asset(db_session, case, env, "HOUSE3", entities=("the_house",))
    pets = _asset(db_session, case, env, "PETS3")
    _titled(house, pets)
    db_session.commit()
    row = _script(db_session, case, [_shot(house, 0, 8), _shot(pets, 8, 14)],
                  sentences=[{"beat_id": "B01", "n": 0, "start": 0.0, "end": 8.0},
                             {"beat_id": "B01", "n": 1, "start": 8.0, "end": 14.0}])
    gen = Gen(reject={pets.asset_code})
    report = asyncio.run(AU.audit_script(db_session, row, AU.VisualAuditor(gen=gen)))
    shots = json.loads(row.script_json)["shots"]
    assert [s["asset_id"] for s in shots] == [house.asset_code]
    assert shots[0]["end"] == 14
    assert report["left_out"][0]["done"] == "held the previous picture"
    assert (house.asset_code, (SENT_HOUSE, SENT_DOG)) in gen.calls


def test_unverified_or_doubtful_pictures_do_not_pass(db_session, env):
    case = _case(db_session)
    doubtful = _asset(db_session, case, env, "DOUBT", status="needs_review")
    _titled(doubtful)
    db_session.commit()
    row = _script(db_session, case, [_shot(doubtful, 0, 6)],
                  sentences=[{"beat_id": "B01", "n": 1, "start": 0.0, "end": 6.0}])
    gen = Gen()
    report = asyncio.run(AU.audit_script(db_session, row, AU.VisualAuditor(gen=gen)))
    shots = json.loads(row.script_json)["shots"]
    assert shots[0]["kind"] == "black" and "asset_id" not in shots[0]
    assert "not verified" in report["left_out"][0]["why"][0]
    assert gen.calls == []                     # never even shown to the auditor

    # an unverified one is vision-checked first, then audited
    fresh = _asset(db_session, case, env, "FRESH", status="unverified")
    _titled(fresh)
    db_session.commit()

    async def verify(db, case, assets):
        for a in assets:
            a.verification_status = "verified"
        db.commit()

    row = _script(db_session, case, [_shot(fresh, 0, 6)],
                  sentences=[{"beat_id": "B01", "n": 1, "start": 0.0, "end": 6.0}])
    asyncio.run(AU.audit_script(db_session, row, AU.VisualAuditor(gen=gen, verify=verify)))
    assert json.loads(row.script_json)["shots"][0]["audit"]["verdict"] == "approved"


def test_audit_stage_runs_before_render():
    from app.documentary.jobs import plan_stages

    names = [s["name"] for s in plan_stages(["en"])]
    assert names.index("critique:en") < names.index("visual_audit:en") < names.index("render:en")


# ---------------------------------------------------------------------------
# videos: order, fills, uploads
# ---------------------------------------------------------------------------


def test_fallback_prefers_a_clip_of_the_same_tier():
    from app.documentary.visuals.director import _fallback_shot

    photo = VisualAsset(asset_code="P", asset_type="photo", asset_role="context",
                        relevance_tier=2, quality_score=0.9, id=1)
    clip = VisualAsset(asset_code="C", asset_type="video", asset_role="context",
                       relevance_tier=2, quality_score=0.5, id=2, clip_start=0.0, clip_end=10.0,
                       duration_seconds=10.0)
    shot = _fallback_shot({}, False, [photo, clip])
    assert shot["asset_id"] == "C" and shot["command"] == "SHOW_CLIP"


def test_fills_take_a_clip_only_when_it_is_long_enough():
    from app.documentary.production.script import _fits, _video_first

    clip = VisualAsset(asset_code="C", asset_type="video", clip_start=0.0, clip_end=6.0)
    photo = VisualAsset(asset_code="P", asset_type="photo")
    assert _fits(clip, 6.2) and not _fits(clip, 9.0) and _fits(photo, 30)
    pref = _video_first({"C": clip, "P": photo})
    assert pref("C") < pref("P")


@needs_ffmpeg
def test_video_upload_is_stored_muted_and_checked(client, db_session, env, monkeypatch):
    checked = []
    monkeypatch.setattr("app.documentary.api._verify_later",
                        lambda case_id, asset_id: checked.append(asset_id))

    class Segmenter:      # cuts the 5-second upload by meaning: one moment
        async def generate_structured(self, role, system, user, images=None):
            assert role == "video_segmenter" and images
            return ({"pieces": [{"start": 0.0, "end": 5.0, "name": "Officers search a field",
                                 "description": "Moving test pattern."}]},
                    GenerationResult(text="{}", model="m/seg", provider="fake"))

    monkeypatch.setattr("app.documentary.visuals.video_segmenter.get_generation_provider",
                        lambda: Segmenter())
    case = _case(db_session)
    src = env / "mine.mov"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=640x360:rate=30:duration=5", "-f", "lavfi", "-i",
                    "sine=frequency=800:duration=5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    with open(src, "rb") as fh:
        r = client.post(f"/api/cases/{case.id}/visuals/upload",
                        files={"file": ("mine.mov", fh, "video/quicktime")},
                        data={"title": "Police search, our footage", "role": "evidence"})
    assert r.status_code == 200, r.text
    src = db_session.get(VisualAsset, r.json()["id"])
    # kept whole (the source, never on screen) and cut into pieces
    assert src.asset_type == "video_source" and src.provider == "upload"
    assert checked == [src.id]
    from app.documentary import storage
    from app.documentary.visuals.footage import probe_video, sheet_path
    from app.documentary.visuals.pieces import piece_info

    info = probe_video(storage.resolve(src.local_path))
    assert info["has_video"] and not info["has_audio"]      # muted
    pieces = [a for a in db_session.query(VisualAsset).filter_by(case_id=case.id)
              if piece_info(a).get("parent") == src.asset_code]
    assert pieces and all(p.verification_status == "unverified" for p in pieces)
    assert [p.title for p in pieces] == ["Officers search a field"]   # named by meaning
    assert all(sheet_path(case.id, p.asset_code).exists() for p in pieces)
    f = client.get(f"/api/visuals/{pieces[0].id}/file")
    assert f.status_code == 200 and f.headers["content-type"] == "video/mp4"


def test_photo_upload_starts_unverified(client, db_session, env, monkeypatch):
    monkeypatch.setattr("app.documentary.api._verify_later", lambda *a: None)
    case = _case(db_session)
    p = _jpg(env / "up.jpg")
    with open(p, "rb") as fh:
        r = client.post(f"/api/cases/{case.id}/visuals/upload",
                        files={"file": ("up.jpg", fh, "image/jpeg")})
    assert r.status_code == 200, r.text
    assert db_session.get(VisualAsset, r.json()["id"]).verification_status == "unverified"
