"""The planner makes a timeline sound before anything is rendered, and finds what it could not."""

from __future__ import annotations

from app.documentary.production import preflight as PF


def shot(kind, start, end, **extra):
    return {"kind": kind, "start": start, "end": end, "beat_id": "B01", "transition_in": "CROSSFADE", **extra}


def graphic(start, end, fact):
    return shot("graphic", start, end, graphic={"data": {"claims": [{"fact_id": fact}]}}, transition_in="FADE_BLACK")


def test_a_timeline_with_a_hole_a_repeat_and_a_black_fade_is_repaired_and_then_has_no_problems():
    shots = [shot("image", 0, 30), graphic(30, 50, "F1"), shot("detective_board", 50, 74), graphic(80.5, 100.5, "F1"),
             shot("image", 100.5, 120), shot("black", 120, 150), graphic(150, 170, "F2"), shot("image", 170, 200)]
    before = PF.problems(shots, 200)
    assert any("covered by no shot" in p for p in before) and any("second graphic" in p for p in before)
    assert any("fades up from black" in p for p in before)
    report = PF.repair(shots, 200)
    assert report["repeated_graphics"] and report["cut_in_after_gaps"] == 1
    assert PF.problems(shots, 200) == []
    ordered = sorted(shots, key=lambda s: s["start"])
    assert all(abs(b["start"] - a["end"]) < 0.06 for a, b in zip(ordered, ordered[1:], strict=False)), "no hole is left"
    assert ordered[-1]["end"] == 200


def test_the_start_and_the_end_of_the_film_that_no_shot_covers_are_filled():
    shots = [shot("image", 5, 60)]
    assert PF.fill_holes(shots, 70) == [{"fix": "hole_filled", "start": 0.0, "seconds": 5.0},
                                        {"fix": "hole_filled", "start": 60.0, "seconds": 10.0}]
    assert [s["kind"] for s in shots] == ["black", "image", "black"]


def test_a_short_black_that_no_neighbour_can_hold_is_reported_not_called_a_problem():
    clip = shot("video", 0, 30, zoom_video=True)
    gap = shot("black", 30, 34)
    nxt = shot("map", 34, 60, zoom_video=True)
    shots = [clip, gap, nxt]
    PF.repair(shots, 60)
    assert gap.get("left_black") and PF.problems(shots, 60) == []


def test_a_short_black_nobody_looked_at_is_a_problem():
    shots = [shot("image", 0, 30), shot("black", 30, 34), shot("image", 34, 60)]
    assert any("neither covered by its neighbours nor drawn" in p for p in PF.problems(shots, 60))


def test_a_script_of_another_case_that_is_not_a_timeline_is_not_compared_with(db_session):
    import json
    from uuid import uuid4

    from app.db.models import Case, ProductionScript, StoryVersion
    from app.documentary.production.critics import previous_productions

    def script_of(case, shots):
        version = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="x", status="ready", narrative_angle="a")
        db_session.add(version)
        db_session.flush()
        row = ProductionScript(case_id=case.id, story_version_id=version.id, language="en", script_json=json.dumps({"shots": shots}))
        db_session.add(row)
        db_session.commit()
        return row

    cases = [Case(canonical_title=f"C{i}", slug=f"pf-{uuid4().hex[:8]}", language="en") for i in range(3)]
    db_session.add_all(cases)
    db_session.commit()
    good = script_of(cases[0], [{"kind": "image", "start": 0, "end": 5}])
    broken = script_of(cases[1], [{"kind": "image"}])
    mine = script_of(cases[2], [])
    compared = [p["id"] for p in previous_productions(db_session, mine, n=1000)]
    assert good.id in compared and broken.id not in compared
