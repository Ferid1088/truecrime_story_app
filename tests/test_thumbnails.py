"""Thumbnail stage: host registry, real-asset selection, brief, composer,
critic and approval (synthetic images, no network)."""

import asyncio
import itertools
import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.core.ai_config import ai_config
from app.db.models import (Case, EditorialBlueprint, EpisodeIdentity, StoryVersion, Thumbnail,
                           VisualAsset)
from app.documentary import storage
from app.identity import titles as T
from app.lifecycle.status import set_resolution
from app.thumbnails import brief as B
from app.thumbnails import hosts as H
from app.thumbnails import selection as S
from app.thumbnails import service as SV

_codes = itertools.count(1)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_session):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(ai_config.thumbnail, "storage_dir", str(tmp_path / "thumbs"))
    monkeypatch.setattr(ai_config.thumbnail, "host_manifest", str(tmp_path / "host" / "manifest.json"))
    db_session.query(Thumbnail).delete()
    db_session.query(EpisodeIdentity).delete()
    db_session.commit()
    return tmp_path


def make_hosts(tmp, outfits=("OUTFIT_TC_03", "OUTFIT_TC_01")):
    d = tmp / "host"
    d.mkdir(parents=True, exist_ok=True)
    assets = []
    for o in outfits:
        for facing in ("left", "right"):
            im = Image.new("RGBA", (700, 1300), (0, 0, 0, 0))
            dr = ImageDraw.Draw(im)
            dr.ellipse((250, 40, 450, 260), fill=(205, 170, 140, 255))
            dr.rectangle((150, 260, 550, 1250), fill=(40, 52, 80, 255) if o.endswith("03")
                         else (90, 40, 40, 255))
            fn = f"{o}_{facing}.png"
            im.save(d / fn)
            assets.append({"id": f"HOST_{o[-5:]}_{facing[0]}", "outfit_id": o,
                           "pose": f"calm_{facing}", "facing": facing, "file": fn,
                           "approved": True})
    (d / "manifest.json").write_text(json.dumps({"assets": assets}))
    return assets


def make_case(db, name, status="UNSOLVED", title="Der verschwundene Kreis", lang="de", outfit="OUTFIT_TC_03"):
    case = Case(canonical_title=name, slug=name.lower().replace(" ", "-") + "-th",
                people_json=json.dumps(["Rita Haas"]))
    db.add(case)
    db.flush()
    set_resolution(db, case, status, changed_by="user", reason="t", confidence=0.9, commit=False)
    db.commit()
    ident = T.sync_identity(db, case, lang, title=title)
    ident.host_outfit_id = outfit
    db.commit()
    return case


def make_asset(db, case, *, kind="person", role="victim", rights="public_domain", reveals=(),
               desc="a smiling portrait", verified=0.9, tier=2, case_id=None, ver_extra=None,
               size=(1000, 700), human_override=False, provider="source_page"):
    n = next(_codes)
    rng = np.random.default_rng(n)
    arr = (rng.integers(40, 200, (size[1], size[0], 3))).astype("uint8")
    rel = storage.visuals_dir(case.id, "photo") / f"a{n}.jpg"
    Image.fromarray(arr).save(rel)
    a = VisualAsset(
        case_id=case_id or case.id, asset_code=f"VIS_{n:03d}", asset_type="photo", provider=provider,
        title=f"asset {n}", description=desc, entity_type=kind, subject_type=kind, asset_role=role,
        rights_status=rights, verification_status="verified" if verified else "unverified",
        verification_confidence=verified, relevance_tier=tier, width=size[0], height=size[1],
        local_path=storage.rel(rel), reveals_json=json.dumps(list(reveals)),
        verification_json=json.dumps({"depicts": desc, "subject_type": kind, **(ver_extra or {})}),
        human_override=human_override)
    db.add(a)
    db.commit()
    return a


def run(coro):
    return asyncio.run(coro)


def test_host_is_required(env, db_session):
    case = make_case(db_session, "Thumb no host")
    make_asset(db_session, case)
    with pytest.raises(H.HostMissing):                    # no manifest at all
        B.build_brief(db_session, case, "de")
    make_hosts(env, outfits=("OUTFIT_TC_01",))
    with pytest.raises(H.HostMissing):                    # not the video's outfit
        B.build_brief(db_session, case, "de")
    bad = {"host": {}, "case_visuals": [{"role": "primary"}], "language": "de",
           "resolution_status": "unsolved", "resolution_label": "Ungelöst", "episode_title": "x"}
    assert any("host" in e for e in B.validate_brief(bad))


def test_host_outfit_matches_the_episode_and_faces_the_picture(env, db_session):
    make_hosts(env)
    case = make_case(db_session, "Thumb outfit")
    make_asset(db_session, case)
    brief = B.build_brief(db_session, case, "de", side="left")
    assert brief["host"]["outfit_id"] == "OUTFIT_TC_03" and brief["host"]["person"] == "Fereidoun"
    assert brief["host"]["reference_asset"].endswith("_r")        # left host faces right
    assert B.build_brief(db_session, case, "de", side="right")["host"]["reference_asset"].endswith("_l")
    with pytest.raises(H.HostMissing):
        B.build_brief(db_session, case, "de", pose="calm_nonexistent")


def test_brief_contents_and_variation(env, db_session):
    make_hosts(env)
    case = make_case(db_session, "Thumb brief")
    a = make_asset(db_session, case)
    brief = B.build_brief(db_session, case, "de")
    assert brief["channel"] == "Fallspur" and brief["language"] == "de"
    assert brief["episode_title"] == "Der verschwundene Kreis"
    assert brief["resolution_status"] == "unsolved" and brief["resolution_label"] == "Ungelöst"
    assert brief["case_visuals"] == [{"asset_id": a.id, "asset_code": a.asset_code,
                                      "kind": "victim", "role": "primary"}]
    assert brief["thumbnail_text"] == "" and brief["style"] == "minimal_documentary"
    assert {"red_arrows", "fake_blood", "crowded_collage", "invented_crime_scene",
            "misleading_relationship", "spoiler_asset"} <= set(brief["forbidden"])
    sides = {B.default_side(Case(case_uid=f"CASE_{i:06x}"), "de") for i in range(30)}
    assert sides == {"left", "right"}                               # varies per episode


def test_real_victim_image_beats_place_and_generic(env, db_session):
    make_hosts(env)
    case = make_case(db_session, "Thumb rank")
    make_asset(db_session, case, kind="place", role="illustration", tier=3)
    victim = make_asset(db_session, case, kind="person", role="victim", tier=2)
    make_asset(db_session, case, kind="landscape", role="illustration", tier=5)   # generic
    make_asset(db_session, case, kind="person", role="victim", provider="generated")
    ok, bad = S.rank_assets(db_session, case)
    assert ok[0]["id"] == victim.id
    reasons = " ".join(b["reason"] for b in bad)
    assert "generic" in reasons and "generated" in reasons


def test_wrong_case_and_unverified_visuals_rejected(env, db_session):
    make_hosts(env)
    case = make_case(db_session, "Thumb wrong")
    other = make_case(db_session, "Thumb other", title="Ein anderer Titel")
    foreign = make_asset(db_session, other)
    mismatch = make_asset(db_session, case, ver_extra={"matches_claim": "no"})
    unverified = make_asset(db_session, case, verified=0)
    low = make_asset(db_session, case, size=(200, 150))
    ok, bad = S.rank_assets(db_session, case)
    assert ok == [] and {b["id"] for b in bad} == {mismatch.id, unverified.id, low.id}
    assert S.reject_reason(case, foreign, set(), False) == "belongs to another case"
    with pytest.raises(B.BriefRefused):
        B.build_brief(db_session, case, "de")


def test_spoiler_custody_and_graphic_visuals_rejected(env, db_session):
    make_hosts(env)
    case = make_case(db_session, "Thumb spoiler")
    sv = StoryVersion(case_id=case.id, version=1, narrative_angle="{}", story_text="x",
                      engagement_score=0.5)
    db_session.add(sv)
    db_session.flush()
    bp = {"beats": [{"id": "B1", "reveals": ["F001"]}, {"id": "B2", "reveals": []},
                    {"id": "B3", "reveals": ["F009"]}]}
    db_session.add(EditorialBlueprint(case_id=case.id, story_version_id=sv.id, status="valid",
                                      blueprint_json=json.dumps(bp)))
    db_session.commit()
    late = make_asset(db_session, case, reveals=["F009"])
    opening = make_asset(db_session, case, reveals=["F001"], kind="place", role="illustration")
    custody = make_asset(db_session, case, desc="man in orange inmate uniform in courtroom")
    graphic = make_asset(db_session, case, desc="a body bag and blood on the floor")
    ok, bad = S.rank_assets(db_session, case)
    assert [o["id"] for o in ok] == [opening.id]
    why = {b["id"]: b["reason"] for b in bad}
    assert "reveals later" in why[late.id] and "custody" in why[custody.id]
    assert why[graphic.id] == "graphic content"


def test_suspect_and_misleading_pairs_refused(env, db_session):
    make_hosts(env)
    case = make_case(db_session, "Thumb suspect")
    suspect = make_asset(db_session, case, role="suspect")
    victim = make_asset(db_session, case, role="victim")
    ok, bad = S.rank_assets(db_session, case)
    assert suspect.id not in {o["id"] for o in ok}
    assert "responsibility is not established" in next(b["reason"] for b in bad if b["id"] == suspect.id)
    other = make_asset(db_session, case, role="illustration", kind="person")
    with pytest.raises(B.BriefRefused, match="relationship"):
        B.build_brief(db_session, case, "de", primary_asset_id=victim.id, secondary_asset_id=other.id)
    # solved: a suspect may appear, but never framed against the victim without conviction rules
    set_resolution(db_session, case, "SOLVED", changed_by="user", reason="verdict")
    ok2, _ = S.rank_assets(db_session, case)
    assert suspect.id in {o["id"] for o in ok2}


def test_rights_gates(env, db_session):
    case = make_case(db_session, "Thumb rights")
    no = make_asset(db_session, case, rights="do_not_use", human_override=True)
    unknown = make_asset(db_session, case, rights="unknown")
    perm = make_asset(db_session, case, rights="permission_required")
    approved = make_asset(db_session, case, rights="editorial_review_required", human_override=True)
    ok, bad = S.rank_assets(db_session, case)
    assert [o["id"] for o in ok] == [approved.id]               # editorial approval is explicit
    why = {b["id"]: b["reason"] for b in bad}
    assert why[no.id] == "rights: do_not_use"
    assert "needs editorial approval" in why[unknown.id] and "needs editorial approval" in why[perm.id]


def test_more_than_configured_images_rejected(env, db_session):
    make_hosts(env)
    case = make_case(db_session, "Thumb count")
    make_asset(db_session, case)
    brief = B.build_brief(db_session, case, "de")
    brief["case_visuals"] += [{"asset_id": 1, "role": "secondary"}] * 2
    assert any("too many case pictures" in e for e in B.validate_brief(brief))
    brief["thumbnail_text"] = "one two three four five"
    assert any("words" in e for e in B.validate_brief(brief))


def test_status_requires_public_status_and_label(env, db_session):
    make_hosts(env)
    case = make_case(db_session, "Thumb unknown", status="STATUS_UNDER_REVIEW")
    make_asset(db_session, case)
    with pytest.raises(B.BriefRefused, match="neither solved nor unsolved"):
        B.build_brief(db_session, case, "de")
    bare = Case(canonical_title="Thumb no title", slug="thumb-no-title-th")
    db_session.add(bare)
    db_session.commit()
    with pytest.raises(B.BriefRefused, match="approved episode title"):
        B.build_brief(db_session, bare, "de")


LANGS = [("en", "The Vanishing Circle", "Unsolved", "ClueVera"),
         ("de", "Der verschwundene Kreis", "Ungelöst", "Fallspur"),
         ("fa", "دایره‌ی ناپدید", "حل‌نشده", "رد خاموش"),
         ("ar", "الدائرة المختفية", "غير محلولة", "أثر خفي")]


@pytest.mark.parametrize("lang,title,label,channel", LANGS)
def test_composed_thumbnail_per_channel(env, db_session, lang, title, label, channel):
    make_hosts(env)
    case = make_case(db_session, f"Thumb compose {lang}", title=title, lang=lang)
    make_asset(db_session, case)
    t = run(SV.create_thumbnail(db_session, case, lang, None))
    brief = json.loads(t.brief_json)
    assert brief["channel"] == channel and brief["resolution_label"] == label == t.status_label
    img = Image.open(SV.file_of(t))
    assert img.size == (1280, 720) and SV.file_of(t).stat().st_size <= 2_000_000
    crit = json.loads(t.critic_json)
    assert crit["verdict"] in ("needs_review", "pass") and not crit["failures"], crit["failures"]
    layout = json.loads(t.layout_json)
    assert len(layout["elements"]) <= ai_config.thumbnail.max_elements          # minimal design
    assert len(set(layout["fonts"])) == 1                                       # one font family
    assert layout["host"][3] / 720 >= 0.8 and layout["host"][2] / 1280 >= 0.15  # host prominent
    assert layout["badge"][2] * layout["badge"][3] / (1280 * 720) <= 0.06       # badge small
    ident = T.get_identity(db_session, case.id, lang)
    assert ident.host_outfit_id == "OUTFIT_TC_03" and ident.thumbnail_status_label == label
    assert "273" not in brief["episode_title"]


def test_host_side_and_badge_placement(env, db_session):
    make_hosts(env)
    case = make_case(db_session, "Thumb side", title="Die Laterne von Celle")
    make_asset(db_session, case)
    left = json.loads(run(SV.create_thumbnail(db_session, case, "de", None, side="left")).layout_json)
    right = json.loads(run(SV.create_thumbnail(db_session, case, "de", None, side="right")).layout_json)
    assert left["host"][0] < 640 < right["host"][0] + right["host"][2] and left["host"][0] < right["host"][0]
    assert left["panel"][0] > right["panel"][0]
    assert db_session.query(Thumbnail).filter_by(case_id=case.id).count() == 2


class VisionGen:
    def __init__(self, **scores):
        self.scores, self.images = scores, []

    async def generate_structured(self, role, system, user, images=None):
        assert role == "thumbnail_critic"
        self.images = images
        return {"curiosity": 0.7, "authenticity": 0.8, "brand_consistency": 0.9, "cleanliness": 0.9,
                "automation_feel": 0.1, "misleading_risk": 0.0, "spoiler_risk": 0.0,
                "looks_professional": True, "problems": [], "reason": "clean", **self.scores}, None


def test_vision_critic_flags_automation_feel_and_blocks_approval(env, db_session):
    make_hosts(env)
    case = make_case(db_session, "Thumb critic")
    make_asset(db_session, case)
    good = run(SV.create_thumbnail(db_session, case, "de", VisionGen()))
    rep = json.loads(good.critic_json)
    assert rep["verdict"] == "pass" and rep["vision_checked"] and rep["scorecard"]["curiosity"] == 0.7
    assert set(rep["scorecard"]) >= {"host_visibility", "case_visual_clarity", "brand_consistency",
                                     "cleanliness", "curiosity", "authenticity", "readability",
                                     "crowding", "spoiler_risk", "misleading_risk",
                                     "automation_feel"}
    bad = run(SV.create_thumbnail(db_session, case, "de",
                                  VisionGen(automation_feel=0.9, looks_professional=False,
                                            problems=["collage look"])))
    assert json.loads(bad.critic_json)["verdict"] == "fail"
    with pytest.raises(SV.ThumbnailRefused):
        SV.decide(db_session, bad, True)
    SV.decide(db_session, good, True)
    assert good.status == "approved"
    SV.decide(db_session, bad, True, override=True)               # explicit human override
    db_session.refresh(good)
    assert good.status == "superseded" and bad.status == "approved"


def test_api_flow(env, client, db_session, monkeypatch):
    make_hosts(env)
    case = make_case(db_session, "Thumb api", title="Der verschwundene Kreis")
    asset = make_asset(db_session, case)
    make_asset(db_session, case, rights="do_not_use")
    opts = client.get(f"/api/cases/{case.id}/thumbnails/options", params={"language": "de"}).json()
    assert opts["outfit_id"] == "OUTFIT_TC_03" and len(opts["host_poses"]) == 2
    assert [u["asset_id"] for u in opts["usable"]] == [asset.id] and len(opts["rejected"]) == 1
    r = client.post(f"/api/cases/{case.id}/thumbnails", json={"language": "de", "side": "right"})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["brief"]["host"]["side"] == "right" and t["status"] == "draft"
    assert client.get(t["image_url"]).headers["content-type"] == "image/jpeg"
    d = client.post(f"/api/thumbnails/{t['id']}/decision", json={"approve": True}).json()
    assert d["status"] == "approved"
    assert len(client.get(f"/api/cases/{case.id}/thumbnails").json()) == 1
    bad = client.post(f"/api/cases/{case.id}/thumbnails", json={"language": "fa"})
    assert bad.status_code == 422
