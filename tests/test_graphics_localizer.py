"""The text of a placed graphic in the film's language: every kind is reached, and what stays in another script is reported."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.documentary.mvp import evidence as EV
from app.documentary.visuals import generated


def _plan(graphic: dict) -> SimpleNamespace:
    return SimpleNamespace(plan_json=json.dumps({"beats": [{"beat_id": "B01", "shots": [{"graphic": graphic}]}]}))


def test_the_labels_of_a_timeline_card_are_localized_one_by_one(monkeypatch):
    async def translate(db, case_id, items, language, narration_excerpt=""):
        return {k: f"فارسی {v}" for k, v in items.items()}

    monkeypatch.setattr(generated, "localize_texts", translate)
    points = [{"date": {"year": 2026, "month": 2}, "labels": ["He called 911", "Police came"], "fact_id": "T1"},
              {"date": {"year": 2026, "month": 3}, "labels": ["A letter"], "fact_id": "T2"}]
    plan_row = _plan({"type": "timeline", "data": {"points": points}, "language": "en"})
    result = asyncio.run(EV.localize_graphics(SimpleNamespace(commit=lambda: None), SimpleNamespace(id=1), plan_row, "fa"))
    saved = json.loads(plan_row.plan_json)["beats"][0]["shots"][0]["graphic"]
    assert [p["labels"] for p in saved["data"]["points"]] == [["فارسی He called 911", "فارسی Police came"], ["فارسی A letter"]]
    assert saved["language"] == "fa" and result["localized"] == 3


def test_a_text_that_is_still_in_latin_letters_in_a_persian_film_is_reported_not_hidden():
    plan = {"beats": [{"beat_id": "B07", "shots": [{"graphic": {
        "type": "fact_card", "language": "fa",
        "data": {"facts": [{"text": "کالب با ۹۱۱ تماس گرفت.", "fact_id": "a"},
                           {"text": "Caleb Flynn contacted 911.", "fact_id": "b"}]}}}]}]}
    assert EV.left_in_another_script(plan, "fa") == [{"beat": "B07", "kind": "fact_card", "text": "Caleb Flynn contacted 911."}]
    assert EV.left_in_another_script(plan, "de") == [], "a Latin-script language cannot be told apart by script"


def test_a_card_with_a_group_missing_is_read_without_error():
    data = {"confirmed": [{"text": "تأیید شد", "fact_id": "a"}]}
    assert [(h["text"], k) for h, k in EV.collect_texts(data, "recap")] == [("تأیید شد", "text")]
