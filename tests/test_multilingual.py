"""Multilingual research -> evidence normalized to English -> the production languages.

All provider calls are mocked; no live API access.
"""
import asyncio
import json

from app.core.ai_config import ai_config
from app.db.base import SessionLocal
from app.db.models import Case, Fact, ResearchJob, Source
from app.providers.generation.base import GenerationResult

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


_slug_counter = [0]


def _mk_case(db, title="Test Case"):
    _slug_counter[0] += 1
    slug = f"tc-{_slug_counter[0]}-{title.lower().replace(' ', '-')}"
    c = Case(canonical_title=title, slug=slug, language="en", status="researched")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


class _Gen:
    """Scripted generation provider recording every role it receives."""

    def __init__(self):
        self.calls = []

    def is_configured(self):
        return True

    async def generate_text(self, role, system, user):
        self.calls.append(("text", role, user))
        return GenerationResult(
            text="lokalisierte Erzählung mit vielen Worten " * 30,
            model="m/w", provider="fake",
        )

    async def generate_structured(self, role, system, user):
        self.calls.append(("json", role, user))
        table = {
            "evidence_normalizer": {
                "summaries": [
                    {"id": i.get("id"), "summary_en": "EN " + (i.get("summary") or "")}
                    for i in json.loads(user).get("items", [])
                ]
            },
        }
        return table[role], GenerationResult(text="{}", model="m/j", provider="fake")


def test_start_case_research_passes_research_languages():
    from app.services import research_jobs

    captured = {}

    class P:
        name = "truecrime"

        def is_configured(self):
            return True

        async def start_case_research(self, case_title, language,
                                      objective=None, research_languages=None,
                                      context=None):
            captured["languages"] = research_languages
            return "ext-1"

    _slug_counter[0] += 1
    db = SessionLocal()
    case_obj = Case(
        canonical_title="X", slug=f"rl-{_slug_counter[0]}", language="en"
    )
    db.add(case_obj)
    db.commit()
    import unittest.mock as m

    with m.patch.object(research_jobs, "get_research_provider", return_value=P()):
        asyncio.run(research_jobs.start_research_job(db, case_obj))
    assert captured["languages"] == ai_config.multilingual.research_languages


def test_ingest_preserves_source_language_and_text(db_session):
    from app.services.research_jobs import _ingest_research

    case = _mk_case(db_session)
    job = ResearchJob(
        case_id=case.id, provider="legacy-provider", job_type="research", status="running"
    )
    db_session.add(job)
    db_session.commit()
    result = {
        "sources": [
            {
                "title": "Der Fall der Leuchtturmwärter",
                "url": "https://beispiel.de/flannan",
                "language": "de",
                "publisher": "Beispiel",
                "summary": "Deutsche Zusammenfassung des Falls.",
            },
            {
                "title": "پرونده فانوس‌دریایی",
                "url": "https://mesal.ir/flannan",
                "language": "fa",
                "summary": "خلاصه فارسی پرونده.",
            },
        ]
    }
    _ingest_research(db_session, job, result)
    srcs = db_session.query(Source).filter(Source.case_id == case.id).all()
    by_lang = {s.language: s for s in srcs}
    assert set(by_lang) == {"de", "fa"}
    assert by_lang["de"].summary.startswith("Deutsche")
    assert by_lang["fa"].summary.startswith("خلاصه")


def test_translated_duplicate_sources_deduped(db_session):
    from app.services.research_jobs import _ingest_research

    case = _mk_case(db_session)
    job = ResearchJob(
        case_id=case.id, provider="legacy-provider", job_type="research", status="running"
    )
    db_session.add(job)
    db_session.commit()
    result = {
        "sources": [
            {
                "title": "The Flannan Isles Mystery",
                "url": "https://news.com/flannan",
                "language": "en",
                "publisher": "BBC",
                "summary": "English.",
            },
            # same canonical URL with tracking noise -> duplicate
            {
                "title": "The Flannan Isles Mystery",
                "url": "https://www.news.com/flannan/?utm_source=x",
                "language": "en",
            },
            # same title+publisher under another URL -> translated duplicate
            {
                "title": "The Flannan Isles Mystery",
                "url": "https://news.com/flannan-de",
                "language": "de",
                "publisher": "BBC",
            },
        ]
    }
    out = _ingest_research(db_session, job, result)
    assert out["sources_added"] == 1
    assert out["duplicates_rejected"] == 2


# ---------------------------------------------------------------------------
# Part 3 — canonical evidence normalization
# ---------------------------------------------------------------------------


def test_evidence_normalizer_sets_summary_en(db_session, monkeypatch):
    from app.services import research_jobs

    case = _mk_case(db_session)
    s = Source(
        case_id=case.id, title="Quelle", url="https://q.de/x",
        source_type="news", language="de",
        summary="Deutscher Originaltext.", status="active",
    )
    db_session.add(s)
    db_session.commit()

    gen = _Gen()
    monkeypatch.setattr(
        research_jobs, "get_generation_provider", lambda: gen
    )
    done = asyncio.run(research_jobs._normalize_sources(db_session, case.id))
    db_session.refresh(s)
    assert done == 1
    assert s.summary_en == "EN Deutscher Originaltext."
    assert s.summary == "Deutscher Originaltext."  # original preserved
    assert any(r == "evidence_normalizer" for _, r, _ in gen.calls)


def test_fact_stores_canonical_and_original(db_session):
    case = _mk_case(db_session)
    f = Fact(
        case_id=case.id, claim="Canonical English claim.",
        original_claim="Kanonsprüchende Aussage.", original_language="de",
        category="context", confidence=0.9,
    )
    db_session.add(f)
    db_session.commit()
    assert f.claim and f.original_claim and f.original_language == "de"





# ---------------------------------------------------------------------------
# Part 21/24 — metrics + config hygiene
# ---------------------------------------------------------------------------


def test_research_languages_endpoint(client, db_session):
    case = _mk_case(db_session)
    en = Source(case_id=case.id, title="EN", url="https://e.com/a",
                source_type="news", language="en", summary="s", status="active")
    de = Source(case_id=case.id, title="DE", url="https://d.de/a",
                source_type="news", language="de", summary="s", status="active")
    db_session.add_all([en, de])
    db_session.flush()
    db_session.add(
        Fact(case_id=case.id, claim="c", source_ids_json=json.dumps([de.id]))
    )
    db_session.commit()
    r = client.get(f"/api/cases/{case.id}/research/languages")
    assert r.status_code == 200
    body = r.json()
    assert body["production_languages"] == ["fa", "en", "de", "ar"]
    assert body["languages"]["en"]["sources"] == 1
    assert body["languages"]["de"]["sources"] == 1
    assert body["languages"]["de"]["evidence_items"] == 1
    assert body["languages"]["de"]["unique_evidence"] == 1
    assert list(body["languages"])[:4] == ai_config.multilingual.research_languages
    assert body["languages"]["en"]["name"] == "English"
    assert body["languages"]["en"]["research_only"] is False
    assert body["languages"]["en"]["enabled"] is True


def test_no_hardcoded_model_ids_or_thresholds():
    import pathlib
    import re

    app_src = pathlib.Path("app").rglob("*.py")
    banned = re.compile(r"gpt-5|gemini-3|claude-sonnet|openai/|google/|anthropic/")
    for path in app_src:
        hits = [ln for ln in path.read_text().splitlines() if banned.search(ln)]
        assert not hits, f"{path}: hardcoded model/threshold: {hits[:2]}"


def test_evidence_is_normalized_to_english_whatever_the_production_language(db_session, monkeypatch):
    """The film is told in Persian, but evidence stays canonical in
    English: an English source needs no summary_en, a Persian one does."""
    from app.services import research_jobs

    assert ai_config.multilingual.evidence_language == "en"
    case = _mk_case(db_session)
    sources = {}
    for lang, text in (("en", "English original."), ("fa", "متن فارسی.")):
        s = Source(case_id=case.id, title=lang, url=f"https://q.example/{lang}",
                   source_type="news", language=lang, summary=text, status="active")
        db_session.add(s)
        sources[lang] = s
    db_session.commit()
    monkeypatch.setattr(research_jobs, "get_generation_provider", lambda: _Gen())
    assert asyncio.run(research_jobs._normalize_sources(db_session, case.id)) == 1
    db_session.refresh(sources["en"])
    db_session.refresh(sources["fa"])
    assert sources["en"].summary_en is None
    assert sources["fa"].summary_en


def test_language_catalog_has_native_stems_for_every_ready_to_enable_language():
    """Part 3(b): ja, zh, fr, es, it are ready to enable by editing
    research_languages only — each already has its Brave code and native
    video-search stems in config."""
    catalog = ai_config.multilingual.language_catalog
    for lang in ("en", "de", "fa", "ar", "ja", "zh", "fr", "es", "it"):
        entry = catalog[lang]
        assert len(entry.video_search_stems) >= 3
        assert all("{t}" in stem for stem in entry.video_search_stems)
        assert "{title}" in entry.video_query_stem


def test_configured_languages_must_exist_in_the_catalog():
    import pytest

    from app.core.ai_config import MultilingualConfig

    with pytest.raises(ValueError, match="missing from language_catalog"):
        MultilingualConfig(research_languages=["en", "xx"], production_languages=["en"],
                           language_catalog={"en": ai_config.multilingual.language_catalog["en"]})


def test_research_only_languages_are_the_research_languages_that_are_not_produced():
    assert ai_config.multilingual.research_only_languages == []
    widened = ai_config.multilingual.model_copy(
        update={"research_languages": [*ai_config.multilingual.research_languages, "ja", "zh"]})
    assert widened.research_only_languages == ["ja", "zh"]


def test_brave_search_lang_comes_from_the_catalog():
    """Checked live against Brave on 2026-10-16: fa and a bare zh answer
    HTTP 422; zh-hans, ja, fr, es, it, ar, de answer 200."""
    m = ai_config.multilingual
    assert m.brave_search_lang("zh") == "zh-hans"
    assert m.brave_search_lang("fa") is None
    assert m.brave_search_lang("de") == "de"
    assert m.brave_search_lang("ja") == "ja"


