"""The YouTube description: the narrative the chapter writer adds to its own call (and the auditor reads), the deterministic checks, the assembly around it (chapters, sources, credits,
disclosure, hashtags, channel) within YouTube's limit, the owner's own text kept, the rebuild without a model call, the plain fallback, persistence and the routes."""

import asyncio
import json
import uuid

import pytest
from test_youtube_chapters import DESCRIPTION, Gen, film, make, rendered, timeline

from app.core.ai_config import ai_config
from app.db.base import SessionLocal
from app.db.models import Case, VideoDescription
from app.documentary import chapters as CH
from app.documentary import youtube_chapters as YT
from app.documentary import youtube_description as YD
from app.lifecycle import videos as VID

CFG = ai_config.description_settings()
EN_TEXT = ("A call at 2:31 in the morning reports an intruder. The documentary follows what the first officers found at the house and the questions that investigators raised that night. "
           "We look at the people involved, the timeline of the hours that followed and what remains unanswered. " * 2 + "Join us and tell us what you think.")


def case_of(**over):
    return Case(canonical_title="Murder of Ashley Flynn", slug=f"flynn-{uuid.uuid4().hex[:8]}", resolution_status=over.pop("resolution_status", "UNSOLVED"),
                people_json=json.dumps(["Ashley Flynn", "Caleb Flynn"]), location="Troy, Ohio", identifiers_json=over.pop("identifiers_json", "[]"), **over)


KNOWN = {"ashley", "flynn", "caleb", "troy", "ohio", "clueVera".casefold(), "cluevera"}


def types(issues, severity=None):
    return {i["type"] for i in issues if severity is None or i["severity"] == severity}


def test_the_narrative_is_cleaned_to_what_youtube_takes():
    assert YD.clean_narrative("  #Hook <b>line</b>  here \n\n\n Second   paragraph.\nThird.") == "Hook bline/b here\n\nSecond paragraph.\n\nThird."


def test_a_good_narrative_has_no_error(db_session):
    assert types(YD.check(EN_TEXT, case_of(), "en", "The Singer in the Pulpit", KNOWN), "error") == set()


def test_the_length_is_bounded_but_the_target_is_a_guideline(db_session):
    case = case_of()
    assert "length" in types(YD.check("Too short to be a description.", case, "en", None, KNOWN), "error")
    assert "length" in types(YD.check("word " * (CFG.max_words + 1), case, "en", None, KNOWN), "error")
    assert "length" not in types(YD.check("Quiet house tonight " * 20, case, "en", None, KNOWN), "error")


def test_the_script_must_belong_to_the_language():
    case = case_of()
    assert "reference" in types(YD.check(EN_TEXT + " Read more at https://news.example/story or www.example.com", case, "en", None, KNOWN), "error")
    assert "wrong_script" in types(YD.check("این یک متن فارسی است که در زبان انگلیسی نباید بیاید " * 5, case, "en", None, KNOWN), "error")
    persian = "یک تماس نیمه‌شب از خانه‌ای در شهر تروی، ماجرایی که پرسش‌های بسیاری برجای گذاشت. " * 6
    assert "wrong_script" not in types(YD.check(persian, case, "fa", None, KNOWN), "error")
    assert "wrong_script" in types(YD.check(EN_TEXT, case, "fa", None, KNOWN), "error") and "wrong_script" in types(YD.check(EN_TEXT, case, "ar", None, KNOWN), "error")


def test_an_outcome_word_an_accusation_and_a_claim_about_a_fictional_case_are_errors():
    case = case_of()
    assert "outcome" in types(YD.check(EN_TEXT + " He was convicted.", case, "en", None, KNOWN), "error")
    assert "unsupported_accusation" in types(YD.check(EN_TEXT + " Is the killer her husband?", case, "en", None, KNOWN), "error")
    solved = case_of(resolution_status="SOLVED")
    assert "unsupported_accusation" not in types(YD.check(EN_TEXT + " Who was the killer?", solved, "en", None, KNOWN), "error")
    fictional = case_of(identifiers_json=json.dumps([{"kind": "fictional"}]))
    assert "fictional_real_claim" in types(YD.check(EN_TEXT + " A true crime story.", fictional, "en", None, KNOWN), "error")


def test_repeated_words_unknown_names_and_a_title_said_whole_are_warnings():
    case = case_of()
    stuffed = EN_TEXT + " investigation " * (CFG.repeat_word_limit + 1)
    assert "keyword_repetition" in types(YD.check(stuffed, case, "en", None, KNOWN), "warning")
    assert "name_unverified" in types(YD.check(EN_TEXT + " Sergeant Maxwell Quill arrived.", case, "en", None, KNOWN), "warning")
    assert "repeats_title" in types(YD.check(EN_TEXT, case, "en", "A call at 2:31 in the morning reports", KNOWN), "warning")


def test_two_or_three_hashtags_with_the_case_the_language_and_the_channel_when_it_is_latin(db_session):
    case = case_of()
    assert YD.hashtags(case, "en") == ["#AshleyFlynn", "#TrueCrime", "#ClueVera"]
    assert YD.hashtags(case, "fa") == ["#AshleyFlynn", "#ترو_کرایم"] and YD.hashtags(case, "tr")[1] == "#GerçekSuç" and YD.hashtags(case, "pt")[1] == "#CrimesReais"
    assert all(" " not in tag and tag.startswith("#") for lang in ("en", "de", "fa", "ar", "es", "it", "fr", "pt", "tr") for tag in YD.hashtags(case, lang)) and len(YD.hashtags(case, "de")) <= 3


def parts(**over):
    base = dict(language="en", narrative="Narrative text.", chapters="00:00 One\n05:00 Two\n10:00 Three", presenter=True, tags=["#A", "#B"], channel_text="ClueVera — https://youtube.com/channel/x")
    base.update(over)
    return base


def test_the_description_has_every_part_in_order_with_the_disclosure_and_the_presenter_line_and_no_reference():
    text, dropped = YD.assemble(**parts())
    assert dropped == [] and text.split("\n\n") == ["Narrative text.", "00:00 One\n05:00 Two\n10:00 Three", "This documentary uses AI-assisted narration. The presenter is an AI-generated avatar.",
                                                    "ClueVera — https://youtube.com/channel/x", "#A #B"]
    assert "presenter" not in YD.assemble(**parts(presenter=False))[0]
    persian, _ = YD.assemble(**parts(language="fa"))
    assert "آواتار" in persian and "Sources" not in text and "Picture credits" not in text


def test_a_description_over_the_limit_loses_the_channel_line_and_never_the_disclosure(monkeypatch):
    monkeypatch.setattr(CFG, "max_chars", 300)
    text, dropped = YD.assemble(**parts(narrative="N" * 150))
    assert dropped == ["the channel line"] and len(text) <= 300 and "ClueVera" not in text and "AI-assisted narration" in text and text.endswith("#A #B")
    assert YD.assemble(**parts(narrative="N" * 290))[0] is None


def test_a_language_added_later_gets_a_description_with_the_english_disclosure(monkeypatch):
    monkeypatch.setattr(ai_config.multilingual, "optional_production_languages", ["nl"])
    from app.tags.dossier import tagged_languages

    assert tagged_languages()[-1] == "nl" and "AI-assisted narration" in YD.assemble(**parts(language="nl"))[0]


# ---------------------------------------------------------------- the writer adds the narrative to its own call


def test_the_writer_is_given_the_case_record_and_the_first_beats_and_writes_the_description_in_the_same_call(db_session):
    gen = Gen()
    case, _, _, row = make(db_session, gen)
    role, payload = gen.payloads[0]
    block = payload["description"]
    assert role == "chapter_writer" and block["words"] == CFG.target_words and block["case"]["name"] == case.canonical_title
    assert not {"central_question", "human_thread", "editorial_thesis"} & set(block["case"]) and "central_question" not in json.dumps(block)
    assert block["beats"] == [b for b in payload["story_beats"] if b[0] == "B01"] and "example" in block and "tone" in block
    assert gen.calls == ["chapter_writer", "chapter_auditor"] and json.loads(row.plan_json)["descriptions"]["en"] == DESCRIPTION
    audit_role, audit = gen.payloads[1]
    assert audit["texts"]["description"] == DESCRIPTION and "example" not in audit["description"] and audit["story_beats"]


def test_without_the_description_the_same_two_calls_are_made_and_nothing_of_it_is_sent(db_session, monkeypatch):
    with_it, without = Gen(), Gen()
    make(db_session, with_it)
    monkeypatch.setattr(CFG, "enabled", False)
    make(db_session, without)
    assert with_it.calls == without.calls and "description" not in without.payloads[0][1] and "description" not in without.payloads[1][1]


def test_a_description_the_auditor_refuses_is_written_again_in_the_same_loop_and_a_short_one_is_sent_back(db_session):
    bad = Gen(bad=DESCRIPTION)
    _, _, _, row = make(db_session, bad)
    assert bad.calls.count("chapter_writer") == 3 and json.loads(row.plan_json)["descriptions"]["en"] is None and CH.plan_covers(row, ["en"])
    short = Gen()
    original = short.generate_structured

    async def tiny(role, system, user, images=None):
        out, res = await original(role, system, user, images)
        if role == "chapter_writer" and "description" in out and not json.loads(user).get("fix"):
            out["description"] = "Far too short."
        return out, res

    short.generate_structured = tiny
    _, _, _, row = make(db_session, short)
    fixes = [d for role, d in short.payloads if role == "chapter_writer" and d.get("fix")]
    assert fixes and any("words" in (i["auditor"] or "") for i in fixes[0]["fix"] if i["key"] == "description") and json.loads(row.plan_json)["descriptions"]["en"] == DESCRIPTION


def test_a_plan_without_a_description_is_not_covered_and_the_backfill_asks_for_the_description_only(db_session):
    from app.agents.chapters import ChapterWriter

    case, master, bp, row = make(db_session, Gen())
    plan = json.loads(row.plan_json)
    del plan["descriptions"]
    row.plan_json = json.dumps(plan)
    db_session.commit()
    assert not CH.plan_covers(row, ["en"])
    gen = Gen()
    newer = asyncio.run(ChapterWriter(gen=gen).add_youtube_parts(db_session, case, bp, master, "en", None, row))
    payload = gen.payloads[0][1]
    assert "description" in payload and "story_chapters" not in payload and "chapters" not in payload and json.loads(newer.plan_json)["descriptions"]["en"] == DESCRIPTION
    assert asyncio.run(ChapterWriter(gen=Gen()).add_youtube_parts(db_session, case, bp, master, "en", None, newer)) is newer


# ---------------------------------------------------------------- the description of a rendered film


def render(db, gen=None):
    case, _, bp, row = make(db, gen or Gen())
    video = rendered(db, case, row, bp, *timeline())
    ps = db.get(__import__("app.db.models", fromlist=["ProductionScript"]).ProductionScript, video.production_script_id)
    script = json.loads(ps.script_json)
    script["credits"] = ["Arnij at Dutch Wikipedia / Wikimedia Commons"]
    script["host"] = [{"scene_id": 1}]
    ps.script_json = json.dumps(script)
    db.commit()
    return case, video, row


def test_a_film_gets_its_description_assembled_around_the_narrative_and_it_is_stored_per_language(db_session):
    case, video, _ = render(db_session)
    row = YD.resolve(db_session, case, video)
    db_session.commit()
    assert row.status == "ok" and row.narrative == DESCRIPTION
    parts_ = row.auto_text.split("\n\n")
    assert parts_[0] == DESCRIPTION and parts_[1].startswith("00:00 Chapter B01") and "Wikimedia" not in row.auto_text and "Sources" not in row.auto_text and "http" not in row.auto_text.replace("https://www.youtube.com", "")
    assert "The presenter is an AI-generated avatar." in row.auto_text and parts_[-1].startswith("#") and len(row.auto_text) <= CFG.max_chars
    again = SessionLocal()
    try:
        assert again.query(VideoDescription).filter_by(case_id=case.id, language="en").one().auto_text == row.auto_text
    finally:
        again.close()


def test_the_same_inputs_keep_the_row_and_a_new_chapter_time_rebuilds_it_without_the_model(db_session):
    case, video, plan = render(db_session)
    first = YD.resolve(db_session, case, video)
    db_session.commit()
    stamp, before = first.resolved_at, first.auto_text
    assert YD.resolve(db_session, case, video).resolved_at == stamp
    from test_youtube_chapters import rendered as render_again

    newer = render_again(db_session, case, plan, db_session.get(__import__("app.db.models", fromlist=["EditorialBlueprint"]).EditorialBlueprint, plan.blueprint_id), *timeline(step=200.0, first=70.0))
    rebuilt = YD.resolve(db_session, case, newer)
    assert rebuilt.id == first.id and rebuilt.narrative == DESCRIPTION and rebuilt.auto_text != before


def test_a_film_without_a_narrative_or_with_an_outcome_in_it_keeps_the_plain_description(db_session):
    case, video, row = render(db_session)
    plan = json.loads(row.plan_json)
    plan["descriptions"] = {"en": None}
    row.plan_json = json.dumps(plan)
    db_session.commit()
    assert YD.resolve(db_session, case, video).status == "no_plan"
    VID.apply_metadata(db_session, case, video)
    assert "Status: UNSOLVED" in video.youtube_description and "AI-assisted narration" not in video.youtube_description
    plan["descriptions"] = {"en": EN_TEXT + " He was convicted of the murder."}
    row.plan_json = json.dumps(plan)
    db_session.commit()
    done = YD.resolve(db_session, case, video)
    assert done.status == "invalid" and "convict" in done.reason and YD.final_text(done) == ""


def test_the_owners_text_is_the_text_in_use_and_the_app_never_changes_it(db_session):
    case, video, _ = render(db_session)
    row = YD.resolve(db_session, case, video)
    YD.save_edit(db_session, row, "My own description <b>text</b>")
    db_session.commit()
    assert YD.final_text(row) == "My own description btext/b" and YD.view(row, "en")["edited"] is True
    VID.apply_metadata(db_session, case, video)
    assert video.youtube_description == "My own description btext/b"
    row.fingerprint = None
    assert YD.resolve(db_session, case, video).edited_text == "My own description btext/b"
    YD.save_edit(db_session, row, None)
    assert YD.final_text(row) == row.auto_text and YD.view(row, "en")["edited"] is False


def test_the_film_publishes_the_assembled_description_once_and_the_follow_up_head_stays(db_session):
    case, video, _ = render(db_session)
    VID.apply_metadata(db_session, case, video)
    VID.apply_metadata(db_session, case, video)
    text = video.youtube_description
    assert text.count("00:00 Chapter B01") == 1 and text.startswith(DESCRIPTION) and text.rstrip().splitlines()[-1].startswith("#") and "Status: UNSOLVED" not in text


def test_the_title_said_whole_in_the_opening_is_flagged_once_the_title_is_chosen(db_session):
    from app.identity import titles as T

    case, video, _ = render(db_session)
    T.sync_identity(db_session, case, "en", title="A call at 2:31 in the morning reports")
    assert "repeats_title" in types(json.loads(YD.resolve(db_session, case, video).validation_json)["issues"], "warning")


# ---------------------------------------------------------------- the routes


def test_the_routes_show_edit_and_regenerate_the_description_of_each_language(db_session, client, monkeypatch):
    case, video, _ = render(db_session)
    base = f"/api/cases/{case.id}/descriptions"
    out = client.get(base).json()
    english = next(x for x in out["languages"] if x["language"] == "en")
    assert english["status"] == "ok" and english["text"].startswith(DESCRIPTION) and english["chars"] == len(english["text"]) <= english["max_chars"] == 5000 and english["hook"]
    assert english["cost"]["extra_llm_requests"] == 0 and {x["status"] for x in out["languages"] if x["language"] != "en"} == {"no_render"}
    edited = client.put(f"{base}/en", json={"text": "Mine"}).json()
    assert next(x for x in edited["languages"] if x["language"] == "en")["edited"] is True
    db_session.refresh(video)
    assert video.youtube_description == "Mine"
    back = client.put(f"{base}/en", json={"text": None}).json()
    assert next(x for x in back["languages"] if x["language"] == "en")["edited"] is False
    monkeypatch.setattr("app.agents.chapters.get_generation_provider", lambda: Gen())
    again = client.post(f"{base}/en/regenerate").json()
    assert next(x for x in again["languages"] if x["language"] == "en")["cost"]["extra_llm_requests"] == 2
    assert client.put(f"{base}/xx", json={"text": "x"}).status_code == 409 and client.get("/api/cases/99999/descriptions").status_code == 404
    assert client.post(f"{base}/de/regenerate").status_code == 409


# ---------------------------------------------------------------- no hints, no false name warnings, no central question


def beat(i, purpose):
    return [f"B{i:02d}", purpose, f"Summary {i}"]


def test_the_writer_gets_only_the_opening_beats_that_set_the_scene_and_never_the_first_evidence():
    beats = [beat(1, "hook"), beat(2, "orientation"), beat(3, "human_introduction"), beat(4, "evidence"), beat(5, "human_introduction")] + [beat(i, "investigation") for i in range(6, 26)]
    assert [b[0] for b in CH.safe_beats(beats)] == ["B01", "B02", "B03"]
    assert [b[0] for b in CH.safe_beats([beat(1, "evidence"), beat(2, "hook")])] == ["B01"]
    many = [beat(i, "orientation") for i in range(1, 51)]
    assert len(CH.safe_beats(many)) == round(50 * CFG.safe_beat_share)


@pytest.mark.parametrize("language,text", [("en", "We ask whether that account held together."), ("en", "A scene that does not fit a simple burglary."), ("de", "Eine Szene, die nicht passt nicht zu einem Einbruch."),
                                           ("fa", "آیا روایت اولیه با شواهد جور درمی‌آید؟"), ("ar", "فهل دخل غريب ذلك البيت فعلًا؟"), ("es", "Una escena que no encaja con un robo."),
                                           ("it", "Il racconto di Caleb non resta sempre lo stesso."), ("fr", "Un récit qui ne tient pas."), ("pt", "Há algo que essa versão não explica."),
                                           ("tr", "Yoksa sahnede görünenden fazlası mı vardı?")])
def test_a_description_that_casts_doubt_on_an_account_or_hints_at_staging_is_an_error_in_every_language(language, text):
    assert "hints" in YD.reveal_issue(text, language)
    assert "outcome" in types(YD.check(EN_TEXT + " " + text, case_of(), "en", None, KNOWN), "error") if language == "en" else True


def test_a_neutral_question_passes():
    assert YD.reveal_issue("What happened in that house, and what did the first officers find?", "en") is None


def test_a_hint_sends_the_description_back_to_the_writer_before_the_auditor_reads_it():
    from app.agents.chapters import ChapterWriter

    assert "hints" in ChapterWriter.precheck("description", EN_TEXT + " Did the account hold up?", "en")
    assert ChapterWriter.precheck("description", EN_TEXT, "en") is None and ChapterWriter.precheck("description", EN_TEXT + " He was convicted.", "en")


def test_a_chapter_title_that_casts_doubt_gets_the_neutral_title():
    out = YT.build([["B01", "The 911 call"], ["B02", "A story that does not add up"], ["B03", "The garage"]], beats_for(0, 300, 600), 900, "en")
    assert [c["source"] for c in out["chapters"]] == ["writer", "fallback", "writer"]


def beats_for(*starts):
    return [{"beat_id": f"B{i + 1:02d}", "start": s, "end": s + 5} for i, s in enumerate(starts)]


def test_sentence_openers_and_german_nouns_are_no_names_but_an_unknown_name_inside_a_sentence_is():
    case = case_of()
    english = "Watch this one with us. Someone broke into the house on Cunningham Court, he said. " * 3 + EN_TEXT
    assert "name_unverified" in types(YD.check(english, case, "en", None, KNOWN), "warning")
    flagged = [i["message"] for i in YD.check(english, case, "en", None, KNOWN) if i["type"] == "name_unverified"][0]
    assert "Cunningham" in flagged and "Watch" not in flagged and "Someone" not in flagged
    assert "name_unverified" not in types(YD.check(english, case, "en", None, KNOWN | {"cunningham", "court"}), "warning")
    german = "Der Notruf kam um 2:31 Uhr. " + "Wir folgen den Aufnahmen der Bodycam und den Aussagen vor Ort. " * 12
    assert "name_unverified" not in types(YD.check(german, case, "de", None, KNOWN), "warning")
