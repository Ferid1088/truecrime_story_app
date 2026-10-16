"""Multilingual research -> canonical EN evidence -> master -> localization.

All provider calls are mocked; no live API access.
"""
import asyncio
import json

from app.core.ai_config import ai_config
from app.db.base import SessionLocal
from app.db.models import Case, Fact, ResearchJob, Source, StoryVersion
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
        self.native_score = 90
        self.semantic_score = 100
        self.engagement = 90
        self.grounding = 0.95

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
            "native_language_critic": {
                "overall_native_quality": self.native_score, "problems": []
            },
            "semantic_consistency_checker": {
                "semantic_consistency_score": self.semantic_score,
                "missing_information": [],
                "added_information": [],
                "meaning_changes": [],
                "uncertainty_changes": [],
                "name_date_number_errors": [],
            },
            "localized_grounding_validator": {
                "supported_claims": [], "unsupported_claims": [],
                "uncertainty_errors": [], "grounding_score": self.grounding,
            },
            "localized_engagement_critic": {
                "score": self.engagement, "dimensions": {}, "problems": [],
            },
        }
        return table[role], GenerationResult(text="{}", model="m/j", provider="fake")


def _master(db, case, status="ready"):
    v = StoryVersion(
        case_id=case.id, version=1, kind="master", language="en",
        narrative_angle="{}", story_text="English master story " * 60,
        text_hash="h" * 32, engagement_score=80.0, status=status, is_best=True,
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


# ---------------------------------------------------------------------------
# Part 1/2 — multilingual research
# ---------------------------------------------------------------------------


    assert langs == ["fa", "en", "de", "ar"]
    langs = ai_config.multilingual.research_languages
    assert langs == ["en", "de", "fa", "ar"]
    # The channel writes natively in Persian; en/de/ar are native rewrites
    # from that master, not translations of an English original.
    assert ai_config.multilingual.canonical_language == "fa"
    for lang in ("en", "de", "ar"):
        assert lang in ai_config.multilingual.localization_languages


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
# Parts 6–12 — localization gates
# ---------------------------------------------------------------------------


def _loc_gen(monkeypatch):
    import app.agents.localization as loc_mod
    import app.agents.story as story_mod

    gen = _Gen()
    monkeypatch.setattr(loc_mod, "get_generation_provider", lambda: gen)
    monkeypatch.setattr(story_mod, "get_generation_provider", lambda: gen)
    # StoryPipeline() inside localize() builds its own provider refs via
    # _new_version only (no gen calls) — safe.
    return gen


def test_localization_blocked_when_master_not_ready(db_session, monkeypatch):
    from app.agents.localization import LocalizationPipeline

    _loc_gen(monkeypatch)
    case = _mk_case(db_session)
    master = _master(db_session, case, status="needs_revision")
    try:
        asyncio.run(
            LocalizationPipeline().localize(db_session, case, master, "de")
        )
    except RuntimeError as e:
        assert "ready" in str(e)
    else:
        raise AssertionError("localization ran on a non-ready master")


def test_localization_derives_from_exact_master(db_session, monkeypatch):
    from app.agents.localization import LocalizationPipeline

    gen = _loc_gen(monkeypatch)
    case = _mk_case(db_session)
    master = _master(db_session, case)
    loc = asyncio.run(
        LocalizationPipeline().localize(db_session, case, master, "de")
    )
    assert loc.kind == "localized" and loc.language == "de"
    assert loc.master_version_id == master.id
    assert loc.derived_from_master_version == master.version
    # per-language engagement stored, never inherited
    assert loc.engagement_score == gen.engagement
    # native critic actually ran for the target language
    assert any(r == "native_language_critic" for _, r, _ in gen.calls)
    assert loc.native_quality_score == gen.native_score
    assert loc.semantic_consistency_score == gen.semantic_score


def test_semantic_additions_detected(db_session, monkeypatch):
    from app.agents.localization import LocalizationPipeline

    gen = _loc_gen(monkeypatch)
    case = _mk_case(db_session)
    master = _master(db_session, case)

    orig = LocalizationPipeline._semantic_check

    async def added(self, db, case, m, t, p, lang):
        return {
            "semantic_consistency_score": 95,
            "added_information": [{"text": "new invented detail"}],
            "missing_information": [], "meaning_changes": [],
            "uncertainty_changes": [], "name_date_number_errors": [],
        }

    monkeypatch.setattr(LocalizationPipeline, "_semantic_check", added)
    # repairs exhausted -> still must fail the gate
    gen2 = gen
    loc = asyncio.run(
        LocalizationPipeline().localize(db_session, case, master, "de")
    )
    assert "semantic_consistency" in loc.critic_notes
    assert loc.status == "needs_revision"
    critic = json.loads(loc.critic_notes)
    assert "semantic_consistency" in critic["quality_gates"]["failures"]


def test_date_change_detected(db_session, monkeypatch):
    from app.agents.localization import LocalizationPipeline

    _loc_gen(monkeypatch)
    case = _mk_case(db_session)
    master = _master(db_session, case)

    async def bad_dates(self, db, case, m, t, p, lang):
        return {
            "semantic_consistency_score": 92,
            "added_information": [], "missing_information": [],
            "meaning_changes": [], "uncertainty_changes": [],
            "name_date_number_errors": [{"detail": "1900 -> 1901"}],
        }

    monkeypatch.setattr(
        LocalizationPipeline, "_semantic_check", bad_dates
    )
    loc = asyncio.run(
        LocalizationPipeline().localize(db_session, case, master, "de")
    )
    critic = json.loads(loc.critic_notes)
    assert "semantic_consistency" in critic["quality_gates"]["failures"]


def test_uncertainty_change_detected(db_session, monkeypatch):
    from app.agents.localization import LocalizationPipeline

    _loc_gen(monkeypatch)
    case = _mk_case(db_session)
    master = _master(db_session, case)

    async def strengthened(self, db, case, m, t, p, lang):
        return {
            "semantic_consistency_score": 96,
            "added_information": [], "missing_information": [],
            "meaning_changes": [],
            "uncertainty_changes": [{"detail": "uncertain -> certain"}],
            "name_date_number_errors": [],
        }

    monkeypatch.setattr(LocalizationPipeline, "_semantic_check", strengthened)
    loc = asyncio.run(
        LocalizationPipeline().localize(db_session, case, master, "ar")
    )
    critic = json.loads(loc.critic_notes)
    assert "semantic_consistency" in critic["quality_gates"]["failures"]


def test_low_native_quality_blocks_ready(db_session, monkeypatch):
    from app.agents.localization import LocalizationPipeline

    gen = _loc_gen(monkeypatch)
    gen.native_score = 40
    case = _mk_case(db_session)
    master = _master(db_session, case)
    loc = asyncio.run(
        LocalizationPipeline().localize(db_session, case, master, "de")
    )
    critic = json.loads(loc.critic_notes)
    assert "native_quality" in critic["quality_gates"]["failures"]


def test_per_language_duration_band(db_session):
    from app.agents.localization import LocalizationPipeline

    loc_cfg = ai_config.localization
    pipe = LocalizationPipeline()
    # de: 140 wpm, 45 min -> 6300 target; a 300-word text fails the band
    gates = pipe._evaluate_gates(
        " ".join(["wort"] * 300), "de", 45,
        ai_config.words_per_minute_for("de"),
        {"overall_native_quality": 90},
        {"semantic_consistency_score": 100},
        {"grounding_score": 0.99},
        engagement=None,
    )
    assert "duration_out_of_range" in gates["failures"]
    # a duration-appropriate text passes that gate
    big = " ".join(["wort"] * int(45 * 140 * 0.9))
    gates = pipe._evaluate_gates(
        big, "de", 45, ai_config.words_per_minute_for("de"),
        {"overall_native_quality": 90},
        {"semantic_consistency_score": 100},
        {"grounding_score": 0.99},
        engagement=None,
    )
    assert "duration_out_of_range" not in gates["failures"]


def test_new_master_marks_localizations_outdated(db_session):
    from app.agents.story import StoryPipeline

    case = _mk_case(db_session)
    master = _master(db_session, case)
    loc_v = StoryVersion(
        case_id=case.id, version=2, kind="localized", language="de",
        master_version_id=master.id, derived_from_master_version=1,
        narrative_angle="{}", story_text="alt", status="ready",
        engagement_score=70.0,
    )
    db_session.add(loc_v)
    db_session.commit()

    StoryPipeline()._new_version(
        db_session, case, {}, [{"id": "full", "text": "neu"}],
        "new master text", 75.0, None, "not_evaluated",
        {"quality_gates": {"pass": True}}, "en", kind="master",
    )
    db_session.refresh(loc_v)
    assert loc_v.status == "outdated"


def test_localization_languages_independent_best(db_session):
    from app.agents.story import StoryPipeline

    case = _mk_case(db_session)
    master = _master(db_session, case)
    # de localization at 60 should not dethrone an fa localization at 90
    fa_v = StoryVersion(
        case_id=case.id, version=2, kind="localized", language="fa",
        master_version_id=master.id, derived_from_master_version=1,
        narrative_angle="{}", story_text="fa text", status="ready",
        engagement_score=90.0, is_best=True,
    )
    db_session.add(fa_v)
    db_session.commit()
    row = StoryPipeline()._new_version(
        db_session, case, {}, [{"id": "full", "text": "de"}],
        "de text", 60.0, None, "not_evaluated",
        {"quality_gates": {"pass": True}}, "de",
        kind="localized", master_version_id=master.id,
        derived_from_master_version=1,
    )
    db_session.refresh(fa_v)
    assert row.is_best  # best within de family
    assert fa_v.is_best  # still best within fa family


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
    assert body["canonical_language"] == "fa"
    assert body["languages"]["en"]["sources"] == 1
    assert body["languages"]["de"]["sources"] == 1
    assert body["languages"]["de"]["evidence_items"] == 1
    assert body["languages"]["de"]["unique_evidence"] == 1
    assert list(body["languages"])[0] == "fa"
    assert body["languages"]["en"]["name"] == "English"
    assert body["languages"]["en"]["research_only"] is False
    assert body["languages"]["en"]["enabled"] is True


def test_no_hardcoded_model_ids_or_thresholds():
    import pathlib, re

    app_src = pathlib.Path("app").rglob("*.py")
    banned = re.compile(r"gpt-5|gemini-3|claude-sonnet|openai/|google/|anthropic/")
    for path in app_src:
        hits = [ln for ln in path.read_text().splitlines() if banned.search(ln)]
        assert not hits, f"{path}: hardcoded model/threshold: {hits[:2]}"


def test_evidence_is_normalized_to_english_whatever_the_master_language(db_session, monkeypatch):
    """The master is written in Persian, but evidence stays canonical in
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
    db_session.refresh(sources["en"]); db_session.refresh(sources["fa"])
    assert sources["en"].summary_en is None
    assert sources["fa"].summary_en


def test_language_catalog_has_native_stems_for_every_ready_to_enable_language():
    """Part 3(b): ja, zh, fr, es, it are ready to enable by editing
    research_languages only — each already has its Brave code and native
    video-search stems in config."""
    catalog = ai_config.multilingual.language_catalog
    for lang in ("en", "de", "fa", "ar", "ja", "zh", "fr", "es", "it"):
        entry = catalog[lang]
        assert len(entry.video_search_stems) == 3
        assert all("{t}" in stem for stem in entry.video_search_stems)
        assert "{title}" in entry.video_query_stem


def test_configured_languages_must_exist_in_the_catalog():
    import pytest

    from app.core.ai_config import MultilingualConfig

    with pytest.raises(ValueError, match="missing from language_catalog"):
        MultilingualConfig(research_languages=["en", "xx"], canonical_language="en",
                           localization_languages=[],
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


def test_canonical_language_leads_every_configured_language_list():
    m = ai_config.multilingual
    assert m.research_languages[0] == m.canonical_language
    assert m.produced_languages[0] == m.canonical_language
    assert next(iter(m.language_catalog)) == m.canonical_language
