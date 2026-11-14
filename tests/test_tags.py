"""The YouTube tags: the deterministic checks (formatting repairs, the 500-character boundary, evidence, years, accusations, a fictional case, the script of the language),
the generator with a scripted model (repair round, malformed answers, a timeout), the tag sets of a case (persistence, a regeneration that never replaces the owner's list,
stale, a failing language that stops no other), the routes and the tags the upload takes."""

import asyncio
import json
from datetime import timedelta

import pytest

from app.core.ai_config import ai_config
from app.db.models import Case, Fact, Video, VideoTagSet
from app.lifecycle import videos as VID
from app.providers.generation.base import GenerationResult
from app.tags import dossier as D
from app.tags import generator as G
from app.tags import service as S
from app.tags import validate as V
from app.utils import utc_now

CFG = ai_config.tags_settings()


def dossier(language="en", status="UNSOLVED", fictional=False, channel="ClueVera"):
    text = {"case.title": "Madeleine McCann disappearance", "case.location": "Praia da Luz, Portugal", "case.incident_date": "2007", "F1": "Madeleine McCann disappeared in Praia da Luz in 2007."}
    return {"case_id": 1, "language": language, "case": {"resolution_status": status, "fictional": fictional}, "channel_name": channel, "evidence_refs": list(text), "evidence_text": text,
            "known_words": {"madeleine", "mccann", "maddie", "praia", "luz", "portugal", "cluevera"}}


def tag(text, category="identity", evidence=("case.title",)):
    return V.Tag(text, category, list(evidence))


def kinds(report):
    return {i["type"] for i in report["issues"]}


def test_formatting_is_repaired_and_named_not_silent():
    fixed, done = V.repair([tag(" #Madeleine   McCann "), tag("madeleine mccann"), tag("  "), tag("Praia da Luz, Portugal")])
    assert [t.text for t in fixed] == ["Madeleine McCann", "Praia da Luz Portugal"]
    assert len(done) == 4 and any("duplicate" in d for d in done) and any("empty" in d for d in done)


def test_the_persian_half_space_spelling_is_a_second_tag_not_a_duplicate():
    fixed, _ = V.repair([tag("مادلین مک کان"), tag("مادلین مک‌کان"), tag("مادلین مک کان")])
    assert [t.text for t in fixed] == ["مادلین مک کان", "مادلین مک‌کان"]


def test_the_payload_counts_commas_and_the_quotes_of_tags_with_spaces():
    assert V.payload_length(["abc"]) == 3 and V.payload_length(["abc", "de"]) == 6
    assert V.payload_length(["a b"]) == 5 and V.payload_length([]) == 0


def test_the_limit_is_exact_at_the_boundary():
    one = tag("x" * 499)
    assert V.payload_length([one.text]) == 499 and V.check([one, tag("y")], dossier(), CFG)["ok"] is False
    limit = [tag("a" * 249, "genre_terms", ()), tag("b" * 250, "genre_terms", ())]
    assert V.payload_length([t.text for t in limit]) == 500 and "too_long" not in kinds(V.check(limit, dossier(), CFG))
    over = [tag("a" * 250, "genre_terms", ()), tag("b" * 250, "genre_terms", ())]
    assert "too_long" in kinds(V.check(over, dossier(), CFG))


def test_a_generated_list_loses_its_last_tags_and_says_so():
    tags = [tag(f"{c * 100}", "genre_terms", ()) for c in "abcdefg"]
    kept, said = V.trim_to_fit(tags, 500)
    assert [t.text[0] for t in kept] == list("abcd") and len(said) == 3 and V.payload_length([t.text for t in kept]) <= 500


def test_a_tag_with_no_evidence_or_with_an_invented_one_is_an_error():
    report = V.check([tag("Madeleine McCann", evidence=()), tag("Praia da Luz", "geography_history", ("F999",))], dossier(), CFG)
    assert {"no_evidence", "invented_evidence"} <= kinds(report) and report["ok"] is False


def test_a_year_that_the_evidence_does_not_hold_is_an_error_persian_digits_included():
    d = dossier("fa")
    assert "year_unsupported" in kinds(V.check([tag("پرتغال ۲۰۰۸", "geography_history", ("case.incident_date",)), tag("مادلین مک کان")], d, CFG))
    assert "year_unsupported" not in kinds(V.check([tag("پرتغال ۲۰۰۷", "geography_history", ("case.incident_date",)), tag("مادلین مک کان")], d, CFG))


def test_a_place_name_not_in_the_cited_evidence_is_flagged_for_review_a_generic_word_is_not():
    assert "name_unverified" in kinds(V.check([tag("Algarve", "geography_history", ("case.location",))], dossier(), CFG))
    assert "name_unverified" not in kinds(V.check([tag("Praia da Luz crime", "geography_history", ("case.location",))], dossier(), CFG))


def test_an_accusation_needs_a_solved_case_and_evidence():
    assert "unsupported_accusation" in kinds(V.check([tag("Madeleine McCann killer", "search_phrases")], dossier(status="UNSOLVED"), CFG))
    assert "unsupported_accusation" in kinds(V.check([tag("McCann killer", "search_phrases", ())], dossier(status="SOLVED"), CFG))
    assert "unsupported_accusation" not in kinds(V.check([tag("McCann killer", "search_phrases", ("F1",))], dossier(status="SOLVED"), CFG))


def test_a_fictional_case_may_not_claim_to_be_real():
    report = V.check([tag("true crime documentary", "genre_terms", ())], dossier(fictional=True), CFG)
    assert "fictional_real_claim" in kinds(report)
    assert "fictional_real_claim" not in kinds(V.check([tag("true crime documentary", "genre_terms", ())], dossier(), CFG))


@pytest.mark.parametrize("language,text", [("de", "ungelöster Vermisstenfall Kriminalfall"), ("fr", "disparition non résolue enfant disparue"), ("it", "bambina scomparsa cronaca nera"),
                                           ("es", "desaparición niña desaparecida año"), ("pt", "desaparecimento misterioso não resolvidos"), ("tr", "çözülemeyen kayıp vakası gizemli")])
def test_diacritics_of_latin_languages_pass_and_a_foreign_script_does_not(language, text):
    assert "wrong_script" not in kinds(V.check([tag(text, "genre_terms", ())], dossier(language), CFG))
    assert "wrong_script" in kinds(V.check([tag("مادلین", "genre_terms", ())], dossier(language), CFG))


@pytest.mark.parametrize("language", ["fa", "ar"])
def test_right_to_left_languages_need_native_script_tags(language):
    assert "no_native_script" in kinds(V.check([tag("Madeleine McCann")], dossier(language), CFG))
    report = V.check([tag("Madeleine McCann"), tag("مادلين ماكان")], dossier(language), CFG)
    assert "no_native_script" not in kinds(report) and "native_names_to_review" in kinds(report)


def test_a_channel_tag_is_exactly_the_channel_name():
    assert "wrong_channel" not in kinds(V.check([tag("ClueVera", "channel", ())], dossier(), CFG))
    assert "wrong_channel" in kinds(V.check([tag("Cluevera TV", "channel", ())], dossier(), CFG))
    assert "wrong_channel" in kinds(V.check([tag("ClueVera", "channel", ())], dossier(channel=None), CFG))


def test_hashtags_commas_unknown_categories_and_an_empty_set_are_errors():
    found = kinds(V.check([V.Tag("#maddie", "genre_terms"), V.Tag("a, b", "genre_terms"), V.Tag("c", "nonsense")], dossier(), CFG))
    assert {"hashtag", "separator_in_tag", "unknown_category"} <= found
    assert "empty_set" in kinds(V.check([], dossier(), CFG))


# ---------------------------------------------------------------- the generator and the tag sets of a case


def make_case(db, title="Madeleine McCann disappearance", resolution="UNSOLVED", **over):
    case = Case(canonical_title=title, slug=f"{title.lower().replace(' ', '-')}-{utc_now().timestamp()}", language="en", resolution_status=resolution, location="Praia da Luz, Portugal",
                country="Portugal", incident_date="2007", aliases_json=json.dumps(["Maddie McCann"]), people_json=json.dumps(["Madeleine McCann"]), **over)
    db.add(case)
    db.flush()
    db.add(Fact(case_id=case.id, claim="Madeleine McCann disappeared in Praia da Luz in 2007.", confidence=0.9))
    db.commit()
    return case


def answer(case, language="en", tags=None, **over):
    tags = tags or [{"text": "Madeleine McCann", "category": "identity", "evidence": ["case.title"]},
                    {"text": "Maddie McCann", "category": "identity", "evidence": ["case.aliases"]},
                    {"text": "Praia da Luz", "category": "geography_history", "evidence": ["case.location"]},
                    {"text": "true crime documentary", "category": "genre_terms", "evidence": []}]
    return {"case_id": case.id, "language": language, "tags": tags, "warnings": [], **over}


class Model:
    """A scripted provider: `script` is a list of answers (dicts, or an exception to raise) per call, per language the last one repeats."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def is_configured(self):
        return True

    async def generate_structured(self, role, system, user, images=None):
        data = json.loads(user)
        self.calls.append((role, data))
        step = self.script(data) if callable(self.script) else self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(step, Exception):
            raise step
        return step, GenerationResult(text="{}", model=f"m/{role}", provider="fake")


def use(monkeypatch, script):
    model = Model(script)
    monkeypatch.setattr("app.tags.generator.get_generation_provider", lambda: model)
    return model


def test_the_generator_sends_the_dossier_not_the_script_and_returns_a_checked_list(db_session, monkeypatch):
    case = make_case(db_session)
    model = use(monkeypatch, [answer(case)])
    made = asyncio.run(G.TagGenerator().generate(db_session, case, "en"))
    role, sent = model.calls[0]
    assert role == "tag_generator" and sent["case"]["title"] == case.canonical_title and sent["evidence_refs"][0] == "case.title" and "F" in sent["facts"][0]["id"]
    assert "evidence_text" not in sent and "known_words" not in sent and set(sent["reference_examples"]) >= {"fa", "pt", "ar"} and "english_tags" not in sent
    assert [t.text for t in made["tags"]][:2] == ["Madeleine McCann", "Maddie McCann"] and made["report"]["ok"] and made["report"]["char_count"] == V.payload_length([t.text for t in made["tags"]])


def test_a_list_with_errors_is_sent_back_once_with_the_problems(db_session, monkeypatch):
    case = make_case(db_session)
    bad = answer(case, tags=[{"text": "Madeleine McCann", "category": "identity", "evidence": []}])
    model = use(monkeypatch, [bad, answer(case)])
    made = asyncio.run(G.TagGenerator().generate(db_session, case, "en"))
    assert len(model.calls) == 2 and "needs evidence" in json.dumps(model.calls[1][1]["problems_to_fix"]) and made["report"]["ok"]


def test_a_list_that_stays_wrong_is_returned_with_its_errors_not_hidden(db_session, monkeypatch):
    case = make_case(db_session)
    use(monkeypatch, [answer(case, tags=[{"text": "Madeleine McCann", "category": "identity", "evidence": []}])])
    made = asyncio.run(G.TagGenerator().generate(db_session, case, "en"))
    assert made["report"]["ok"] is False and "no_evidence" in kinds(made["report"])


@pytest.mark.parametrize("bad", ["not json at all", {"tags": "nope"}, {"case_id": 999, "language": "en", "tags": []}, None])
def test_a_malformed_answer_is_an_error_after_the_repair_round(db_session, monkeypatch, bad):
    case = make_case(db_session)
    use(monkeypatch, [bad])
    with pytest.raises(G.TagError):
        asyncio.run(G.TagGenerator().generate(db_session, case, "en"))


def test_an_answer_for_another_case_or_language_is_asked_again(db_session, monkeypatch):
    case = make_case(db_session)
    model = use(monkeypatch, [answer(case, language="de"), answer(case)])
    asyncio.run(G.TagGenerator().generate(db_session, case, "en"))
    assert "language must be en" in model.calls[1][1]["problems_to_fix"]


def test_a_list_over_five_hundred_characters_is_cut_at_the_end_and_the_cut_is_named(db_session, monkeypatch):
    case = make_case(db_session)
    long = [{"text": f"{c} " * 30 + "x", "category": "genre_terms", "evidence": []} for c in "abcdefgh"]
    use(monkeypatch, [answer(case, tags=long)])
    made = asyncio.run(G.TagGenerator().generate(db_session, case, "en"))
    assert made["report"]["char_count"] <= 500 and any("stay within 500" in f for f in made["fixes"]) and len(made["tags"]) < 8


def test_one_language_with_the_model_down_is_recorded_and_the_others_still_work(db_session, monkeypatch):
    case = make_case(db_session)
    use(monkeypatch, lambda data: TimeoutError("model timed out") if data["language"] == "de" else answer(case, data["language"], tags=[
        {"text": "Madeleine McCann", "category": "identity", "evidence": ["case.title"]}, {"text": "مادلین" if data["language"] in ("fa", "ar") else "Maddie McCann", "category": "genre_terms", "evidence": []}]))
    done = asyncio.run(S.generate(db_session, case, ["en", "de", "fr"]))
    assert done == ["en", "de", "fr"]
    en, de, fr = (S.view(db_session, case, x) for x in ("en", "de", "fr"))
    assert en["status"] == "done" and fr["status"] == "done" and de["status"] == "failed" and "timed out" in de["error"] and de["tags"] == []


def test_the_tags_survive_a_new_session_and_each_language_has_its_own(db_session, monkeypatch):
    case = make_case(db_session)
    use(monkeypatch, lambda data: answer(case, data["language"], tags=[{"text": f"Madeleine McCann {data['language']}", "category": "identity", "evidence": ["case.title"]}]))
    asyncio.run(S.generate(db_session, case, ["en", "es"]))
    db_session.expire_all()
    assert S.view(db_session, case, "en")["text"] == "Madeleine McCann en" and S.view(db_session, case, "es")["text"] == "Madeleine McCann es"


def test_generate_all_leaves_current_languages_alone_and_force_regenerates(db_session, monkeypatch):
    case = make_case(db_session)
    model = use(monkeypatch, lambda data: answer(case, data["language"]))
    asyncio.run(S.generate(db_session, case, ["en", "de"]))
    first = len(model.calls)
    assert asyncio.run(S.generate(db_session, case, ["en", "de"])) == []
    assert len(model.calls) == first
    assert asyncio.run(S.generate(db_session, case, ["en"], force=True)) == ["en"] and len(model.calls) == first + 1


def test_a_regeneration_never_replaces_an_edited_list_it_waits_as_a_candidate(db_session, monkeypatch):
    case = make_case(db_session)
    use(monkeypatch, lambda data: answer(case, data["language"]))
    asyncio.run(S.generate(db_session, case, ["en"]))
    S.save(db_session, case, "en", [{"text": "my own tag", "category": "manual"}])
    asyncio.run(S.generate(db_session, case, ["en"], force=True))
    shown = S.view(db_session, case, "en")
    assert shown["text"] == "my own tag" and shown["origin"] == "edited" and shown["candidate"]["text"].startswith("Madeleine McCann")
    S.use_candidate(db_session, case, "en")
    assert S.view(db_session, case, "en")["text"].startswith("Madeleine McCann") and S.view(db_session, case, "en")["candidate"] is None


def test_an_approved_list_is_not_overwritten_and_editing_it_withdraws_the_approval(db_session, monkeypatch):
    case = make_case(db_session)
    use(monkeypatch, lambda data: answer(case, data["language"]))
    asyncio.run(S.generate(db_session, case, ["en"]))
    S.approve(db_session, case, "en")
    before = S.view(db_session, case, "en")["text"]
    asyncio.run(S.generate(db_session, case, ["en"], force=True))
    shown = S.view(db_session, case, "en")
    assert shown["approval"] == "approved" and shown["text"] == before and shown["candidate"] is not None
    S.drop_candidate(db_session, case, "en")
    S.save(db_session, case, "en", [{"text": "changed"}])
    assert S.view(db_session, case, "en")["approval"] == "pending"


def test_a_list_with_errors_cannot_be_approved(db_session):
    case = make_case(db_session)
    S.save(db_session, case, "en", [{"text": "Madeleine McCann", "category": "identity"}])
    with pytest.raises(S.TagsError, match="needs evidence"):
        S.approve(db_session, case, "en")
    with pytest.raises(S.TagsError):
        S.approve(db_session, make_case(db_session, "Other case"), "en")


def test_new_facts_make_a_list_stale_and_do_not_change_it(db_session, monkeypatch):
    case = make_case(db_session)
    use(monkeypatch, lambda data: answer(case, data["language"]))
    asyncio.run(S.generate(db_session, case, ["en"]))
    assert S.view(db_session, case, "en")["stale"] is False
    db_session.add(Fact(case_id=case.id, claim="A new development in the case.", confidence=0.9))
    db_session.commit()
    shown = S.view(db_session, case, "en")
    assert shown["stale"] is True and shown["text"].startswith("Madeleine McCann")
    assert asyncio.run(S.generate(db_session, case, ["en"])) == ["en"]


def test_an_interrupted_generation_is_shown_and_can_be_started_again(db_session, monkeypatch):
    case = make_case(db_session)
    db_session.add(VideoTagSet(case_id=case.id, language="en", generation_status="running", updated_at=utc_now() - timedelta(seconds=CFG.running_timeout_seconds + 5)))
    db_session.commit()
    assert S.view(db_session, case, "en")["status"] == "interrupted" and S.needs_generation(db_session, case, "en")
    use(monkeypatch, lambda data: answer(case, data["language"]))
    asyncio.run(S.generate(db_session, case, ["en"]))
    assert S.view(db_session, case, "en")["status"] == "done"


def test_a_language_added_to_the_configuration_gets_a_tab_and_tags(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.multilingual, "optional_production_languages", ["nl"])
    monkeypatch.setitem(ai_config.tags_settings().locales, "nl", {"label": "Dutch", "audience": "Netherlands"})
    case = make_case(db_session)
    assert S.languages()[-1] == "nl" and S.view(db_session, case, "nl")["locale"] == "Dutch"
    with pytest.raises(S.TagsError):
        S.ensure_language("xx")


def test_two_cases_have_independent_tags(db_session, monkeypatch):
    a, b = make_case(db_session, "Case A"), make_case(db_session, "Case B")
    S.save(db_session, a, "en", [{"text": "alpha", "category": "genre_terms"}])
    assert S.view(db_session, a, "en")["text"] == "alpha" and S.view(db_session, b, "en")["text"] == ""


# ---------------------------------------------------------------- the routes and the upload


def test_the_routes_generate_edit_check_approve_and_export(db_session, client, monkeypatch):
    case = make_case(db_session)
    use(monkeypatch, lambda data: answer(case, data["language"], tags=[{"text": "Madeleine McCann", "category": "identity", "evidence": ["case.title"]},
                                                                      {"text": "مادلین" if data["language"] in ("fa", "ar") else "Maddie McCann", "category": "genre_terms", "evidence": []}]))
    base = f"/api/cases/{case.id}/tags"
    out = client.post(f"{base}/generate", json={"languages": ["en", "fa"]}).json()
    assert [x["language"] for x in out["languages"]] == S.languages() and out["max_chars"] == 500
    assert {x["language"]: x["status"] for x in out["languages"]}["en"] == "done" and {x["language"]: x["status"] for x in out["languages"]}["de"] == "none"
    checked = client.post(f"{base}/en/check", json={"tags": [{"text": "#x"}, {"text": "y", "category": "nonsense"}]}).json()
    assert checked["ok"] is False and checked["tag_count"] == 2 and "unknown_category" in kinds(checked) and checked["fixes"] == ["cleaned «#x» to «x»"]
    saved = client.put(f"{base}/en", json={"tags": [{"text": "Madeleine McCann", "category": "identity", "evidence": ["case.title"]}, {"text": "Maddie McCann", "category": "identity", "evidence": ["case.aliases"]}]}).json()
    assert saved["text"] == "Madeleine McCann, Maddie McCann" and saved["origin"] == "edited" and saved["report"]["char_count"] == 34
    assert client.post(f"{base}/en/approve").json()["approval"] == "approved"
    exported = client.get(f"{base}/export").json()["languages"]
    assert exported["en"] == {"approved": True, "tags": ["Madeleine McCann", "Maddie McCann"], "text": "Madeleine McCann, Maddie McCann"} and exported["fa"]["approved"] is False
    assert client.post(f"{base}/xx/generate").status_code == 409 and client.get(f"/api/cases/9999/tags").status_code == 404
    assert client.post(f"{base}/de/approve").status_code == 409


def test_the_upload_takes_the_approved_tags_of_the_films_own_language_only(db_session):
    case = make_case(db_session)
    S.save(db_session, case, "en", [{"text": "Madeleine McCann", "category": "identity", "evidence": ["case.title"]}, {"text": "Praia da Luz", "category": "geography_history", "evidence": ["case.location"]}])
    S.approve(db_session, case, "en")
    S.save(db_session, case, "de", [{"text": "Fall Maddie", "category": "genre_terms"}])
    english = Video(case_id=case.id, language="en", title="T", production_type="original", status_at_production="UNSOLVED")
    german = Video(case_id=case.id, language="de", title="T", production_type="original", status_at_production="UNSOLVED")
    assert VID.youtube_metadata(db_session, case, english)["tags"] == ["Madeleine McCann", "Praia da Luz"]
    assert "Fall Maddie" not in VID.youtube_metadata(db_session, case, german)["tags"] and "true crime" in VID.youtube_metadata(db_session, case, german)["tags"]


def test_the_documentary_run_has_a_tags_stage_after_the_names_and_it_can_be_switched_off(monkeypatch):
    from app.documentary.jobs import plan_stages

    monkeypatch.setattr(ai_config.case_naming, "generate_in_pipeline", True)
    monkeypatch.setattr(ai_config.tags_settings(), "generate_in_pipeline", True)
    names = [s["name"] for s in plan_stages(["en", "de"])]
    assert names.index("tags") == names.index("naming") + 1
    monkeypatch.setattr(ai_config.tags_settings(), "generate_in_pipeline", False)
    assert "tags" not in [s["name"] for s in plan_stages(["en", "de"])]
