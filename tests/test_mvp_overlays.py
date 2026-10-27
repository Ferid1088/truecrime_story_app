"""Other channels' logos, watermarks and caption strips: found by a vision scan, cut away by the film's frame, and a
picture or clip that cannot be cut free of them is not used."""
import asyncio
import io
import json
from uuid import uuid4

import numpy as np
from PIL import Image

from app.db.models import (
    Case,
    EditorialBlueprint,
    MvpCandidate,
    MvpRequirement,
    StoryVersion,
    VisualAsset,
    VisualPlan,
)
from app.documentary.mvp import overlays as OV
from app.documentary.render import engine as RE

# the owner's screenshot (2000 x 1266): a bar with a channel logo on the right, a second channel's emblem over the
# picture at its lower right, and a credit line under it
SCREENSHOT = [OV.Mark("bar", "bar with a logo", (0.9075, 0.0, 1.0, 1.0)),
              OV.Mark("logo", "ClueVera", (0.91, 0.035, 0.99, 0.15)),
              OV.Mark("logo", "The Courtroom - True Crimes Told", (0.865, 0.758, 0.965, 0.916)),
              OV.Mark("caption", "Footage released ... (YouTube)", (0.495, 0.965, 0.99, 0.99))]


def overlaps(crop, mark, width, height, margin=0.0):
    x, y, w, h = crop
    return not (x + w <= mark.box[0] - margin or x >= mark.box[2] + margin
                or y + h <= mark.box[1] - margin or y >= mark.box[3] + margin)


def test_the_cut_of_the_owners_screenshot_keeps_no_logo_bar_or_caption_and_most_of_the_picture():
    scan = OV.judge(SCREENSHOT, 2000, 1266)
    assert scan.status == OV.CROPPED
    x, y, w, h = scan.crop
    assert not any(overlaps(scan.crop, m, 2000, 1266) for m in SCREENSHOT)
    assert abs((w * 2000) / (h * 1266) - 16 / 9) < 0.01 and w * h >= 0.5


def test_a_picture_with_no_mark_is_left_as_it_is():
    scan = OV.judge([], 1920, 1080)
    assert scan.status == OV.CLEAN and scan.crop is None


def test_a_mark_across_the_middle_cannot_be_cut_away_so_the_picture_is_unusable():
    scan = OV.judge([OV.Mark("watermark", "agency", (0.05, 0.4, 0.95, 0.6))], 1920, 1080)
    assert scan.status == OV.UNUSABLE and scan.crop is None


def test_marks_outside_the_picture_or_without_a_box_are_ignored():
    marks = OV.parse_marks({"marks": [{"kind": "logo", "what": "a", "box": [0.9, 0.0, 1.4, 0.1]},
                                      {"kind": "logo", "what": "b"}, {"kind": "weird", "box": [0.1, 0.1, 0.2, 0.2]},
                                      {"kind": "logo", "box": [0.5, 0.5, 0.5, 0.5]}]})
    assert [(m.kind, m.box) for m in marks] == [("logo", (0.9, 0.0, 1.0, 0.1)), ("other", (0.1, 0.1, 0.2, 0.2))]


def _asset(db, case, code="A1", kind="photo", sha="s1"):
    asset = VisualAsset(case_id=case.id, asset_code=code, asset_type=kind, title=code, sha256=sha,
                        local_path=f"{code}.jpg", relevance_tier=2, asset_role="evidence")
    db.add(asset)
    db.commit()
    return asset


def _case(db):
    case = Case(canonical_title="Overlay proof", slug=f"ov-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    return case


def jpeg(width=640, height=360):
    out = io.BytesIO()
    Image.new("RGB", (width, height), (40, 40, 40)).save(out, "JPEG")
    return out.getvalue()


def scanner(db, case, answer):
    async def asker(payload, images):
        return answer
    return OV.OverlayScanner(db, case, asker=asker, frames=lambda asset: [jpeg()])


def test_a_scan_is_stored_on_the_asset_and_not_asked_again(db_session):
    case = _case(db_session)
    asset = _asset(db_session, case)
    answer = {"marks": [{"kind": "logo", "what": "channel", "box": [0.85, 0.02, 0.98, 0.15]}]}
    first = scanner(db_session, case, answer)
    out = asyncio.run(first.run([asset]))
    assert out["cropped"] == 1 and OV.crop_of(asset)["w"] > 0.5
    stored = json.loads(asset.spec_json)["overlays"]
    assert stored["status"] == "cropped" and stored["marks"][0]["kind"] == "logo"
    again = scanner(db_session, case, {"marks": []})
    assert asyncio.run(again.run([asset]))["cropped"] == 1 and OV.crop_of(asset)["w"] > 0.5


def test_an_asset_changed_since_its_scan_is_scanned_again(db_session):
    case = _case(db_session)
    asset = _asset(db_session, case)
    asyncio.run(scanner(db_session, case, {"marks": [{"kind": "logo", "box": [0.85, 0.02, 0.98, 0.15]}]}).run([asset]))
    asset.sha256 = "changed"
    assert OV.current(asset) is None


def test_a_failed_scan_is_never_taken_as_clean(db_session):
    case = _case(db_session)
    asset = _asset(db_session, case)
    out = asyncio.run(scanner(db_session, case, {"unexpected": True}).run([asset]))
    assert out["failed"] == 1 and out["clean"] == 0 and OV.current(asset) is None


def test_an_asset_that_cannot_be_cut_free_loses_its_approved_candidates(db_session):
    case = _case(db_session)
    asset = _asset(db_session, case, "A9")
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t", narrative_angle="a")
    db_session.add(sv)
    db_session.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db_session.add(bp)
    db_session.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1, validation_json="{}")
    db_session.add(plan)
    db_session.commit()
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.1", beat_id="B01",
                         function="evidence", role_needed="evidence", priority="high", status="open")
    db_session.add(req)
    db_session.commit()
    cand = MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id, role="evidence", final_status="approved",
                        provenance_status="approved", identity_status="approved", ethics_status="approved",
                        scene_status="approved", rights_status="approved")
    db_session.add(cand)
    db_session.commit()
    answer = {"marks": [{"kind": "watermark", "what": "agency", "box": [0.02, 0.4, 0.98, 0.6]}]}
    out = asyncio.run(scanner(db_session, case, answer).run([asset]))
    db_session.refresh(cand)
    assert out["rejected_codes"] == ["A9"] and cand.final_status == "rejected"
    assert "cannot be cut away" in cand.reasons_json


def test_the_renderer_shows_only_the_cut_of_a_picture_and_of_a_clip():
    frame = np.zeros((100, 200, 3), np.uint8)
    frame[:, 150:] = 255                                           # the right quarter (a bar with a logo)
    cut = {"x": 0.0, "y": 0.0, "w": 0.7, "h": 0.7875}
    reader = RE.ClipReader("missing.mp4", 320, 180, 10.0, crop=cut)
    shown = reader._fill(frame)
    assert shown.shape == (180, 320, 3) and int(shown.max()) == 0
    assert RE.crop_box(cut, 200, 100) == (0, 0, 140, 79) and RE.crop_box(None, 200, 100) is None


def test_the_pictures_own_side_bars_do_not_make_a_tall_video_unusable():
    tall_in_wide = [OV.Mark("bar", "side", (0.0, 0.0, 0.3, 1.0)), OV.Mark("bar", "side", (0.7, 0.0, 1.0, 1.0))]
    scan = OV.judge(tall_in_wide, 1280, 720)
    assert scan.status == OV.CROPPED
    with_logo = tall_in_wide + [OV.Mark("logo", "channel", (0.75, 0.8, 0.95, 0.95))]
    assert OV.judge(with_logo, 1280, 720).status == OV.CROPPED


def test_only_approved_candidates_are_rejected_for_a_mark_that_cannot_be_cut_away(db_session):
    case = _case(db_session)
    asset = _asset(db_session, case, "A8")
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t", narrative_angle="a")
    db_session.add(sv)
    db_session.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db_session.add(bp)
    db_session.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1, validation_json="{}")
    db_session.add(plan)
    db_session.commit()
    cands = []
    for n, status in enumerate(("approved", "needs_review")):
        req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key=f"B0{n}.1", beat_id=f"B0{n}",
                             function="evidence", role_needed="evidence", priority="high", status="open")
        db_session.add(req)
        db_session.commit()
        cand = MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id, role="evidence", final_status=status,
                            provenance_status="approved", identity_status="approved" if status == "approved"
                            else "needs_review", ethics_status="approved", scene_status="approved",
                            rights_status="approved")
        db_session.add(cand)
        db_session.commit()
        cands.append(cand)
    OV.reject(db_session, asset, OV.Scan(OV.UNUSABLE, [OV.Mark("watermark", "agency", (0.0, 0.4, 1.0, 0.6))]))
    for cand in cands:
        db_session.refresh(cand)
    assert [c.final_status for c in cands] == ["rejected", "needs_review"]


# -- the cut ---------------------------------------------------------------------------------------------------------


def clean_of(scan, marks, width, height):
    assert scan.status == OV.CROPPED
    assert not any(overlaps(scan.crop, m, width, height) for m in marks)
    return scan.crop


def test_a_corner_logo_in_a_wide_video_is_cut_to_a_full_screen_window():
    logo = [OV.Mark("logo", "channel", (0.88, 0.80, 0.99, 0.97))]
    x, y, w, h = clean_of(OV.judge(logo, 1920, 1080), logo, 1920, 1080)
    assert abs((w * 1920) / (h * 1080) - 16 / 9) < 0.01 and w * h > 0.7


def test_a_split_picture_is_unusable_because_either_half_could_be_the_wrong_person():
    divider = [OV.Mark("other", "divider", (0.498, 0.0, 0.502, 1.0))]
    assert OV.judge(divider, 1500, 1000).status == OV.UNUSABLE


def test_a_mark_off_the_centre_is_cut_away_and_the_centre_is_kept():
    side = [OV.Mark("caption", "sidebar", (0.0, 0.0, 0.2, 1.0))]
    x, y, w, h = clean_of(OV.judge(side, 1500, 1000), side, 1500, 1000)
    assert x <= 0.5 <= x + w and w * h > 0.7


def test_a_portrait_with_a_caption_strip_loses_only_the_strip():
    strip = [OV.Mark("caption", "credit", (0.0, 0.93, 1.0, 1.0))]
    x, y, w, h = clean_of(OV.judge(strip, 1600, 2000), strip, 1600, 2000)
    assert w == 1.0 and h > 0.9


def test_the_cut_of_another_shape_is_taken_when_a_full_screen_one_would_lose_much_more():
    left_column = [OV.Mark("caption", "sidebar", (0.0, 0.0, 0.4, 1.0))]
    x, y, w, h = clean_of(OV.judge(left_column, 1920, 1080), left_column, 1920, 1080)
    assert abs((w * 1920) / (h * 1080) - 16 / 9) > 0.1 and w * h > 0.45


def test_a_mark_through_the_middle_or_one_that_leaves_too_little_makes_the_picture_unusable():
    assert OV.judge([OV.Mark("logo", "big", (0.2, 0.2, 0.8, 0.8))], 1920, 1080).status == OV.UNUSABLE
    across = [OV.Mark("watermark", "x", (0.0, 0.3, 1.0, 0.7))]
    assert OV.judge(across, 1920, 1080).status == OV.UNUSABLE            # both remaining strips are under 40 percent


def test_the_bars_of_a_pillarboxed_clip_are_cut_away_and_do_not_count_as_lost_picture():
    bars = [OV.Mark("bar", "left", (0.0, 0.0, 0.2, 1.0)), OV.Mark("bar", "right", (0.8, 0.0, 1.0, 1.0))]
    x, y, w, h = clean_of(OV.judge(bars, 1920, 1080), bars, 1920, 1080)
    assert w * h >= 0.57 and x >= 0.2 and x + w <= 0.8
    with_logo = bars + [OV.Mark("logo", "channel", (0.62, 0.8, 0.78, 0.97))]
    clean_of(OV.judge(with_logo, 1920, 1080), with_logo, 1920, 1080)


def test_twelve_marks_are_judged_quickly():
    import time

    marks = [OV.Mark("logo", str(n), (0.07 * n, 0.02 + 0.07 * (n % 5), 0.07 * n + 0.04, 0.06 + 0.07 * (n % 5)))
             for n in range(12)]
    start = time.perf_counter()
    OV.judge(marks, 1920, 1080)
    assert time.perf_counter() - start < 3.0


def test_a_stored_scan_is_judged_again_by_todays_rules_without_asking_the_model(db_session):
    case = _case(db_session)
    asset = _asset(db_session, case)
    asset.width, asset.height = 1500, 1000
    asset.spec_json = json.dumps({"overlays": {
        "version": 2, "status": "unusable", "crop": None, "sha256": asset.sha256,
        "marks": [{"kind": "logo", "what": "channel", "box": [0.8, 0.0, 1.0, 0.25]}]}})
    db_session.commit()
    out = asyncio.run(scanner(db_session, case, {"unexpected": True}).run([asset]))
    assert out["cropped"] == 1 and out["failed"] == 0 and OV.crop_of(asset)["w"] > 0.5
    assert json.loads(asset.spec_json)["overlays"]["status"] == "cropped"
