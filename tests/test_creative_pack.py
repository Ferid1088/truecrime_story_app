"""The creative pack (V4): exactly two creative requests per complete pack (GPT-6 writes, GPT-Luna rewrites everything for all languages together), deterministic checks afterwards, free-flowing
narration that the existing voice, avatar and video steps still read, one final title and one final cover text, caching, honest fallbacks, and nothing approved, voiced or the owner's replaced."""

import asyncio
import json
import uuid

import pytest
from pack_fixture import FILL, PackGen, filler, make_clips
from test_host import _plan_story

from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import Case, CaseTitleCandidate, CoverProposal, EpisodeIdentity, ShortClip, StoryVersion
from app.providers.generation.base import GenerationError
from app.shortform import clips as C
from app.shortform import pack as PK

LANGUAGES = ["fa", "en", "de", "es", "tr", "fr", "it", "pt", "ar"]
KEYS = ["C1", "C2", "C3", "C4", "C5"]


@pytest.fixture()
def story_of(db_session):
    db_session.query(CaseTitleCandidate).delete()
    db_session.query(EpisodeIdentity).delete()
    db_session.commit()
    case, master, _ = _plan_story(db_session)
    return case, master, PK.story_of(db_session, case, master)


def run(gen, story, languages=None, key=None):
    return asyncio.run(PK.generate(gen, story, languages, case_key=key or uuid.uuid4().hex))


def test_exactly_one_gpt6_request_and_one_luna_request_make_a_complete_pack(story_of):
    case, master, story = story_of
    gen = PackGen()
    out = run(gen, story)
    assert [r for r, _ in gen.calls] == ["creative_pack", "creative_polisher"] and out["usage"]["calls_made"] == 2 and out["cache"] == "miss" and out["fallback"] is None and out["problems"] == {}


def test_all_selected_languages_and_five_shorts_are_kept_with_one_final_title_and_cover_each(story_of):
    case, master, story = story_of
    out = run(PackGen(), story)
    assert out["languages"] == LANGUAGES and set(out["pack"]["languages"]) == set(LANGUAGES)
    for code, body in out["pack"]["languages"].items():
        assert set(body["film"]) == {"title", "cover_text"} and body["film"]["title"] and body["film"]["cover_text"]
        assert list(body["shorts"]) == KEYS and all(set(s) == {"hook", "story", "avatar", "title", "cover_text", "narration"} and all(s.values()) for s in body["shorts"].values())
    some = run(PackGen(), story, ["fa", "de"])                                            # the case's selected languages only
    assert some["languages"] == ["fa", "de"] and set(some["pack"]["languages"]) == {"fa", "de"}


def test_gpt6_gets_the_whole_story_and_luna_gets_only_gpt6s_texts_and_compact_anchors(story_of):
    case, master, story = story_of
    gen = PackGen()
    out = run(gen, story)
    first, second = gen.called("creative_pack")[0], gen.called("creative_polisher")[0]
    assert len(first["acts"]) == len(story.acts) and "Wexford" in json.dumps(first["acts"]) and first["clip_keys"] == KEYS and first["target_seconds"] == "15-17"
    assert set(first) == {"case", "central_question", "acts", "facts", "names", "clip_keys", "target_seconds", "min_words", "max_words", "situation_count", "speculation_count", "languages"} and not {"slots", "angles", "scores"} & set(first)
    assert first["languages"]["fa"]["short_title_max_characters"] == 100 and first["languages"]["en"]["film_title_max_characters"] > 50
    assert set(second) == {"languages", "anchors", "target_seconds", "min_words", "max_words", "situations", "summary", "pick_count", "speculation_count", "limits"} and "acts" not in second and "facts" not in second and len(second["anchors"]) == 5
    sent = {code: {"film": body["film"], "shorts": [{"clip_key": k, **{f: (s[f].removesuffix(PK._invitation(code)).strip() if f == "avatar" else s[f]) for f in ("hook", "story", "avatar", "title", "cover_text")}} for k, s in body["shorts"].items()]}
            for code, body in out["gpt6"]["languages"].items()}
    assert second["languages"] == sent                                                    # exactly the texts GPT-6 wrote (the closing invitation is added by the code), nothing of its working
    assert all(a["anchor"] for a in second["anchors"])


def test_the_prompts_are_only_the_seven_themes_and_a_technical_contract_with_no_examples_and_no_libraries():
    gpt6, luna = PK.system_text("creative_pack"), PK.system_text("creative_polisher")
    for text in (gpt6, luna):
        assert text.count("\n") < 40 and "هوس، شهوت و قتل" in text and "Rätsel, Hinweise und Enthüllungen" in text and "Never mention a court" in text
        assert "exceptional True Crime storyteller" not in text and "Narration:" not in text
    assert "situation_count" in gpt6 and "speculation_count" in gpt6 and "pick_count" in luna and "suspicious reviewer" in luna
    assert len(gpt6) < 6000 and len(luna) < 6000                                            # compact: no examples, no 630-title or 144-script library
    import app.naming as naming_package
    assert not hasattr(naming_package, "standard")


def test_a_luna_rewrite_that_brings_in_a_number_a_quotation_or_a_name_keeps_gpt6s_wording_for_that_item(story_of):
    case, master, story = story_of

    def polish(languages):
        en = languages["en"]["shorts"]
        en[0]["story"] += " It happened in 1998."
        en[1]["story"] += ' He wrote "I will never forget you".'
        en[2]["story"] += " Then Botner called."
        en[3]["title"] = "A much better polished title for this Short"

    out = run(PackGen(polish=polish), story)
    first = out["gpt6"]["languages"]["en"]["shorts"]
    final = out["pack"]["languages"]["en"]["shorts"]
    assert [final[k]["story"] for k in KEYS[:3]] == [first[k]["story"] for k in KEYS[:3]]
    assert "number 1998" in out["refused"]["en/C1/story"] and any(r.startswith("quotation") for r in out["refused"]["en/C2/story"]) and "name Botner" in out["refused"]["en/C3/story"]
    assert final["C4"]["title"] == "A much better polished title for this Short" and out["sources"]["en/C4/title"] == "luna" and out["sources"]["en/C1/story"] == "gpt6"
    assert out["sources"]["de/C1/hook"] == "luna"                                       # every other item is Luna's


def test_capitalized_words_are_not_names_in_titles_covers_german_or_contractions(story_of):
    case, master, story = story_of

    def polish(languages):
        languages["en"]["film"]["title"] = "He Said I'm Free Before The Call Again"
        languages["en"]["shorts"][0]["cover_text"] = "BEFORE THE CALL THAT CHANGED EVERYTHING FOR THEM ALL"
        languages["de"]["shorts"][0]["story"] += " Ein Notruf kam spät."
        languages["en"]["shorts"][1]["story"] += " I'm sure it was late."

    out = run(PackGen(polish=polish), story)
    assert not [k for k in out["refused"] if k.startswith(("en/", "de/"))] and out["sources"]["en/film/title"] == "luna" and out["sources"]["de/C1/story"] == "luna" and out["sources"]["en/C2/story"] == "luna"


def test_a_quotation_of_the_original_that_luna_loses_is_restored(story_of):
    case, master, story = story_of

    def damage(pack):
        pack["languages"]["en"]["shorts"][0]["story"] = 'She read his message: "I chose her". Nobody had expected that from him at all. It stayed with her.'

    def polish(languages):
        languages["en"]["shorts"][0]["story"] = "She read his message and nobody had expected it from him at all. It stayed with her."

    out = run(PackGen(damage=damage, polish=polish), story)
    assert out["pack"]["languages"]["en"]["shorts"]["C1"]["story"].startswith('She read his message: "I chose her"') and any(r.endswith("lost") for r in out["refused"]["en/C1/story"])


def test_a_cover_text_and_a_title_of_eight_words_are_accepted_without_a_question_or_a_call_to_action(story_of):
    case, master, story = story_of

    def damage(pack):
        pack["languages"]["fa"]["shorts"][0]["cover_text"] = filler("fa", 8)
        pack["languages"]["en"]["film"]["title"] = "one two three four five six seven eight"

    out = run(PackGen(damage=damage, luna="fail"), story)
    assert out["problems"] == {} and out["pack"]["languages"]["fa"]["shorts"]["C1"]["cover_text"] == filler("fa", 8) and len(out["pack"]["languages"]["en"]["film"]["title"].split()) == 8


def test_no_backup_candidate_or_score_field_is_needed_or_kept(story_of):
    case, master, story = story_of
    out = run(PackGen(), story)
    text = json.dumps(out["pack"])
    for banned in ("backup", "candidates", "score", "evidence_ids", "fact_ids", "scouting", "alt_title"):
        assert banned not in text
    assert set(out["pack"]) == {"anchors", "situations", "speculations", "summary", "languages"}


def test_deterministic_validation_finds_only_real_correctness_problems(story_of):
    case, master, story = story_of

    def damage(pack):
        del pack["languages"]["tr"]
        del pack["languages"]["de"]["shorts"][2]
        pack["languages"]["en"]["shorts"][0]["title"] = " ".join(["abcdefghijk"] * 10)               # ten words, 119 characters: over the platform's limit
        pack["languages"]["en"]["shorts"][1]["cover_text"] = ""
        pack["languages"]["fa"]["shorts"][0]["hook"] = "one two three four five six."
        pack["languages"]["ar"]["film"]["title"] = "العنوان with latin words inside one two three four"
        pack["languages"]["es"]["shorts"][0]["story"] = "four short words."
        pack["languages"]["it"]["shorts"][0]["title"] = "too few words in this title"

    out = run(PackGen(damage=damage, luna="fail"), story)
    types = {place: {p["type"] for p in found} for place, found in out["problems"].items()}
    assert types["tr"] == {"language_missing"} and types["de/C3"] == {"clip_missing"} and types["en/C1"] == {"too_long"} and types["en/C2"] == {"empty"}
    assert types["fa/C1"] == {"wrong_script"} and types["ar/film"] == {"wrong_script"} and types["es/C1"] == {"duration"} and types["it/C1"] == {"word_count"}


def test_a_failed_luna_request_keeps_the_validated_gpt6_result_and_says_so_and_a_rerun_asks_only_luna_again(story_of):
    case, master, story = story_of
    gen = PackGen(luna="fail")
    out = run(gen, story, key="again")
    assert out["fallback"]["stage"] == "luna" and "empty completion" in out["fallback"]["reason"] and out["pack"] == out["gpt6"] and out["problems"] == {} and out["sources"] == {}
    assert out["usage"]["calls_made"] == 2 and "luna" not in out["usage"]
    healthy = PackGen()
    again = run(healthy, story, key="again")
    assert [r for r, _ in healthy.calls] == ["creative_polisher"] and again["cache"] == "gpt6" and again["fallback"] is None and again["usage"]["calls_made"] == 1       # GPT-6 is never paid for twice


def test_an_answer_of_luna_without_json_keeps_the_gpt6_result_and_no_other_model_is_asked(story_of):
    case, master, story = story_of
    gen = PackGen(luna="words")
    out = run(gen, story)
    assert out["fallback"]["stage"] == "luna" and "no JSON" in out["fallback"]["reason"] and out["pack"] == out["gpt6"] and len(gen.calls) == 2


def test_a_pack_larger_than_the_configured_capacity_keeps_gpt6_and_sends_nothing_to_luna(story_of, monkeypatch):
    case, master, story = story_of
    monkeypatch.setattr(ai_config.creative_pack.polisher, "max_input_chars", 1000)
    gen = PackGen()
    out = run(gen, story)
    assert "capacity" in out["fallback"]["reason"] and "nothing was cut" in out["fallback"]["reason"] and [r for r, _ in gen.calls] == ["creative_pack"] and out["pack"] == out["gpt6"]
    monkeypatch.setattr(ai_config.creative_pack.polisher, "enabled", False)
    off = PackGen()
    assert run(off, story)["fallback"]["reason"].startswith("disabled") and [r for r, _ in off.calls] == ["creative_pack"]


def test_a_failed_gpt6_request_raises_and_nothing_half_made_is_returned(story_of):
    case, master, story = story_of

    class Down:
        async def generate_structured(self, role, system, user, images=None):
            raise GenerationError("provider", "overloaded", provider="fake")

    with pytest.raises(GenerationError):
        run(Down(), story)
    empty = PackGen(damage=lambda pack: pack.update(languages={}))
    with pytest.raises(GenerationError, match="without any language"):
        run(empty, story)


def test_a_pack_with_unchanged_input_is_never_paid_for_twice(story_of):
    case, master, story = story_of
    gen = PackGen()
    first = run(gen, story, key="same")
    second = run(gen, story, key="same")
    assert len(gen.calls) == 2 and second["cache"] == "final" and second["usage"]["calls_made"] == 0 and second["pack"] == first["pack"]
    other = PackGen()
    assert run(other, story, ["fa", "en"], key="same")["cache"] == "miss" and len(other.calls) == 2                       # another input is another pack
    both = PackGen()

    async def twice():
        return await asyncio.gather(PK.generate(both, story, ["de"], case_key="together"), PK.generate(both, story, ["de"], case_key="together"))

    one, two = asyncio.run(twice())
    assert len(both.calls) == 2 and {one["cache"], two["cache"]} == {"miss", "final"}                                         # two simultaneous callers: one pays


def test_tokens_and_cost_are_logged_for_each_model_and_in_total(story_of, caplog):
    case, master, story = story_of
    with caplog.at_level("INFO"):
        out = run(PackGen(cost=0.02), story)
    assert out["usage"]["gpt6"] == {"model": "fake/creative_pack", "input_tokens": 1000, "output_tokens": 500, "cached_tokens": None, "cost_source": "gateway", "cost_usd": 0.02, "cost_eur": round(0.02 * ai_config.budget.usd_to_eur, 6)}
    assert out["usage"]["luna"]["model"] == "fake/creative_polisher" and out["usage"]["total_cost_usd"] == 0.04 and out["usage"]["total_cost_eur"] > 0
    assert [r.getMessage().split(":")[0] for r in caplog.records if r.getMessage().startswith("creative pack")] == ["creative pack, GPT-6", "creative pack, Luna"]
    unpriced = run(PackGen(cost=None), story)
    assert unpriced["usage"]["gpt6"]["cost_usd"] is None and unpriced["usage"]["total_cost_usd"] is None and unpriced["usage"]["total_cost_eur"] is None         # an unknown cost is never free


def test_a_free_narration_is_cut_into_the_five_parts_the_voice_avatar_and_video_steps_read():
    for code, text in (("fa", "زن می‌دونست شوهرش با بهترین دوستش رابطه داره. حتی برای هر دو شام پخت. اما همون شب، معشوقه تو اتاق‌خواب مرد کشته شد. چرا زن اصرار داشت خودش شام درست کنه؟"),
                       ("en", "Three women thought they were the only love of his life. Then all three received the same message. By morning, two were missing. But he hadn't sent it. Who did?"),
                       ("de", "Er weinte öffentlich um seine ermordete Frau und niemand zweifelte an seiner Trauer, bis ein aufgezeichneter Anruf einen Namen enthüllte, den er verschwiegen hatte.")):
        parts = C.legacy_parts(text, code)
        assert list(parts) == list(C.TEXT_PARTS) and all(parts.values()) and " ".join(parts.values()).split() == text.split()
    with pytest.raises(ValueError):
        C.legacy_parts("only four words here", "en")


def test_a_stored_pack_clip_works_with_the_consumers_of_the_five_parts(db_session, story_of):
    case, master, story = story_of
    gen, english = make_clips(db_session, case, master, keep_titles=True)
    row = english[0]
    script = json.loads(row.script_json)
    assert all(script[part] for part in ("hook", "context", "reaction")) and script["detail"] == "" and script["cta"] == "" and script["narration"] and set(script["package"]) == {"title", "cover_text"} and "angle" not in script
    view = C.clip_dict(row)
    assert view["clip_key"] == "C1" and view["narration"] and C.seconds_of(view)["hook"] == 0.0 and view["selection"]["duration"]["kind"] == "estimate"
    assert C.duration_of(row)["kind"] == "estimate" and C.spoken_seconds(script, "en") == C.narration_seconds(script["narration"], "en")
    assert [r.clip_key for r in C.clips_of(db_session, case.id, "en")] == KEYS


def test_the_pack_is_stored_as_clips_one_title_and_cover_texts_for_every_language_with_the_english_clips_as_masters(db_session, story_of):
    case, master, story = story_of
    gen, english = make_clips(db_session, case, master, keep_titles=True)
    assert [r.clip_key for r in english] == KEYS and {r.kind for r in english} == {"master"} and {r.status for r in english} == {"valid"}
    natives = db_session.query(ShortClip).filter(ShortClip.case_id == case.id, ShortClip.language == "fa").order_by(ShortClip.id).all()
    assert [r.kind for r in natives] == ["native"] * 5 and [r.master_clip_id for r in natives] == [m.id for m in english]
    assert db_session.query(ShortClip).filter(ShortClip.case_id == case.id).count() == 45
    titles = db_session.query(CaseTitleCandidate).filter_by(case_id=case.id, language="de", origin="pack").all()
    assert len(titles) == 1 and json.loads(titles[0].critic_json)["rank_score"] == 1.0
    covers = {p.cover_key: p for p in db_session.query(CoverProposal).filter_by(case_id=case.id, language="fa").all()}
    assert set(covers) == {"long", *KEYS} and all(p.status == "open" and p.title and json.loads(p.validation_json)["pack"] and "alt_title" not in json.loads(p.validation_json) for p in covers.values())


def test_what_is_voiced_approved_or_the_owners_is_never_replaced_by_the_pack(db_session, story_of):
    case, master, story = story_of
    db_session.add(ShortClip(case_id=case.id, language="fa", clip_key="C2", kind="native", story_version_id=master.id, version=1, status="valid", script_json="{}", media_json=json.dumps({"voice_seconds": 15.2})))
    db_session.add(CoverProposal(case_id=case.id, language="de", cover_key="C1", kind="short", version=1, source="agent", status="approved", title="An approved cover text", scene_prompt="a door"))
    db_session.add(CoverProposal(case_id=case.id, language="es", cover_key="long", kind="long", version=1, source="owner", status="open", title="The owner wrote this", scene_prompt=""))
    db_session.commit()
    stored = PK.store(db_session, case, master, run(PackGen(), story))
    assert "fa/C2" in stored["skipped_clips"] and db_session.query(ShortClip).filter_by(case_id=case.id, language="fa", clip_key="C2").count() == 1
    assert {"de/C1", "es/long"} <= set(stored["skipped_covers"])
    assert db_session.query(CoverProposal).filter_by(case_id=case.id, language="de", cover_key="C1").count() == 1 and db_session.query(CoverProposal).filter_by(case_id=case.id, language="es", cover_key="long").count() == 1


def test_a_title_the_owner_chose_is_not_replaced_by_the_pack(db_session, story_of):
    case, master, story = story_of
    from app.identity.titles import sync_identity

    ident = sync_identity(db_session, case, "en", title="The owner's own title that nobody may replace by itself")
    ident.title_source, ident.title_approved = "owner", True
    db_session.commit()
    PK.store(db_session, case, master, run(PackGen(), story))
    assert db_session.query(EpisodeIdentity).filter_by(case_id=case.id, language="en").one().editorial_title == "The owner's own title that nobody may replace by itself"
    assert db_session.query(CaseTitleCandidate).filter_by(case_id=case.id, language="en", origin="pack").count() == 1


def test_an_item_with_a_problem_is_not_stored(db_session, story_of):
    case, master, story = story_of
    result = run(PackGen(damage=lambda pack: pack["languages"]["de"]["shorts"].pop(2), luna="fail"), story)
    stored = PK.store(db_session, case, master, result)
    assert db_session.query(ShortClip).filter_by(case_id=case.id, language="de", clip_key="C3").count() == 0 and stored["clips"] == 44 and "de/C3" in result["problems"]


def test_the_route_makes_and_stores_the_pack_or_only_proposes_it_and_needs_an_english_story(db_session, client, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen = PackGen()
    monkeypatch.setattr("app.providers.generation.get_generation_provider", lambda: gen)
    before = db_session.query(ShortClip).filter_by(case_id=case.id).count()
    preview = client.post(f"/api/cases/{case.id}/creative-pack?store=false")
    assert preview.status_code == 200 and preview.json()["problems"] == {} and preview.json()["stored"] is None and len(preview.json()["pack"]["languages"]) == 9
    assert db_session.query(ShortClip).filter_by(case_id=case.id).count() == before and [r for r, _ in gen.calls] == ["creative_pack", "creative_polisher"]
    made = client.post(f"/api/cases/{case.id}/creative-pack")
    assert made.status_code == 200 and made.json()["stored"]["clips"] == 45 and made.json()["cache"] == "final" and len(gen.calls) == 2          # the second call was free
    assert client.post("/api/cases/999999/creative-pack").status_code == 404
    bare = Case(canonical_title="No story", slug=f"nostory-{uuid.uuid4().hex[:8]}", language="en")
    db_session.add(bare)
    db_session.commit()
    assert client.post(f"/api/cases/{bare.id}/creative-pack").status_code == 409


def test_the_autopilot_makes_english_first_the_others_after_the_owner_released_it_and_a_failure_leaves_the_title_writer_to_work(db_session, story_of, monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(ai_config.covers_settings(), "text_review_languages", ["en", "fa"])           # the owner's review is off in the other tests

    case, master, story = story_of
    master.approved_at = None
    db_session.commit()
    assert asyncio.run(PK.ensure(db_session, case, PackGen(), ["fa", "en"])) is None                      # no approved English final story yet
    master.kind, master.approved_at = "final", datetime.now(timezone.utc)
    db_session.commit()

    class Failing:
        async def generate_structured(self, role, system, user, images=None):
            raise GenerationError("provider", "empty completion", provider="fake")

    assert asyncio.run(PK.ensure(db_session, case, Failing(), ["fa", "en"])) is None and db_session.query(CaseTitleCandidate).filter_by(case_id=case.id, origin="pack").count() == 0
    gen = PackGen()
    english = asyncio.run(PK.ensure(db_session, case, gen, ["fa", "en"]))
    assert english["languages"] == ["en"] and english["clips"] == 5 and english["titles"] == 1 and set(gen.called("creative_pack")[0]["languages"]) == {"en"}
    assert "english_released" not in gen.called("creative_pack")[0]
    waiting = PackGen()
    assert asyncio.run(PK.ensure(db_session, case, waiting, ["fa", "en"])) is None and waiting.calls == []      # the other languages wait for the owner's release of the English texts
    released = {"film_title": "The Title", "film_cover_text": "A cover text", "shorts": {"C1": {"title": "One", "cover_text": "Two"}}}
    monkeypatch.setattr(PK, "released_english", lambda db, c: released)
    gen2 = PackGen()
    rest = asyncio.run(PK.ensure(db_session, case, gen2, ["fa", "en"]))
    asked = gen2.called("creative_pack")[0]
    assert rest["languages"] == ["fa"] and rest["clips"] == 5 and set(asked["languages"]) == {"fa"} and asked["english_released"] == released
    again = PackGen()
    assert asyncio.run(PK.ensure(db_session, case, again, ["fa", "en"])) is None and again.calls == []            # each language once per case


def test_the_pack_never_touches_a_voice_or_a_publishing_provider(story_of, monkeypatch, db_session):
    case, master, story = story_of

    def forbidden(*a, **k):
        raise AssertionError("the pack is text only: no speech, picture or publication")

    for target in ("app.providers.voice.get_voice_provider", "app.documentary.voice_render.get_voice_provider"):
        monkeypatch.setattr(target, forbidden)
    PK.store(db_session, case, master, run(PackGen(), story))


def test_the_configuration_asks_for_the_two_models_and_five_free_shorts():
    cfg = ai_config.creative_pack
    assert cfg.languages == LANGUAGES and cfg.clip_keys == KEYS and cfg.target_seconds == [15, 17] and cfg.short_title_max_characters == 100
    assert ai_config.alias_for("creative_pack") == "sol" and ai_config.alias_for("creative_polisher") == "luna"
    assert ai_config.generation_provider().models["luna"] == "gpt-5.6-luna" and ai_config.covers_settings().title["min_words"] == 8 and ai_config.case_naming.min_words == 8
    assert ai_config.agents["creative_polisher"].output == "text" and FILL


def test_an_almost_json_answer_of_luna_is_repaired_for_free(story_of, monkeypatch):
    case, master, story = story_of
    gen = PackGen()
    original = gen.generate_text

    async def sloppy(role, system, user, images=None):
        result = await original(role, system, user, images)
        result.text = result.text.replace('"polished"}', '"polished",}')          # a trailing comma, as a model sometimes writes it
        return result

    monkeypatch.setattr(gen, "generate_text", sloppy)
    out = run(gen, story, key="sloppy")
    assert out["fallback"] is None and out["sources"]["en/C1/hook"] == "luna"


def test_a_luna_answer_that_cannot_be_parsed_is_kept_and_read_again_by_a_later_run_without_a_new_request(story_of, monkeypatch):
    case, master, story = story_of
    gen = PackGen(luna="words")
    out = run(gen, story, key="kept")
    assert out["fallback"]["stage"] == "luna" and "kept in the cache" in out["fallback"]["reason"] and [r for r, _ in gen.calls] == ["creative_pack", "creative_polisher"]
    healthy = PackGen()
    again = run(healthy, story, key="kept")
    assert [r for r, _ in healthy.calls] == ["creative_polisher"] and again["fallback"] is None          # an unreadable answer is asked for again
    third = PackGen()
    assert run(third, story, key="kept")["cache"] == "final" and third.calls == []


def test_a_request_without_a_gateway_price_is_priced_from_the_configured_rate():
    from app.providers.generation.base import GenerationResult

    luna = PK._usage(GenerationResult(text="{}", model="gpt-5.6-luna", provider="apimaster", input_tokens=9223, output_tokens=5193, cached_tokens=9088))
    from app.services.cost_model import model_cost_eur

    assert luna["cost_source"] == "configured_pricing" and luna["cost_eur"] == round(model_cost_eur("gpt-5.6-luna", 9223, 5193, cached_tokens=9088), 6) and 0.005 < luna["cost_eur"] < 0.01


def test_every_request_is_booked_to_the_case_ledger_in_its_own_stamped_run(db_session, story_of):
    from app.db.models import AgentRun
    from app.services.tracking import track_run

    case, master, story = story_of
    before = db_session.query(AgentRun).filter(AgentRun.case_id == case.id).count()
    asyncio.run(PK.generate(PackGen(), story, ["fa"], case_key=uuid.uuid4().hex, ledger=lambda label: track_run(db_session, case.id, label)))
    runs = db_session.query(AgentRun).filter(AgentRun.case_id == case.id).order_by(AgentRun.id).all()[before:]
    assert [r.agent_name for r in runs] == ["Creative pack: GPT-6", "Creative pack: Luna"] and [r.model for r in runs] == ["fake/creative_pack", "fake/creative_polisher"]
    assert all(r.input_tokens == 1000 and r.output_tokens == 500 and r.status == "completed" for r in runs)
