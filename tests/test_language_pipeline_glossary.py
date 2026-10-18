"""The facts check across all four languages: figures in any digits per evidence item, and every
person and place of a told evidence item in an owner-approved spelling."""

import json

from app.db.models import EntityGlossaryEntry
from app.language_pipeline.glossary import (
    APPROVED,
    Entity,
    Glossary,
    entities_of,
    seed_spellings,
)
from app.language_pipeline.materials import SharedMaterial

KILLING = "Abby Borden was killed on 4 August 1892 with 19 blows in Fall River."
TRIAL = "Lizzie Borden stood trial in New Bedford in June 1893."


def _material():
    facts = [
        {"id": "F001", "claim": KILLING, "people": ["Abby Borden"], "locations": ["Fall River"]},
        {"id": "F002", "claim": TRIAL, "people": ["Lizzie Borden"], "locations": ["New Bedford"]},
    ]
    pack = {"facts": facts, "timeline": [], "contradictions": [], "approved_context": []}
    plan = {"acts": [{"id": "act1", "evidence_ids": ["F001", "F002"], "do_not_reveal": []}]}
    return SharedMaterial(case_id=1, plan_id=1, plan=plan, pack=pack, draft_pack=pack, fingerprint="f")


def _entry(entity, kind, language, spellings, status=APPROVED):
    return EntityGlossaryEntry(case_id=1, entity=entity, kind=kind, language=language,
                               spellings=json.dumps(spellings, ensure_ascii=False),
                               evidence_ids="[]", status=status, origin="owner")


def _glossary(status=APPROVED):
    rows = []
    spelled = {
        "en": {"Abby Borden": ["Abby Borden"], "Lizzie Borden": ["Lizzie Borden"],
               "Fall River": ["Fall River"], "New Bedford": ["New Bedford"]},
        "fa": {"Abby Borden": ["ابی بوردن"], "Lizzie Borden": ["لیزی بوردن"],
               "Fall River": ["فال ریور"], "New Bedford": ["نیو بدفورد"]},
        "ar": {"Abby Borden": ["آبي بوردن"], "Lizzie Borden": ["ليزي بوردن"],
               "Fall River": ["فال ريفر"], "New Bedford": ["نيو بدفورد"]},
    }
    kinds = {"Abby Borden": "people", "Lizzie Borden": "people",
             "Fall River": "locations", "New Bedford": "locations"}
    for language, names in spelled.items():
        rows += [_entry(n, kinds[n], language, s, status) for n, s in names.items()]
    return Glossary(rows)


def test_entities_are_the_proper_names_of_the_evidence_without_their_parts():
    facts = [{"id": "F001", "claim": "x", "people": ["Bridget Sullivan", "Sullivan", "Bridget", "kitchen"],
              "locations": ["Fall River, Massachusetts", "Massachusetts", "barn", "Borden family home",
                            "Bay of Pigs"]}]
    material = SharedMaterial(1, 1, {"acts": []}, {"facts": facts}, {"facts": facts}, "f")
    assert [(e.kind, e.name) for e in entities_of(material)] == [
        ("locations", "Bay of Pigs"), ("locations", "Fall River, Massachusetts"),
        ("people", "Bridget Sullivan")]


def test_seeded_spellings_are_the_evidence_name_and_its_short_forms():
    person = Entity("Abby Durfee Gray", "people", ("F1",))
    place = Entity("Fall River, Massachusetts", "locations", ("F1",))
    assert seed_spellings(person) == ["Abby Durfee Gray", "Abby Gray", "Gray"]
    assert seed_spellings(place) == ["Fall River, Massachusetts", "Fall River"]


class _GlossaryGen:
    def __init__(self):
        self.calls = []

    async def generate_structured(self, role, system, user, images=None, *, prefix=None, cache_key=None):
        from app.providers.generation.base import GenerationResult

        task = json.loads(user)
        self.calls.append((role, task["language"], [e["name"] for e in task["entities"]]))
        entries = [{"entity": e["name"], "spellings": [f"{e['name']}-{task['language']}"]}
                   for e in task["entities"] if e["name"] != "Fall River"]
        return {"entries": entries}, GenerationResult(
            text="{}", model="fake", provider="fake", input_tokens=5, output_tokens=5, total_tokens=10,
            cost_usd=0.001)


def test_the_glossary_is_seeded_proposed_edited_and_approved_per_language(db_session):
    import asyncio
    import uuid

    from app.db.models import Case
    from app.language_pipeline.glossary import GlossaryProposer, GlossaryStore

    case = Case(canonical_title="Glossary Case", slug=f"g-{uuid.uuid4().hex[:8]}", language="en", status="researched")
    db_session.add(case)
    db_session.commit()
    store = GlossaryStore(db_session, case.id)
    entities = entities_of(_material())
    assert store.sync(entities, ["en", "fa"]) == 8
    assert store.sync(entities, ["en", "fa"]) == 0            # nothing doubles, nothing is reset
    glossary = store.load()
    assert glossary.preferred("en")["Abby Borden"] == "Abby Borden"   # from the evidence
    assert glossary.preferred("fa") == {}                              # the agent has to propose

    gen = _GlossaryGen()
    filled = asyncio.run(GlossaryProposer(gen).propose(db_session, case, "fa"))
    assert filled == 3 and [c[:2] for c in gen.calls] == [("evidence_normalizer", "fa")]
    assert store.load().preferred("fa")["Abby Borden"] == "Abby Borden-fa"
    assert not store.load().approved("fa")                              # a proposal is not approval

    store.set_spellings("fa", "Fall River", ["فال ریور", "فال‌ریور"], "owner")
    assert store.approve("fa", "owner") == 4
    assert store.load().approved("fa") and not store.load().approved("en")
    # an owner edit makes that entry a proposal again until it is approved
    store.set_spellings("fa", "Abby Borden", ["ابی بوردن"], "owner")
    assert not store.load().approved("fa")
    # a second proposal call asks only for entries that have no spelling
    assert asyncio.run(GlossaryProposer(gen).propose(db_session, case, "fa")) == 0
    assert len(gen.calls) == 1


