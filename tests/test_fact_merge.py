"""Cross-language duplicates of one fact are merged into a single, corroborated fact."""

import asyncio

import numpy as np

from app.agents.fact_merge import FactMerger


class FakeEmbedder:
    """The same claim text group gets the same direction; groups are orthogonal."""

    model = "fake-embed"
    total_tokens = 0
    total_cost_usd = 0.0

    def __init__(self, groups):
        self.groups = groups  # claim -> group index

    async def embed(self, texts):
        out = np.zeros((len(texts), 8), dtype=np.float32)
        for i, text in enumerate(texts):
            out[i, self.groups[text]] = 1.0
        return out


def fact(claim, sources, **kw):
    return {"claim": claim, "source_ids": sources, "category": "legal", "confidence": 0.7,
            "disputed": False, "event_date": None, "chunk_ids": [], "people": [],
            "locations": [], **kw}


def merge(facts, groups):
    return asyncio.run(FactMerger(FakeEmbedder(groups), min_similarity=0.9).merge(facts))


def test_same_fact_in_three_languages_becomes_one_corroborated_fact():
    facts = [
        fact("Judge Caverly sentenced both to life plus 99 years", [1], original_language="en",
             confidence=0.8, evidence_strength="strong_secondary", people=["Caverly"]),
        fact("Richter Caverly verurteilte beide zu lebenslang plus 99 Jahren", [2],
             original_language="de", confidence=0.9, evidence_strength="primary",
             original_claim="Richter Caverly verurteilte beide…", chunk_ids=[7],
             locations=["Chicago"]),
        fact("القاضي كافرلي حكم عليهما بالسجن المؤبد و99 عاماً", [3], original_language="ar"),
    ]
    groups = {f["claim"]: 0 for f in facts}
    merged = merge(facts, groups)
    assert len(merged) == 1
    one = merged[0]
    assert one["source_ids"] == [1, 2, 3]
    assert one["original_language"] == "de"            # strongest evidence represents it
    assert one["confidence"] == 0.9
    assert one["chunk_ids"] == [7]
    assert one["people"] == ["Caverly"] and one["locations"] == ["Chicago"]


def test_different_numbers_or_dates_are_not_merged():
    facts = [
        fact("Franks was 14 years old", [1]),
        fact("Franks was 15 years old", [2]),
        fact("The trial opened", [3], event_date="1924-07-21"),
        fact("The trial opened", [4], event_date="1924-08-01"),
    ]
    groups = {"Franks was 14 years old": 0, "Franks was 15 years old": 0, "The trial opened": 1}
    assert len(merge(facts, groups)) == 4


def test_unrelated_facts_and_order_are_kept():
    facts = [fact("A", [1]), fact("B", [2]), fact("A", [3])]
    merged = merge(facts, {"A": 0, "B": 1})
    assert [f["claim"] for f in merged] == ["A", "B"]
    assert merged[0]["source_ids"] == [1, 3]


def test_disputed_if_any_duplicate_is_disputed():
    facts = [fact("A", [1]), fact("A", [2], disputed=True)]
    assert merge(facts, {"A": 0})[0]["disputed"] is True


def test_empty_input_needs_no_embedding():
    assert asyncio.run(FactMerger(None, min_similarity=0.9).merge([])) == []
