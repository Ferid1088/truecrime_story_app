"""The creative pack: every title, cover text, Short hook and Short script of a case for all nine languages from ONE request to one model that sees the whole case, validated by the code, repaired
once with exactly what is wrong, stored as proposals that never replace anything approved or voiced, and never voicing or publishing anything."""

import asyncio
import json
import uuid

import pytest
from pack_fixture import PackGen, filler, make_clips
from test_host import _plan_story

from app.agents.shorts import CreativePack
from app.core.ai_config import ai_config
from app.db.models import Case, CaseTitleCandidate, CoverProposal, EpisodeIdentity, ShortClip, StoryVersion
from app.naming.agent import title_languages
from app.shortform import clips as C
from app.shortform import pack as PK


@pytest.fixture()
def story_of(db_session):
    db_session.query(CaseTitleCandidate).delete()
    db_session.query(EpisodeIdentity).delete()
    db_session.commit()
    case, master, _ = _plan_story(db_session)
    return case, master, PK.story_of(db_session, case, master)


async def _first(gen, story):
    return await CreativePack(gen).run(dossier=PK.dossier(story))


def test_the_dossier_holds_the_whole_case_and_the_limits_of_every_language_and_holds_nothing_back(story_of):
    case, master, story = story_of
    given = PK.dossier(story)
    assert list(given["languages"]) == title_languages() and len(given["languages"]) == 9
    assert given["languages"]["fa"]["title_words"] == [7, 18] and given["languages"]["fa"]["cover_words"] == [7, 18] and given["languages"]["en"]["film_title_characters"] > 50
    assert [s["clip_key"] for s in given["slots"]] == ["C1", "C2", "C3", "C4", "C5"] and given["facts"] and given["minimum_concepts"] >= 8
    assert len(given["acts"]) == len(story.acts) and "Wexford" in json.dumps(given["acts"]) and "answer_acts" not in given, "every act goes to the model: nothing of the story is hidden"
    assert given["generic_phrases"]["fa"] and set(given["generic_phrases"]) == set(given["languages"])


def test_all_titles_covers_hooks_and_scripts_of_all_nine_languages_come_from_one_request_and_pass_the_validation(story_of):
    case, master, story = story_of
    gen = PackGen()
    out = asyncio.run(PK.generate(gen, story))
    assert len(gen.calls) == 1 and gen.calls[0][0] == "creative_pack" and out["problems"] == {} and out["repairs"] == 0
    assert set(out["pack"]["languages"]) == set(title_languages()) and all(len(body["shorts"]) == 5 and body["film"]["title"] and body["film"]["cover_text"] for body in out["pack"]["languages"].values())
    assert len(out["pack"]["scouting"]["concepts"]) >= 8 and out["pack"]["checks"]["first"] and out["pack"]["checks"]["second"]


def test_a_title_or_cover_text_below_seven_words_or_above_eighteen_is_refused_in_every_place(story_of):
    case, master, story = story_of

    def damage(pack):
        fa = pack["languages"]["fa"]
        fa["film"]["title"], fa["film"]["cover_text"] = filler("fa", 6), filler("fa", 19)
        fa["shorts"][0]["title"], fa["shorts"][0]["cover_text"], fa["shorts"][0]["cover_alternative"] = filler("fa", 5), filler("fa", 6), filler("fa", 20)

    out = asyncio.run(PK.generate(PackGen(damage=damage), story))
    assert out["repairs"] == 1 and out["problems"] == {}                                                       # the repair fixes them
    first = PK.validate(asyncio.run(_first(PackGen(damage=damage), story)), PK.dossier(story))
    types = {i["type"] for i in first["fa/film"]} | {i["type"] for i in first["fa/C1"]}
    assert {"film_title_words", "film_cover_words", "short_title_words", "short_cover_text_words", "short_cover_alternative_words"} <= types


def test_a_weak_concept_choice_a_missing_clip_a_missing_language_a_made_up_fact_id_and_an_empty_phrase_are_found_by_the_code(story_of):
    case, master, story = story_of

    def damage(pack):
        pack["scouting"]["concepts"] = pack["scouting"]["concepts"][:4]
        del pack["languages"]["tr"]
        del pack["languages"]["de"]["shorts"][2]
        pack["languages"]["en"]["shorts"][0]["evidence_ids"] = ["F999"]
        pack["languages"]["es"]["shorts"][1]["detail"] = filler("es", 12) + ". " + "Nadie esperaba lo que ocurrió después."

    problems = PK.validate(asyncio.run(_first(PackGen(damage=damage), story)), PK.dossier(story))
    assert "too_few_concepts" in {i["type"] for i in problems["scouting"]} and "language_missing" in {i["type"] for i in problems["tr"]}
    assert "clip_missing" in {i["type"] for i in problems["de/C3"]} and "unknown_fact_id" in {i["type"] for i in problems["en/C1"]}
    assert "generic_phrase" in {i["type"] for i in problems["es/C2"]}


def test_a_concept_without_a_verified_fact_id_or_with_too_little_reliability_is_never_chosen():
    facts, acts = {"F001", "F002"}, {"act1"}
    base = {"category": "x", "angle": "question", "proof": "", "status": "testimony", "risk": "none", "act_ids": ["act1", "ghost"]}
    good = {m: 5 for m in PK.MEASURES}
    found = [{**base, "concept": "invented id", "evidence_ids": ["F999"], "scores": {**good, "emotional_intensity": 10}},
             {**base, "concept": "no id", "evidence_ids": [], "scores": {**good, "emotional_intensity": 10}},
             {**base, "concept": "unreliable", "evidence_ids": ["F001"], "scores": {**good, "emotional_intensity": 10, "reliability": 2}},
             {**base, "concept": "sound", "evidence_ids": ["F001", "F002"], "scores": good}]
    rated = PK.rate(found, facts, acts)
    assert [r["concept"] for r in rated if r["choosable"]] == ["sound"] and rated[0]["choosable"] is False
    assert all(r["why_not"] for r in rated if not r["choosable"]) and rated[-1]["act_ids"] == ["act1"]       # an act id that was not given is dropped
    assert PK.overall({m: 10 for m in PK.MEASURES}) == 10.0 and PK.overall({}) == 0.0 and PK.overall({"emotional_intensity": "nonsense"}) == 0.0


def test_a_chosen_concept_that_is_not_choosable_is_refused(story_of):
    case, master, story = story_of

    def damage(pack):
        pack["scouting"]["concepts"][0]["evidence_ids"] = []

    problems = PK.validate(asyncio.run(_first(PackGen(damage=damage), story)), PK.dossier(story))
    assert any(i["type"] == "slot_concept" and i["clip_key"] == "C1" for i in problems["scouting"])


def test_a_repair_request_carries_the_earlier_answer_and_the_problems_and_only_the_configured_number_is_made(story_of):
    case, master, story = story_of
    gen = PackGen(damage=lambda pack: pack["languages"].pop("it"))
    out = asyncio.run(PK.generate(gen, story))
    assert len(gen.calls) == 2 and out["repairs"] == 1 and out["problems"] == {}
    repeat = gen.calls[1][1]
    assert set(repeat["problems_to_fix"]) == {"it"} and "languages" in repeat["previous"]
    stubborn = PackGen()

    async def always(role, system, user, images=None):
        out_, res = await PackGen.generate_structured(stubborn, role, system, user, images)
        out_["languages"].pop("it", None)
        return out_, res

    stubborn.generate_structured = always
    out = asyncio.run(PK.generate(stubborn, story))
    assert out["repairs"] == ai_config.shorts_standard.pack.repairs and "it" in out["problems"] and len(stubborn.calls) == 1 + ai_config.shorts_standard.pack.repairs


def test_a_malformed_answer_is_reported_as_problems_and_never_crashes(story_of):
    case, master, story = story_of
    out = asyncio.run(PK.generate(PackGen(junk=True), story))
    assert out["pack"]["languages"] == {} and out["pack"]["scouting"]["concepts"] == [] and "scouting" in out["problems"] and len(out["problems"]) >= 10


def test_the_pack_is_stored_as_clips_titles_and_cover_texts_for_every_language_with_the_english_clips_as_masters(db_session, story_of):
    case, master, story = story_of
    gen, english = make_clips(db_session, case, master, keep_titles=True)
    assert [r.clip_key for r in english] == ["C1", "C2", "C3", "C4", "C5"] and {r.kind for r in english} == {"master"} and {r.status for r in english} == {"valid"}
    natives = db_session.query(ShortClip).filter(ShortClip.case_id == case.id, ShortClip.language == "fa").order_by(ShortClip.id).all()
    assert [r.kind for r in natives] == ["native"] * 5 and [r.master_clip_id for r in natives] == [m.id for m in english]
    script = json.loads(natives[0].script_json)
    assert script["concept"]["concept"] and script["concept"]["overall"] > 0 and script["package"]["title"] and script["package"]["cover_text"] and script["selection"]["duration"]["kind"] == "estimate"
    assert db_session.query(ShortClip).filter(ShortClip.case_id == case.id).count() == 45
    titles = db_session.query(CaseTitleCandidate).filter_by(case_id=case.id, language="de", origin="pack").all()
    assert titles and any(json.loads(t.critic_json)["rank_score"] == 1.0 for t in titles)
    covers = {p.cover_key: p for p in db_session.query(CoverProposal).filter_by(case_id=case.id, language="fa").all()}
    assert set(covers) == {"long", "C1", "C2", "C3", "C4", "C5"} and all(p.status == "open" and p.title and json.loads(p.validation_json)["pack"] for p in covers.values())
    assert json.loads(covers["C1"].validation_json)["alt_title"] and json.loads(covers["long"].validation_json)["format"] == "16:9"


def test_what_is_voiced_approved_or_the_owners_is_never_replaced_by_the_pack(db_session, story_of):
    case, master, story = story_of
    voiced = ShortClip(case_id=case.id, language="fa", clip_key="C2", kind="native", story_version_id=master.id, version=1, status="valid", script_json="{}", media_json=json.dumps({"voice_seconds": 15.2}))
    db_session.add(voiced)
    db_session.add(CoverProposal(case_id=case.id, language="de", cover_key="C1", kind="short", version=1, source="agent", status="approved", title="An approved cover text that stays exactly as it is", scene_prompt="a door"))
    db_session.add(CoverProposal(case_id=case.id, language="es", cover_key="long", kind="long", version=1, source="owner", status="open", title="The owner wrote this cover text and it stays as written", scene_prompt=""))
    db_session.commit()
    result = asyncio.run(PK.generate(PackGen(), story))
    stored = PK.store(db_session, case, master, result)
    assert "fa/C2" in stored["skipped_clips"] and db_session.query(ShortClip).filter_by(case_id=case.id, language="fa", clip_key="C2").count() == 1
    assert {"de/C1", "es/long"} <= set(stored["skipped_covers"])
    assert db_session.query(CoverProposal).filter_by(case_id=case.id, language="de", cover_key="C1").count() == 1 and db_session.query(CoverProposal).filter_by(case_id=case.id, language="es", cover_key="long").count() == 1


def test_a_title_the_owner_chose_is_not_replaced_by_the_pack(db_session, story_of):
    case, master, story = story_of
    from app.identity.titles import sync_identity

    ident = sync_identity(db_session, case, "en", title="The owner's own title that nobody may replace by itself")
    ident.title_source, ident.title_approved = "owner", True
    db_session.commit()
    PK.store(db_session, case, master, asyncio.run(PK.generate(PackGen(), story)))
    assert db_session.query(EpisodeIdentity).filter_by(case_id=case.id, language="en").one().editorial_title == "The owner's own title that nobody may replace by itself"
    assert db_session.query(CaseTitleCandidate).filter_by(case_id=case.id, language="en", origin="pack").count() >= 3


def test_the_route_makes_and_stores_the_pack_or_only_proposes_it_and_needs_an_english_story(db_session, client, monkeypatch):
    case, master, _ = _plan_story(db_session)
    gen = PackGen()
    monkeypatch.setattr("app.providers.generation.get_generation_provider", lambda: gen)
    before = db_session.query(ShortClip).filter_by(case_id=case.id).count()
    preview = client.post(f"/api/cases/{case.id}/creative-pack?store=false")
    assert preview.status_code == 200 and preview.json()["problems"] == {} and preview.json()["stored"] is None and len(preview.json()["pack"]["languages"]) == 9
    assert db_session.query(ShortClip).filter_by(case_id=case.id).count() == before
    made = client.post(f"/api/cases/{case.id}/creative-pack")
    assert made.status_code == 200 and made.json()["stored"]["clips"] == 45 and len(gen.calls) == 2
    assert client.post("/api/cases/999999/creative-pack").status_code == 404
    bare = Case(canonical_title="No story", slug=f"nostory-{uuid.uuid4().hex[:8]}", language="en")
    db_session.add(bare)
    db_session.commit()
    assert client.post(f"/api/cases/{bare.id}/creative-pack").status_code == 409


def test_the_autopilot_makes_the_pack_once_from_the_approved_english_story_and_a_failure_leaves_the_title_writer_to_work(db_session, story_of):
    from datetime import datetime, timezone

    from app.providers.generation.base import GenerationError

    case, master, story = story_of
    master.approved_at = None
    db_session.commit()
    assert asyncio.run(PK.ensure(db_session, case, PackGen())) is None                      # no approved English final story yet
    master.kind, master.approved_at = "final", datetime.now(timezone.utc)
    db_session.commit()

    class Failing:
        async def generate_structured(self, role, system, user, images=None):
            raise GenerationError("provider", "empty completion", provider="fake")

    assert asyncio.run(PK.ensure(db_session, case, Failing())) is None and db_session.query(CaseTitleCandidate).filter_by(case_id=case.id, origin="pack").count() == 0
    done = asyncio.run(PK.ensure(db_session, case, PackGen()))
    assert done["clips"] == 45 and done["titles"] >= 9 and done["problems"] == []
    again = PackGen()
    assert asyncio.run(PK.ensure(db_session, case, again)) is None and again.calls == []   # once per case


def test_the_pack_never_touches_a_voice_or_a_publishing_provider(story_of, monkeypatch, db_session):
    case, master, story = story_of

    def forbidden(*a, **k):
        raise AssertionError("the pack is text only: no speech, picture or publication")

    for target in ("app.providers.voice.get_voice_provider", "app.documentary.voice_render.get_voice_provider"):
        monkeypatch.setattr(target, forbidden)
    assert asyncio.run(PK.generate(PackGen(), story))["problems"] == {}
    PK.store(db_session, case, master, asyncio.run(PK.generate(PackGen(), story)))


def test_the_duration_is_an_estimate_until_the_voice_is_synthesized_and_then_the_measured_length():
    clip = {"hook": filler("en", 12), "context": filler("en", 5), "detail": filler("en", 16), "reaction": filler("en", 8), "cta": filler("en", 3)}
    estimated = C.with_estimate(clip, "en")["selection"]["duration"]
    assert estimated["kind"] == "estimate" and 8 <= estimated["seconds"] <= 24
    assert C.spoken_seconds({**clip, "detail": "word " * 5}, "en") < C.spoken_seconds(clip, "en")
    row = ShortClip(case_id=1, language="en", clip_key="C1", script_json=json.dumps(clip), media_json="{}")
    assert C.duration_of(row)["kind"] == "estimate"
    row.media_json = json.dumps({"voice_seconds": 16.4})
    assert C.duration_of(row) == {"seconds": 16.4, "kind": "measured"}
