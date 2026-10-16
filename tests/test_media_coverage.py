"""Part 4a: the media coverage metric of a film."""

from app.documentary.visuals import coverage as C


def _shot(start, end, category, beat="B1", kind="image"):
    return {"start": start, "end": end, "kind": kind, "beat_id": beat,
            "usage": {"category": category}}


def test_coverage_splits_case_specific_exact_generic_and_uncovered_time():
    shots = [_shot(0, 20, "evidence"), _shot(20, 30, "context"),
             _shot(30, 40, "generic"), _shot(40, 50, "map")]
    r = C.coverage_report(shots, 60.0)
    assert r["case_specific_share"] == round(40 / 60, 4)   # evidence + context + map
    assert r["exact_share"] == round(30 / 60, 4)           # evidence + map
    assert r["generic_share"] == round(10 / 60, 4)
    assert r["uncovered_share"] == round(10 / 60, 4)       # 50-60 shows no new picture
    assert r["seconds_by_category"]["context"] == 10.0


def test_per_minute_shares_and_gap_list_per_beat():
    shots = [_shot(0, 60, "evidence", "B1"), _shot(60, 120, "generic", "B2"),
             _shot(120, 150, "evidence", "B3"), _shot(150, 180, "generic", "B3")]
    beats = C.beat_windows(shots)
    r = C.coverage_report(shots, 180.0, beats)
    assert [m["case_specific_share"] for m in r["per_minute"]] == [1.0, 0.0, 0.5]
    assert [b["beat_id"] for b in r["per_beat"]] == ["B1", "B2", "B3"]
    assert [g["beat_id"] for g in r["gaps"]] == ["B2"]     # B3 is exactly at the 50% minimum
    assert "0%" in r["gaps"][0]["reason"]


def test_non_picture_shots_and_empty_film_do_not_count():
    r = C.coverage_report([_shot(0, 10, "evidence", kind="title")], 10.0)
    assert r["case_specific_share"] == 0.0
    empty = C.coverage_report([], 0.0)
    assert empty["case_specific_share"] == 0.0 and empty["per_minute"] == []


def test_coverage_endpoint_needs_a_production_script(client, db_session):
    from app.db.models import Case
    from app.utils import slugify

    case = Case(canonical_title="Coverage none", slug=slugify("Coverage none"), language="en")
    db_session.add(case)
    db_session.commit()
    assert client.get(f"/api/cases/{case.id}/documentary/media-coverage/fa").status_code in (404, 409)
