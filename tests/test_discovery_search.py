import asyncio
import json

import numpy as np
import pytest

from app.db.models import DiscoveryCandidate
from app.lifecycle.library_search import search_candidates, words

MARK = "zzsearchtest"


@pytest.fixture
def add(db_session):
    """Creates suggestions marked as this test's own and removes exactly those afterwards."""
    made: list[int] = []

    def make(title, rationale="", state="suggested"):
        c = DiscoveryCandidate(title=f"{title} {MARK}", query=title, rationale=rationale,
                               fingerprint=f"{MARK}|{title}", state=state, people_json=json.dumps([]))
        db_session.add(c)
        db_session.commit()
        made.append(c.id)
        return c

    yield make
    db_session.query(DiscoveryCandidate).filter(DiscoveryCandidate.id.in_(made)).delete(synchronize_session=False)
    db_session.commit()


def found(db, query, embedder=None):
    out = asyncio.run(search_candidates(db, query, embedder))
    return out, [r["title"].removesuffix(f" {MARK}") for r in out["results"] if MARK in r["title"]]


class MeaningEmbedder:
    """Puts 'knife' and 'stab' on one axis and everything else on another."""

    async def embed(self, texts):
        return np.array([[1.0, 0.0] if any(w in t.lower() for w in ("knife", "stab")) else [0.0, 1.0] for t in texts])


def test_words_fold_accents_and_drop_short_words():
    assert words("Café de l'Étoile") == ["cafe", "etoile"]


def test_lexical_search_matches_word_prefixes(db_session, add):
    add("Navjeet", "stabbing outside a house")
    add("Lake", "a drowning")
    out, titles = found(db_session, "stab")
    assert out["mode"] == "lexical"
    assert titles == ["Navjeet"]


def test_semantic_search_finds_a_case_without_the_word(db_session, add):
    add("Navjeet", "knife attack at night")
    add("Lake", "a drowning")
    out, titles = found(db_session, "stabbing", MeaningEmbedder())
    assert out["mode"] == "lexical+semantic"
    assert titles == ["Navjeet"]


def test_deleted_suggestions_are_not_found(db_session, add):
    add("Gone", "stabbing", state="deleted")
    assert found(db_session, "stabbing")[1] == []
