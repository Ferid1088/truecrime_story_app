"""The titles of the Shorts follow the owner's title standard: the rules and the language's own examples go to the model, a candidate over the limit is dropped, the winner can be written into the Short."""

import asyncio
import json

import pytest
from test_host import _plan_story

from app.covers import text_gate as TG
from app.db.models import ShortClip
from app.publishing import hook_titles as HT
from app.providers.generation.base import GenerationResult


class Titles:
    def __init__(self, data):
        self.data, self.calls = data, []

    async def generate_structured(self, role, system, user, images=None):
        self.calls.append((role, system, json.loads(user)))
        return self.data, GenerationResult(text="{}", model="fake", provider="fake", input_tokens=10, output_tokens=5, total_tokens=15, cost_usd=0.01)


def test_the_standard_gives_the_rules_and_only_the_languages_own_examples():
    fa, de = HT.standard_for("fa"), HT.standard_for("de")
    assert "MANDATORY RULES FOR EVERY LANGUAGE" in fa and "REFERENCE LIBRARY" not in fa.split("================")[0] and "پرونده جنایی: وقتی هوس بوی خون گرفت" in fa
    assert "پرونده جنایی: وقتی هوس بوی خون گرفت" not in de and "FALLSPUR" in de and "FA — " not in de
    assert "PISTAS OCULTAS" in HT.standard_for("pt") and "أثر خفي" in HT.standard_for("ar")
    with pytest.raises(KeyError):
        HT.standard_for("xx")


def test_a_title_is_asked_for_with_the_standard_and_a_too_long_candidate_is_dropped_and_the_winner_can_be_applied(db_session):
    case, master, _ = _plan_story(db_session)
    db_session.add(ShortClip(case_id=case.id, language="fa", clip_key="C1", kind="native", story_version_id=master.id, version=1, status="valid",
                             script_json=json.dumps({"hook": "هوک", "story": "داستان", "avatar": "آواتار", "package": {"title": "قدیمی"}})))
    db_session.commit()
    gen = Titles({"candidates": [{"title": "عنوان اول با یک گره باز", "angle": "a", "why": "w"}, {"title": "x" * 300, "angle": "b", "why": "w"}, {"title": "عنوان سوم", "angle": "c", "why": "w"}], "winner": 0, "backup": 2})
    out = asyncio.run(HT.write(gen, db_session, case, "fa", "C1"))
    assert [c["title"] for c in out["candidates"]] == ["عنوان اول با یک گره باز", "عنوان سوم"] and out["winner"] == 0 and out["backup"] in (0, 1)
    role, system, given = gen.calls[0]
    assert role == "creative_pack" and "MANDATORY RULES" in system and "بوسه‌ای که بوی مرگ می‌داد" in system and given["short"]["hook"] == "هوک" and given["max_characters"] == 100
    TG.edit(db_session, case, "fa", "short_title", "C1", out["candidates"][out["winner"]]["title"])
    assert json.loads(db_session.query(ShortClip).filter_by(case_id=case.id, language="fa", clip_key="C1").one().script_json)["package"]["title"] == "عنوان اول با یک گره باز"
    with pytest.raises(KeyError):
        asyncio.run(HT.write(gen, db_session, case, "fa", "C9"))
    with pytest.raises(ValueError):
        asyncio.run(HT.write(Titles({"candidates": []}), db_session, case, "fa", "C1"))
