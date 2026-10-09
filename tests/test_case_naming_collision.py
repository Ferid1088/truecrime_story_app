"""Collision corpus + exact / near / semantic checks (no network)."""

import asyncio
import json

import numpy as np
import pytest

from app.db.models import (Case, CaseTitleCandidate, DiscoveryCandidate, EpisodeIdentity,
                           Source, Video)
from app.naming import collision as C
from app.naming.corpus import get_corpus
from app.naming.normalize import norm_title


def _case(db, title, **kw):
    case = Case(canonical_title=title, slug=title.lower().replace(" ", "-") + "-nc", **kw)
    db.add(case)
    db.commit()
    return case


def test_normalization_variants():
    assert norm_title("The  Vanishing, Circle!") == norm_title("the vanishing circle")
    assert norm_title("Gelöst: Der Fall") == norm_title("geloest der fall".replace("oe", "o"))
    assert norm_title("Straße") == norm_title("STRASSE")
    # Persian half-space, Arabic yeh/kaf variants, diacritics, tatweel
    assert norm_title("حل‌نشده") == norm_title("حل نشده")
    assert norm_title("كتاب") == norm_title("کتاب")
    assert norm_title("عَـربي") == norm_title("عربی")
    assert norm_title("‏رد خاموش‎") == norm_title("رد خاموش")


def test_exact_collision_with_internal_archived_and_production(db_session):
    a = _case(db_session, "The Hollow Mile", status="archived")
    b = _case(db_session, "Quiet Harbor Night", status="producing")
    me = _case(db_session, "Collision subject")
    corpus = get_corpus(db_session)
    for t in ("the hollow mile", "THE HOLLOW MILE!", "Quiet  Harbor Night"):
        r = C.check_lexical(t, "en", me.id, corpus)
        assert r.exact and r.rejected, t
    assert not C.check_lexical("A Lantern In Celle", "en", me.id, corpus).rejected
    assert a.id and b.id


def test_own_family_is_not_a_collision_but_own_research_title_is(db_session):
    me = _case(db_session, "Family subject")
    db_session.add(EpisodeIdentity(case_id=me.id, language="de", channel_id="de",
                                   editorial_title="Der verschwundene Kreis"))
    db_session.add(Source(case_id=me.id, title="Der leere Hof", url="http://x/1",
                          source_type="article", language="de"))
    db_session.commit()
    corpus = get_corpus(db_session)
    assert not C.check_lexical("Der verschwundene Kreis", "de", me.id, corpus).rejected
    assert C.check_lexical("Der leere Hof", "de", me.id, corpus).exact


def test_corpus_rebuilds_when_titles_change(db_session):
    me = _case(db_session, "Cache subject")
    assert not C.check_lexical("Rebuilt Title Zed", "en", me.id, get_corpus(db_session)).rejected
    db_session.add(DiscoveryCandidate(title="Rebuilt Title Zed", query="q", rationale="r", fingerprint="fp-rebuilt"))
    db_session.commit()
    assert C.check_lexical("Rebuilt Title Zed", "en", me.id, get_corpus(db_session)).exact


def test_rejected_history_blocks_regeneration(db_session):
    me = _case(db_session, "History subject")
    db_session.add(CaseTitleCandidate(case_id=me.id, language="en", title="The Empty Road",
                                      title_norm=norm_title("The Empty Road"), status="rejected",
                                      rejection_reason="generic"))
    db_session.commit()
    r = C.check_lexical("the empty road", "en", me.id, get_corpus(db_session))
    assert r.exact and "previously rejected" in r.reasons[0]


def test_discovered_article_and_youtube_titles_collide(db_session):
    me = _case(db_session, "Research subject")
    other = _case(db_session, "Research other")
    db_session.add_all([
        Source(case_id=other.id, title="The Silent Orchard", url="http://x/a",
               source_type="article", language="en"),
        Source(case_id=other.id, title="The Silent Orchard - full documentary",
               url="http://x/b", source_type="youtube_video", language="en")])
    db_session.commit()
    corpus = get_corpus(db_session)
    assert C.check_lexical("The Silent Orchard", "en", me.id, corpus).exact
    near = C.check_lexical("Silent Orchard Documentary", "en", me.id, corpus)
    assert near.near_score >= 80
    # a 3+ word phrase inside a longer headline counts
    hit = C.check_lexical("The Silent Orchard", "en", me.id, corpus)
    assert hit.rejected


def test_near_collision_threshold(db_session):
    me = _case(db_session, "Near subject")
    _case(db_session, "The Vanishing Circle")
    corpus = get_corpus(db_session)
    r = C.check_lexical("Vanishing Circle The", "en", me.id, corpus)
    assert r.rejected and r.near_score >= 88
    assert not C.check_lexical("A Lantern In Celle", "en", me.id, corpus).rejected


class FakeEmbedder:
    """'Empty house' in two languages share a vector; others are orthogonal."""

    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        out = np.zeros((len(texts), 4), dtype=np.float32)
        for i, t in enumerate(texts):
            n = norm_title(t)
            out[i, 0 if n in ("the empty house", "das leere haus") else
                1 if n == "a lantern in celle" else 2] = 1.0
        return out


def test_multilingual_semantic_duplicate_detected_own_family_allowed(db_session):
    me = _case(db_session, "Semantic subject")
    other = _case(db_session, "The Empty House")
    db_session.add(EpisodeIdentity(case_id=me.id, language="en", channel_id="en",
                                   editorial_title="A Lantern In Celle"))
    db_session.commit()
    corpus = get_corpus(db_session)
    emb = FakeEmbedder()
    titles = {"de1": "Das leere Haus", "de2": "Eine Laterne in Celle"}
    results = {k: C.check_lexical(v, "de", me.id, corpus) for k, v in titles.items()}
    asyncio.run(C.add_semantic(results, titles, {}, me.id, corpus, emb))
    assert results["de1"].semantic_score >= 0.99 and results["de1"].rejected
    assert "semantic duplicate" in results["de1"].reasons[-1]
    # compared to other cases only: the case's own EN title is never in the pool
    assert "A Lantern In Celle" not in emb.calls[0] + emb.calls[1]
    assert other.id


def test_naming_never_calls_a_search_backend(db_session, monkeypatch):
    import app.research_engine.search as S
    import app.services.search as SS

    def boom(*a, **k):
        raise AssertionError("search backend called during naming")

    for mod in (S, SS):
        for name in dir(mod):
            if name.lower().startswith(("search", "query", "run_search")) and callable(
                    getattr(mod, name)) and not isinstance(getattr(mod, name), type):
                monkeypatch.setattr(mod, name, boom)
    me = _case(db_session, "No search subject")
    corpus = get_corpus(db_session)
    emb = FakeEmbedder()
    results = {"x": C.check_lexical("Anything At All", "en", me.id, corpus)}
    asyncio.run(C.add_semantic(results, {"x": "Anything At All"}, {}, me.id, corpus, emb))
    # the embedder saw only candidate + stored titles
    stored = {e.text for e in corpus.entries}
    assert all(t in stored or t == "Anything At All" for call in emb.calls for t in call)
