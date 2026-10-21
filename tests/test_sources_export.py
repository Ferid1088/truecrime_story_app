import csv
import io
import json
import uuid

from app.db.models import Case, ProductionScript, VisualAsset
from app.documentary import sources_export as SE


def _case(db):
    case = Case(canonical_title="Sources", slug=uuid.uuid4().hex, language="en")
    db.add(case)
    db.commit()
    return case


def _asset(db, case, code, **kw):
    db.add(VisualAsset(case_id=case.id, asset_code=code, asset_type=kw.pop("asset_type", "photo"),
                       provider=kw.pop("provider", "wikimedia"), **kw))
    db.commit()


def _script(db, case, shots):
    ps = ProductionScript(case_id=case.id, story_version_id=1, language="en", duration_seconds=100.0,
                          script_json=json.dumps({"shots": shots}))
    db.add(ps)
    db.commit()
    return ps


def test_every_picture_and_clip_a_film_uses_is_a_row_with_its_source_and_rights(db_session):
    case = _case(db_session)
    _asset(db_session, case, "P1", title="The house", source_url="https://img.example/h.jpg",
           page_url="https://news.example/story", source_name="news.example", license="CC BY 4.0",
           rights_status="creative_commons", credit="Jane Doe / CC BY 4.0")
    _asset(db_session, case, "V1", asset_type="video", provider="internet_archive", title="Newsreel",
           source_url="https://archive.org/download/x/x.mp4", source_name="Internet Archive",
           rights_status="public_domain", credit="Newsreel / Internet Archive")
    ps = _script(db_session, case, [
        {"asset_id": "P1", "kind": "image", "start": 0, "end": 8},
        {"asset_id": "P1", "kind": "image", "start": 50, "end": 57, "crop_region": 2},
        {"asset_id": "V1", "kind": "video", "start": 8, "end": 20, "clip_start": 31.5, "clip_end": 43.5},
        {"asset_id": None, "kind": "title", "start": 20, "end": 24}])
    rows = SE.rows(db_session, ps)
    assert [r["asset_code"] for r in rows] == ["P1", "V1", "P1"]      # in film order
    p1, v1, p1b = rows
    assert (p1["source_url"], p1["page_url"], p1["source"], p1["rights_status"], p1["credit"]) == (
        "https://img.example/h.jpg", "https://news.example/story", "news.example", "creative_commons",
        "Jane Doe / CC BY 4.0")
    assert (p1["film_start"], p1["film_end"], p1["crop_region"]) == (0, 8, "")
    assert p1b["crop_region"] == 2 and p1b["film_start"] == 50
    assert (v1["clip_start"], v1["clip_end"], v1["kind"]) == (31.5, 43.5, "video")


def test_a_map_is_credited_with_the_map_data_and_a_generated_card_says_so(db_session):
    case = _case(db_session)
    _asset(db_session, case, "M1", asset_type="map", provider="generated", rights_status="owned")
    ps = _script(db_session, case, [
        {"asset_id": "M1", "kind": "map", "start": 0, "end": 10, "attribution": ["© OpenStreetMap contributors"]}])
    [row] = SE.rows(db_session, ps)
    assert row["credit"] == "© OpenStreetMap contributors" and row["provider"] == "generated"


def test_the_csv_has_a_fixed_header_and_one_line_per_row(db_session):
    case = _case(db_session)
    _asset(db_session, case, "P1", title="A, with a comma", source_url="https://x/1")
    ps = _script(db_session, case, [{"asset_id": "P1", "kind": "image", "start": 0, "end": 5}])
    text = SE.csv_text(db_session, ps)
    parsed = list(csv.reader(io.StringIO(text)))
    assert parsed[0] == SE.COLUMNS and len(parsed) == 2
    assert parsed[1][SE.COLUMNS.index("title")] == "A, with a comma"


def test_the_endpoint_serves_the_csv_of_a_film(client, db_session):
    case = _case(db_session)
    _asset(db_session, case, "P1", source_url="https://x/1")
    ps = _script(db_session, case, [{"asset_id": "P1", "kind": "image", "start": 0, "end": 5}])
    r = client.get(f"/api/documentary/production/{ps.id}/sources.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and "P1" in r.text
    assert client.get("/api/documentary/production/999999/sources.csv").status_code == 404


def test_a_clip_from_youtube_exports_its_source_record_and_the_time_range_in_the_source(db_session):
    case = _case(db_session)
    _asset(db_session, case, "SRC", asset_type="video_source", provider="youtube_clip",
           spec_json=json.dumps({"source_window": [60.0, 360.0], "source_record": {
               "url": "https://www.youtube.com/watch?v=abc", "channel": "Moscow Police Department",
               "title": "Press conference", "video_date": "2022-12-30", "source_class": "official_body",
               "class_evidence": "channel kind official", "downloaded_at": "2026-10-21T09:00:00+00:00"}}))
    _asset(db_session, case, "PC1", asset_type="video", provider="youtube_clip", title="Statement",
           source_url="https://www.youtube.com/watch?v=abc", source_name="Moscow Police Department",
           credit="Press conference / Moscow Police Department (YouTube)",
           spec_json=json.dumps({"parent": "SRC", "window": [10.0, 22.0], "source_record": {
               "url": "https://www.youtube.com/watch?v=abc", "channel": "Moscow Police Department",
               "title": "Press conference", "video_date": "2022-12-30", "source_class": "official_body",
               "class_evidence": "channel kind official", "downloaded_at": "2026-10-21T09:00:00+00:00"}}))
    ps = _script(db_session, case, [{"asset_id": "PC1", "kind": "video", "start": 5, "end": 17,
                                     "clip_start": 10.0, "clip_end": 22.0}])
    [row] = SE.rows(db_session, ps)
    assert (row["source_start"], row["source_end"]) == (70.0, 82.0)
    assert (row["source_class"], row["source_date"]) == ("official_body", "2022-12-30")
    assert row["source"] == "Moscow Police Department" and row["credit"].endswith("(YouTube)")
    assert row["downloaded_at"].startswith("2026-10-21")
