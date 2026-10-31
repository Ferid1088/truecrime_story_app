"""How a language written in its own script spells the case's names: the planner's name rules find a Persian name by the forms read from the narration."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

from app.core.ai_config import ai_config
from app.db.models import Case, EntityNameForm
from app.documentary import names as NS
from app.documentary.mvp import people as PE
from app.documentary.production import picture_rules as PR

FORMS = {"caleb_flynn": {"full": ["کالب فلین"], "first": ["کالب"], "last": ["فلین"]},
         "cunningham_court": {"full": ["کانینگهام کورت"], "first": [], "last": []}}
SENTENCES = [{"start": 0, "end": 10, "display": "کالب فلین از خانه‌اش در کانینگهام کورت با ۹۱۱ تماس گرفت."},
             {"start": 10, "end": 20, "display": "کالبد او هنوز در خانه بود و فلین گریه می‌کرد."}]
ENTITIES = [{"key": "caleb_flynn", "name": "Caleb Flynn", "type": "person"},
            {"key": "cunningham_court", "name": "Cunningham Court", "type": "place"},
            {"key": "ashley_flynn", "name": "Ashley Flynn", "type": "person"}]


def _case(db):
    case = Case(canonical_title="Names", slug=f"names-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    return case


def test_a_persian_name_is_found_by_the_forms_and_a_word_that_only_starts_like_it_is_not():
    names = PR.NameForms(FORMS, ["و"])
    assert names.mentions(SENTENCES[0]["display"], "caleb_flynn")
    assert names.named_by("کالب به اپراتور گفت", "caleb_flynn")
    assert names.named_by("وکالب آمد", "caleb_flynn"), "a prefix joined to the name"
    assert not names.mentions("کالبد او", "caleb_flynn"), "'body' is not the name Caleb"
    assert names.mentions("فلین گفت نه", "caleb_flynn") and not names.named_by("فلین گفت نه", "caleb_flynn"), "a last name alone does not name the person"
    assert PR.NameForms().mentions("Flynn said no.", "caleb_flynn"), "without forms the slug is matched as before"


def test_a_repeat_in_persian_stays_when_the_text_names_its_person_and_goes_when_it_does_not():
    names = PR.NameForms(FORMS)
    shots = [{"kind": "image", "start": 0, "end": 10, "asset_id": "A", "entities": ["caleb_flynn"], "beat_id": "B01"},
             {"kind": "image", "start": 50, "end": 60, "asset_id": "A", "entities": ["caleb_flynn"], "beat_id": "B01"}]
    named = [{"start": 50, "end": 60, "display": "کالب فلین چیزی نگفت."}]
    other = [{"start": 50, "end": 60, "display": "در گاراژ باز بود."}]
    assert PR.drop_unrelated_repeats(list(shots), named, lambda code: set(), names) == []
    assert PR.drop_unrelated_repeats([dict(s) for s in shots], other, lambda code: set(), names)[0]["fix"] == "unrelated_repeat_removed"
    assert PR.drop_unrelated_repeats([dict(s) for s in shots], named, lambda code: set())[0]["fix"] == "unrelated_repeat_removed", "the slug alone is blind to Persian"


def test_a_person_the_persian_text_names_is_shown_at_the_first_mention():
    shots = [{"kind": "image", "start": 0, "end": 60, "asset_id": "X", "entities": ["ashley_flynn"], "beat_id": "B01"}]
    pool = [SimpleNamespace(asset_code="VIS_B")]
    made = []

    def make(target, asset, start, end, why):
        made.append(why)
        return {**target, "asset_id": asset.asset_code, "entities": ["caleb_flynn"], "start": start, "end": end}

    done = PR.cover_people(shots, SENTENCES, {"caleb_flynn": ["VIS_B"]}, lambda beat: pool, make, lambda t: "B01",
                           lambda a: ["caleb_flynn"], names=PR.NameForms(FORMS))
    assert done and done[0]["first_mention"] and made[0] == "the first time the text names this person"


def test_the_agents_forms_are_kept_only_when_the_narration_really_contains_them():
    narration = NS.narration_of(SENTENCES)
    answer = {"entities": [{"key": "caleb_flynn", "full": ["کالب فلین", "کالب فلاینی"], "first": ["کالب"], "last": ["فلین", "x"]},
                           {"key": "unknown_person", "full": ["کالب فلین"]},
                           {"key": "cunningham_court", "full": ["کانینگهام کورت"]}]}
    out = NS.verified(answer, ENTITIES, narration)
    assert out["caleb_flynn"] == {"full": ["کالب فلین"], "first": ["کالب"], "last": ["فلین"]}, "an invented or too short form is dropped"
    assert "unknown_person" not in out and out["cunningham_court"]["full"] == ["کانینگهام کورت"]


def test_the_forms_are_read_once_per_narration_and_a_latin_script_language_reads_none(db_session, monkeypatch):
    case = _case(db_session)
    calls = []

    async def fake_run_agent(name, gen, body, images=None):
        calls.append(json.loads(body)["language"])
        return {"entities": [{"key": "caleb_flynn", "full": ["کالب فلین"], "first": ["کالب"], "last": ["فلین"]}]}, \
            SimpleNamespace(model="m", provider="p", usage=None)

    monkeypatch.setattr(NS, "run_agent", fake_run_agent)
    monkeypatch.setattr(NS, "get_generation_provider", lambda: object())
    monkeypatch.setattr(NS, "stamp_run", lambda *a: None)
    first = asyncio.run(NS.ensure(db_session, case, "fa", SENTENCES, ENTITIES))
    assert first["read"] == 1 and sorted(first["missing"]) == ["ashley_flynn", "cunningham_court"]
    assert asyncio.run(NS.ensure(db_session, case, "fa", SENTENCES, ENTITIES)) == {"reused": True, "read": 3}
    assert calls == ["fa"], "the same narration is not read again"
    assert db_session.query(EntityNameForm).filter_by(case_id=case.id, language="fa").count() == 3
    assert NS.load(db_session, case.id, "fa").mentions("کالب فلین آمد", "caleb_flynn")
    changed = [{**SENTENCES[0], "display": SENTENCES[0]["display"] + " و بعد رفت."}]
    asyncio.run(NS.ensure(db_session, case, "fa", changed, ENTITIES))
    assert calls == ["fa", "fa"], "another narration is read again"
    assert asyncio.run(NS.ensure(db_session, case, "en", SENTENCES, ENTITIES)) == {"skipped": True} and calls == ["fa", "fa"]
    assert not NS.load(db_session, case.id, "en").forms


def test_the_inspector_finds_the_first_mention_of_a_person_in_a_persian_narration():
    persons = [{"key": "caleb_flynn", "name": "Caleb Flynn", "type": "person"}]
    assert PE.first_mentions(SENTENCES, persons, lambda t: "B01") == []
    found = PE.first_mentions(SENTENCES, persons, lambda t: "B01", PR.NameForms(FORMS))
    assert [(m.key, m.start) for m in found] == [("caleb_flynn", 0)]


def test_persian_and_arabic_are_the_native_script_languages():
    assert NS.uses_native_script("fa") and NS.uses_native_script("ar")
    assert not NS.uses_native_script("en") and not NS.uses_native_script("de")
    assert ai_config.names.min_form_chars >= 2
