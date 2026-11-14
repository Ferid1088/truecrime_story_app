"""The YouTube chapters: the pure resolver (YouTube's rules, durations, spoiler words, neutral titles, Unicode), the chapter writer picking them inside its existing calls (no extra
request, the extra tokens estimated), the list stored per case and language and moved by a new render without a model call, its place in the description, and the routes."""

import asyncio
import json

import pytest
from test_chapters import _setup  # noqa: F401  (the case fixture helpers)
from test_chapters import BP

from app.core.ai_config import ai_config
from app.db.models import Case, ChapterPlan, EditorialBlueprint, Fact, ProductionScript, StoryVersion, Video, VideoChapters
from app.documentary import chapters as CH
from app.documentary import youtube_chapters as YT
from app.lifecycle import videos as VID
from app.providers.generation.base import GenerationResult

CFG = ai_config.chapters.youtube


def beats(*starts):
    return [{"beat_id": f"B{i + 1:02d}", "start": s, "end": s + 5} for i, s in enumerate(starts)]


def titles(result):
    return [(c["time"], c["title"]) for c in result["chapters"]]


def test_a_valid_list_starts_at_zero_and_keeps_the_order_of_the_film():
    timeline = beats(52.7, 400, 900, 1700, 2500, 3300)
    out = YT.build([["B03", "The call"], ["B01", "Opening scene"], ["B05", "The search"], ["B06", "Last stretch"]], timeline, 4000, "en")
    assert out["status"] == "ok" and titles(out) == [("00:00", "Opening scene"), ("15:00", "The call"), ("41:40", "The search"), ("55:00", "Last stretch")]
    assert [c["source"] for c in out["chapters"]] == ["writer"] * 4


def test_a_film_over_an_hour_uses_hours_and_a_missing_first_anchor_gets_a_neutral_opening():
    out = YT.build([["B02", "The scene"], ["B03", "The house"], ["B04", "The trial"]], beats(50, 700, 3700, 3900), 4200, "en")
    assert out["chapters"][0] == {"start": 0, "time": "00:00", "title": "The Opening", "beat": "B01", "source": "fallback"}
    assert [c["time"] for c in out["chapters"]] == ["00:00", "11:40", "1:01:40", "1:05:00"]


@pytest.mark.parametrize("anchors", [[["B01", "One"], ["B02", "Two"]], [["B01", "One"]], []])
def test_fewer_than_three_chapters_are_omitted_not_invented(anchors):
    out = YT.build(anchors, beats(0, 300, 600, 900), 1200, "en")
    assert out["status"] == "omitted" and out["chapters"] == [] and "fewer than 3" in out["reason"]


def test_a_chapter_shorter_than_ten_seconds_is_merged_and_the_first_one_always_stays():
    out = YT.build([["B01", "One"], ["B02", "Two"], ["B03", "Three"], ["B04", "Four"], ["B05", "Five"]], beats(5, 8, 400, 405, 900), 1300, "en")
    assert [c["beat"] for c in out["chapters"]] == ["B01", "B04", "B05"] and out["chapters"][0]["start"] == 0
    assert any("shorter than 10 s" in d for d in out["dropped"])
    tail = YT.build([["B01", "One"], ["B02", "Two"], ["B03", "Three"], ["B04", "Four"]], beats(0, 300, 600, 1195), 1200, "en")
    assert [c["beat"] for c in tail["chapters"]] == ["B01", "B02", "B03"]


def test_an_anchor_that_is_not_on_the_films_timeline_is_dropped_and_two_in_one_second_are_one():
    out = YT.build([["B01", "One"], ["B09", "Nowhere"], ["B02", "Two"], ["B03", "Three"], ["B04", "Four"]], beats(0, 300, 300.4, 700), 1000, "en")
    assert [c["beat"] for c in out["chapters"]] == ["B01", "B02", "B04"] and len(out["dropped"]) == 2


def test_a_title_that_names_an_outcome_is_replaced_by_the_languages_neutral_one_not_by_a_new_call():
    anchors = [["B01", "A woman is dead"], ["B02", "The killer is her husband"], ["B03", "The confession"], ["B04", "A new suspect"], ["B05", "What the evidence says"]]
    out = YT.build(anchors, beats(0, 300, 600, 900, 1200), 1500, "en")
    assert [c["title"] for c in out["chapters"]] == ["A woman is dead", "Part 2", "Part 3", "A new suspect", "What the evidence says"]
    assert [c["source"] for c in out["chapters"]] == ["writer", "fallback", "fallback", "writer", "writer"]
    last = YT.build([["B01", "Start"], ["B02", "Middle"], ["B03", "The verdict explained"]], beats(0, 300, 600), 900, "en")
    assert last["chapters"][-1]["title"] == "The Final Questions"


def test_empty_overlong_and_repeated_titles_get_neutral_ones():
    long = "x" * (ai_config.chapters.max_title_chars + 1)
    out = YT.build([["B01", "Same"], ["B02", ""], ["B03", long], ["B04", "same"], ["B05", "Fine"]], beats(0, 300, 600, 900, 1200), 1500, "en")
    assert [c["title"] for c in out["chapters"]] == ["Same", "Part 2", "Part 3", "Part 4", "Fine"]


@pytest.mark.parametrize("language,spoiler", [("de", "Der Mörder ist entlarvt"), ("fa", "قاتل پیدا شد"), ("ar", "الحقيقة كاملة"), ("es", "El asesino confiesa"), ("fr", "Le coupable avoue"),
                                              ("it", "Il colpevole confessa"), ("pt", "O assassino é revelado"), ("tr", "Katil ortaya çıkıyor")])
def test_every_language_has_spoiler_words_and_neutral_titles(language, spoiler):
    out = YT.build([["B01", "Start"], ["B02", spoiler], ["B03", "Ende"]], beats(0, 300, 600), 900, language)
    assert out["chapters"][1]["source"] == "fallback" and out["chapters"][1]["title"] != spoiler


def test_persian_and_arabic_titles_keep_their_text_and_the_persian_fallback_uses_persian_digits():
    out = YT.build([["B01", "شب کانینگهام کورت"], ["B02", "قاتل"], ["B03", "مسیر تحقیق"], ["B04", "پرسش‌های باز"]], beats(0, 300, 600, 900), 1200, "fa")
    assert titles(out)[0] == ("00:00", "شب کانینگهام کورت") and out["chapters"][1]["title"] == "بخش ۲"
    ar = YT.build([["B01", "ليلة في تيب سيتي"], ["B02", "التحقيق"], ["B03", "الأدلة"]], beats(0, 300, 600), 900, "ar")
    assert ar["chapters"][1]["title"] == "التحقيق" and YT.block(ar["chapters"]).splitlines()[0] == "00:00 ليلة في تيب سيتي"


def test_more_chapters_than_the_maximum_lose_the_shortest():
    anchors = [[f"B{i:02d}", f"T{i}"] for i in range(1, 15)]
    out = YT.build(anchors, beats(*[i * 300 for i in range(14)]), 5000, "en")
    assert len(out["chapters"]) == CFG.max_chapters and out["chapters"][0]["beat"] == "B01"


def test_the_description_block_is_one_line_per_chapter():
    assert YT.block([{"time": "00:00", "title": "A"}, {"time": "03:15", "title": "B"}]) == "00:00 A\n03:15 B"
    assert YT.format_time(0) == "00:00" and YT.format_time(59.9) == "00:59" and YT.format_time(3600) == "1:00:00"


# ---------------------------------------------------------------- the chapter writer picks the chapters in its own calls


def film(db, n=24):
    case, master, bp = _setup(db)
    blueprint = {"beats": [{"id": f"B{i + 1:02d}", "act_id": "full", "purpose": "hook" if i == 0 else "investigation", "summary": f"Beat {i + 1} " + "x" * 200, "words": 100, "reveals": []}
                           for i in range(n)]}
    bp.blueprint_json = json.dumps(blueprint)
    db.commit()
    return case, master, bp


DESCRIPTION = ("A call at 2:31 in the morning reports an intruder. " + "A family home becomes the centre of an investigation. " * 8 + "Join us to follow what is known.")


class Gen:
    """The chapter writer and auditor: the writer answers the cards and, when asked, the chapters; `bad` titles are refused by the auditor once."""

    def __init__(self, bad=None, youtube=True):
        self.calls, self.payloads, self.bad, self.youtube = [], [], bad, youtube

    async def generate_structured(self, role, system, user, images=None):
        data = json.loads(user)
        self.calls.append(role)
        self.payloads.append((role, data))
        res = GenerationResult(text="{}", model="gemini-3.8-flash", provider="fake")
        language = data["language"]
        if role == "chapter_writer":
            out = {"film_title": {language: "The film"}, "chapters": [{"act_id": c["act_id"], "title": {language: "Part"}} for c in data.get("chapters", [])], "events": []}
            story = data.get("story_chapters")
            if data.get("description") and not any(i["key"] != "description" for i in data.get("fix") or []):
                out["description"] = DESCRIPTION
            if story:
                ids = [b[0] for b in data["story_beats"]]
                picks = [ids[0], ids[6], ids[12], "B99", ids[18]]
                out["story_chapters"] = [[b, f"Chapter {b}"] for b in picks]
                if self.bad and not data.get("fix"):
                    out["story_chapters"][1] = [ids[6], self.bad]
                for item in data.get("fix") or []:
                    if item["key"].startswith("story_chapter:"):
                        out["story_chapters"] = [[item["key"].split(":")[1], "A new line of inquiry"]]
            return out, res
        return {"verdicts": [{"key": k, "ok": t != self.bad, "reason": "gives away the culprit"} for k, t in data["texts"].items()]}, res


def make(db, gen, n=24):
    from app.agents.chapters import ChapterWriter

    case, master, bp = film(db, n)
    row = asyncio.run(ChapterWriter(gen=gen).create(db, case, bp, master, ["en"]))
    return case, master, bp, row


def test_the_writer_is_asked_for_the_chapters_in_the_same_call_with_the_compact_beats(db_session):
    gen = Gen()
    _, _, _, row = make(db_session, gen)
    role, payload = gen.payloads[0]
    story, beats = payload["story_chapters"], payload["story_beats"]
    assert role == "chapter_writer" and len(beats) == 24 and beats[0][:2] == ["B01", "hook"] and story["count"] == CH.youtube_count(24)
    assert all(len(b[2]) <= CFG.summary_chars for b in beats) and "chapters" in payload and "events" in payload
    assert json.loads(row.plan_json)["story_chapters"]["en"][0] == ["B01", "Chapter B01"]


def test_an_invented_beat_id_is_ignored_and_the_chapters_come_back_in_film_order(db_session):
    _, _, _, row = make(db_session, Gen())
    assert [b for b, _ in json.loads(row.plan_json)["story_chapters"]["en"]] == ["B01", "B07", "B13", "B19"]


def test_a_title_the_auditor_refuses_is_rewritten_once_in_the_same_loop_and_no_new_stage_is_needed(db_session):
    gen = Gen(bad="The husband did it")
    _, _, _, row = make(db_session, gen)
    story = dict(json.loads(row.plan_json)["story_chapters"]["en"])
    assert story["B07"] == "A new line of inquiry" and gen.calls.count("chapter_writer") == 2 and gen.calls.count("chapter_auditor") == 2


def test_chapters_add_no_request_the_calls_are_the_same_with_and_without_them(db_session, monkeypatch):
    with_chapters, without = Gen(), Gen()
    make(db_session, with_chapters)
    monkeypatch.setattr(ai_config.chapters.youtube, "enabled", False)
    make(db_session, without)
    assert with_chapters.calls == without.calls == ["chapter_writer", "chapter_auditor"]
    assert "story_chapters" not in without.payloads[0][1]


def test_the_extra_tokens_and_their_price_are_estimated_and_labelled(db_session):
    _, _, _, row = make(db_session, Gen())
    calls = json.loads(row.plan_json)["story_chapters_cost"]["en"]
    assert [c["agent"] for c in calls] == ["chapter writer", "chapter auditor"] and all(c["estimate"] and c["tokens_in"] > 0 for c in calls)
    assert calls[0]["tokens_out"] > 0 and calls[0]["model"] == "gemini-3.8-flash" and (calls[0]["cost_eur"] is None or calls[0]["cost_eur"] > 0)


def test_a_plan_made_before_the_chapters_existed_is_written_again_by_the_stage(db_session):
    _, _, _, row = make(db_session, Gen())
    assert CH.plan_covers(row, ["en"]) and not CH.plan_covers(row, ["de"])
    plan = json.loads(row.plan_json)
    del plan["story_chapters"]
    row.plan_json = json.dumps(plan)
    assert not CH.plan_covers(row, ["en"])


def test_a_plan_without_chapters_gets_them_from_the_same_writer_without_touching_the_cards(db_session):
    from app.agents.chapters import ChapterWriter

    case, master, bp, row = make(db_session, Gen())
    plan = json.loads(row.plan_json)
    del plan["story_chapters"]
    row.plan_json = json.dumps(plan)
    db_session.commit()
    gen = Gen()
    newer = asyncio.run(ChapterWriter(gen=gen).add_youtube_parts(db_session, case, bp, master, "en", None, row))
    payload = gen.payloads[0][1]
    assert "chapters" not in payload and "events" not in payload and payload["story_beats"] and "description" not in payload
    after = json.loads(newer.plan_json)
    assert after["film_title"] == plan["film_title"] and after["story_chapters"]["en"][0][0] == "B01" and after["story_chapters_backfilled"] == {"en": True} and newer.version == row.version + 1


# ---------------------------------------------------------------- resolved on the finished film, stored per language


def rendered(db, case, plan_row, bp, *starts, language="en", duration=4000.0):
    master = db.query(StoryVersion).filter(StoryVersion.case_id == case.id).first()
    script = {"duration": duration, "beats": [{"beat_id": f"B{i + 1:02d}", "start": s, "end": s + 5} for i, s in enumerate(starts)]}
    ps = ProductionScript(case_id=case.id, story_version_id=master.id, language=language, blueprint_id=bp.id, status="rendered", duration_seconds=duration, script_json=json.dumps(script))
    db.add(ps)
    db.flush()
    video = Video(case_id=case.id, language=language, production_script_id=ps.id, title="T", status_at_production="UNSOLVED", duration_seconds=duration, production_type="original")
    db.add(video)
    db.commit()
    return video


def timeline(n=24, step=160.0, first=52.0):
    return [first] + [first + i * step for i in range(1, n)]


def test_a_rendered_film_gets_its_chapters_stored_and_the_same_render_keeps_them(db_session):
    case, _, bp, row = make(db_session, Gen())
    video = rendered(db_session, case, row, bp, *timeline())
    first = YT.resolve(db_session, case, video)
    db_session.commit()
    assert first.status == "ok" and json.loads(first.chapters_json)["chapters"][0]["time"] == "00:00" and first.plan_id == row.id and first.production_script_id == video.production_script_id
    stamp = first.resolved_at
    again = YT.resolve(db_session, case, video)
    assert again.id == first.id and again.resolved_at == stamp


def test_a_new_render_moves_the_times_and_asks_no_model(db_session):
    case, _, bp, row = make(db_session, Gen())
    old = rendered(db_session, case, row, bp, *timeline())
    first = YT.resolve(db_session, case, old)
    db_session.commit()
    before = [c["time"] for c in json.loads(first.chapters_json)["chapters"]]
    new = rendered(db_session, case, row, bp, *timeline(step=200.0, first=70.0))
    after = YT.resolve(db_session, case, new)
    assert after.id == first.id and after.video_id == new.id and [c["time"] for c in json.loads(after.chapters_json)["chapters"]] != before
    assert db_session.query(VideoChapters).filter(VideoChapters.case_id == case.id).count() == 1


def test_a_film_without_a_plan_or_a_render_has_a_status_not_a_list(db_session):
    case, master, bp = film(db_session)
    ps_video = rendered(db_session, case, None, bp, *timeline())
    assert YT.resolve(db_session, case, ps_video).status == "no_plan"
    bare = Video(case_id=case.id, language="de", title="T", status_at_production="UNSOLVED", production_type="original")
    db_session.add(bare)
    db_session.commit()
    assert YT.resolve(db_session, case, bare).status == "no_render"


def test_a_short_film_cannot_hold_three_chapters_and_is_omitted(db_session):
    case, _, bp, row = make(db_session, Gen())
    video = rendered(db_session, case, row, bp, *timeline(step=1.0, first=0.0), duration=60.0)
    done = YT.resolve(db_session, case, video)
    assert done.status == "omitted" and "fewer than 3" in done.reason


def test_the_chapters_are_in_the_description_once_however_often_it_is_built(db_session):
    case, _, bp, row = make(db_session, Gen())
    video = rendered(db_session, case, row, bp, *timeline())
    VID.apply_metadata(db_session, case, video)
    VID.apply_metadata(db_session, case, video)
    text = video.youtube_description
    assert text.count("00:00 Chapter B01") == 1 and text.startswith("A call at 2:31") and "Chapter B19" in text


def test_an_omitted_list_adds_nothing_to_the_description_and_never_blocks_the_film(db_session):
    case, _, bp, row = make(db_session, Gen())
    video = rendered(db_session, case, row, bp, *timeline(step=1.0, first=0.0), duration=60.0)
    VID.apply_metadata(db_session, case, video)
    assert "00:00" not in (video.youtube_description or "") and video.youtube_title is not None


# ---------------------------------------------------------------- the routes


def test_the_routes_show_every_language_refresh_the_description_and_write_missing_chapters(db_session, client, monkeypatch):
    case, _, bp, row = make(db_session, Gen())
    video = rendered(db_session, case, row, bp, *timeline())
    overview = client.get(f"/api/cases/{case.id}/chapters/youtube").json()
    english = next(x for x in overview["languages"] if x["language"] == "en")
    assert english["status"] == "ok" and english["text"].splitlines()[0] == "00:00 Chapter B01" and english["duration"] == "1:06:40"
    assert {x["status"] for x in overview["languages"] if x["language"] != "en"} == {"no_render"}
    assert english["cost"]["extra_llm_requests"] == 0 and english["method"]
    client.post(f"/api/cases/{case.id}/chapters/youtube/refresh")
    db_session.refresh(video)
    assert "00:00 Chapter B01" in video.youtube_description
    plan = json.loads(row.plan_json)
    del plan["story_chapters"]
    row.plan_json = json.dumps(plan)
    db_session.commit()
    monkeypatch.setattr("app.agents.chapters.get_generation_provider", lambda: Gen())
    done = client.post(f"/api/cases/{case.id}/chapters/youtube/plan", json={"languages": ["en"]}).json()
    assert done["planned"] == ["en"] and next(x for x in done["languages"] if x["language"] == "en")["cost"]["extra_llm_requests"] == 2
    assert client.post(f"/api/cases/{case.id}/chapters/youtube/plan", json={"languages": ["xx"]}).status_code == 409
    assert client.get("/api/cases/99999/chapters/youtube").status_code == 404
