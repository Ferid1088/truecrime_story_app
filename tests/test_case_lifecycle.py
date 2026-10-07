"""Case lifecycle: selection (recent + solved + never used), duplicate
protection by identity, solved/unsolved status everywhere, the
unsolved-case monitor (cheap when nothing happened), follow-up
candidates that wait for the user's approval, follow-up openings and
YouTube titles. Acceptance scenarios A–G of
docs/Master_Task_Case_Lifecycle.MD (fakes only, no network)."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

from app.core.ai_config import ai_config
from app.db.models import (
    Case, CaseStatusCheck, CaseStatusHistory, DiscoveryCandidate, DocumentaryJob,
    FollowUpCandidate, MonitorRun, ProductionScript, Source, StoryVersion, Video,
)
from app.lifecycle import followups as F
from app.lifecycle import scheduler as S
from app.lifecycle import videos as V
from app.lifecycle.identity import Identity, IdentityIndex, compare, raw_identity, skeleton
from app.lifecycle.monitor import UnsolvedCaseMonitor, detect_signals
from app.lifecycle.selection import (
    gate_solved, ingest_candidates, parse_date, prepare_candidates, recency_score,
)
from app.lifecycle.status import set_resolution
from app.research_engine.fetcher import FetchOutcome
from app.research_engine.search import SearchResult
from app.utils import slugify, utc_now

TODAY = date.today()


def _iso(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeRegistry:
    """search(query) -> results whose title/snippet contain a key."""

    def __init__(self, table: dict[str, list[dict]] | None = None):
        self.table = table or {}
        self.calls: list[tuple] = []

    async def search(self, query, language, limit=10, categories=None, time_range=None):
        self.calls.append((query, language, time_range))
        out = []
        for key, rows in self.table.items():
            if key.lower() in query.lower():
                for i, r in enumerate(rows):
                    out.append(SearchResult(
                        backend="fake", query=query, language=language, rank=i + 1,
                        url=r["url"], title=r.get("title"), snippet=r.get("snippet"),
                        published_at=r.get("published_at")))
        return out[:limit]


class FakeFetcher:
    def __init__(self, pages: dict[str, str] | None = None):
        self.pages = pages or {}
        self.calls: list[str] = []

    async def fetch(self, url):
        self.calls.append(url)
        html = self.pages.get(url, f"<html><body><p>{url}</p></body></html>")
        return FetchOutcome(original_url=url, canonical_url=url, status="ok", status_code=200,
                            content_type="text/html", body=html.encode())


class FakeProvider:
    def __init__(self, registry, fetcher=None):
        self.registry = registry
        self.fetcher = fetcher or FakeFetcher()


class FakeGen:
    """case_status_verifier answers from a table keyed by case title."""

    def __init__(self, verdicts: dict[str, dict] | None = None):
        self.verdicts = verdicts or {}
        self.calls: list[str] = []

    def is_configured(self):
        return True

    async def generate_structured(self, role, system, user, images=None):
        self.calls.append(role)
        data = json.loads(user)
        title = data["case"]["title"]
        v = dict(self.verdicts.get(title) or {"status": "UNKNOWN", "confidence": 0.2})
        if v.get("supporting_urls") == "ALL":
            v["supporting_urls"] = [d["url"] for d in data["documents"]]
        return v, None


def _case(db, title, **kw) -> Case:
    case = Case(canonical_title=title, slug=slugify(title) + f"-{abs(hash(title)) % 99999}",
                language="en", **kw)
    db.add(case)
    db.commit()
    return case


def _covered(db, case: Case, language="en", episode=27) -> Video:
    """A published film about the case (the channel covered it)."""
    master = StoryVersion(case_id=case.id, version=1, kind="master", narrative_angle="{}",
                          language="en", story_text="x",
                          narrative_structure=json.dumps({"title": f"The {case.canonical_title}"}))
    db.add(master)
    db.flush()
    ps = ProductionScript(case_id=case.id, story_version_id=master.id, language=language,
                          mode="full", script_json=json.dumps({"opening_strategy": "last_sighting"}))
    db.add(ps)
    db.flush()
    v = V.register_render(db, None, ps, {"path": "x.mp4", "duration": 3000})
    return V.publish(db, v, episode_number=episode)


# ---------------------------------------------------------------------------
# identity: duplicate detection (B)
# ---------------------------------------------------------------------------


def test_names_match_across_spellings():
    assert skeleton("Müller") == skeleton("Mueller") == skeleton("Muller")
    a = Identity.build("new", None, "The disappearance of Inga Gehricke",
                       people=["Inga Gehricke"], location="Stendal, Germany", dates=["2015"])
    b = Identity.build("case", 1, "Missing in Stendal: the Inga case",
                       people=["Inga Gericke"], location="Stendal, Saxony-Anhalt, Germany",
                       dates=["2015-05-02"])
    v = compare(a, b)
    assert v.duplicate and "same person" in v.reasons[0]


@pytest.mark.parametrize("a, b, dup", [
    # same incident, different titles: names in the title
    ({"title": "Inga Gehricke"}, {"title": "The Mysterious Disappearance of Inga Gehricke"}, True),
    # alias match
    ({"title": "Flannan Isles mystery", "aliases": ["Lighthouse keepers case"]},
     {"title": "The Lighthouse Keepers Case"}, True),
    # shared case-specific URL + shared name
    ({"title": "Murder in Hamelin", "key_people": ["Anna Weber"],
      "source_urls": ["https://news.example.de/2024/anna-weber-mord"]},
     {"title": "Anna W. case", "key_people": ["Anna Weber"],
      "source_urls": ["https://news.example.de/2024/anna-weber-mord?utm_source=x"]}, True),
    # namesake: same name, other place and other decade
    ({"title": "Murder of John Miller", "key_people": ["John Miller"],
      "location": "Leeds, UK", "incident_date": "1988"},
     {"title": "John Miller", "key_people": ["John Miller"], "location": "Perth, Australia",
      "incident_date": "2021"}, False),
    # different people, generic words only in common
    ({"title": "The murder of Anna Schmidt"}, {"title": "The murder of Maria Lopez"}, False),
    # same place + same year but nothing else in common
    ({"title": "Arson in Stendal", "location": "Stendal, Germany", "incident_date": "2019"},
     {"title": "Bank robbery", "location": "Stendal, Germany", "incident_date": "2019"}, False),
])
def test_duplicate_rules(a, b, dup):
    ia, ib = raw_identity(a), raw_identity(b)
    ib.kind, ib.id = "case", 7
    assert compare(ia, ib).duplicate is dup


def test_case_creation_is_guarded(client, db_session):
    r = client.post("/api/cases", json={"canonical_title": "Lifecycle Case Kerstin Bauer",
                                        "people": ["Kerstin Bauer"], "location": "Weimar, Germany",
                                        "resolution_status": "SOLVED"})
    assert r.status_code == 200 and r.json()["resolution_status"] == "SOLVED"
    dup = client.post("/api/cases", json={"canonical_title": "The killing of Kerstin Baur",
                                          "people": ["Kerstin Baur"], "location": "Weimar"})
    assert dup.status_code == 409
    detail = dup.json()["detail"]
    assert detail["matched_id"] == r.json()["id"] and "Kerstin" in detail["reason"]
    # the status has a history from the moment the case exists
    hist = client.get(f"/api/cases/{r.json()['id']}/status").json()["history"]
    assert hist[0]["new_status"] == "SOLVED" and hist[0]["changed_by"] == "user"


# ---------------------------------------------------------------------------
# selection: A + B through the discovery pipeline
# ---------------------------------------------------------------------------


def _raw(title, people, place, incident, latest, status, urls=()):
    return {"title": title, "key_people": people, "location": place, "incident_date": incident,
            "latest_development_date": latest, "resolution_status": status,
            "source_urls": list(urls), "rationale": f"{title} fits"}


def test_recency_and_dates():
    assert parse_date("5 March 2024") == date(2024, 3, 5)
    assert parse_date("2023-11") == date(2023, 11, 15)
    assert parse_date("im Mai 2022") == date(2022, 5, 15)
    assert recency_score(TODAY) == 1.0
    assert 0.45 < recency_score(TODAY - timedelta(days=365)) < 0.55


def test_recent_solved_unused_case_is_suggested_first(db_session):
    """A: recent + solved + never used ranks first; an older solved case
    ranks below; an unsolved case is filtered; a case already in the
    system under another title is rejected as duplicate (B)."""
    existing = _case(db_session, "The Lisa Vogt murder", people_json=json.dumps(["Lisa Vogt"]),
                     location="Erfurt, Germany", incident_date="2023")
    raws = [
        _raw("Old solved case of Peter Hahn", ["Peter Hahn"], "Kassel, Germany", "2012",
             "2014-06", "SOLVED"),
        _raw("Murder of Jana Kluge", ["Jana Kluge"], "Gera, Germany", _iso(200), _iso(20),
             "SOLVED"),
        _raw("Unsolved: the Tom Brandt case", ["Tom Brandt"], "Jena, Germany", _iso(100), None,
             "UNSOLVED"),
        _raw("Erfurt woman found dead — Lisa Fogt", ["Lisa Fogt"], "Erfurt, Germany", "2023",
             _iso(30), "SOLVED"),
    ]
    found = [{"url": f"https://news{i}.example/{r['title'].split()[-1]}", "title": r["title"],
              "snippet": f"{r['key_people'][0]} convicted"} for i, r in enumerate(raws)]
    reg = FakeRegistry({"Jana Kluge": [
        {"url": "https://mdr.example.de/jana-kluge-urteil", "title": "Jana Kluge: Urteil",
         "snippet": "Jana Kluge killer sentenced to life"},
        {"url": "https://tagesschau.example/jana-kluge", "title": "Jana Kluge verdict",
         "snippet": "court convicted the killer of Jana Kluge"}],
        "Peter Hahn": [{"url": "https://hna.example/peter-hahn", "title": "Peter Hahn",
                        "snippet": "Peter Hahn murderer convicted in 2014"}]})
    gen = FakeGen({
        "Murder of Jana Kluge": {"status": "SOLVED", "confidence": 0.92,
                                 "supporting_urls": "ALL", "latest_development": "convicted",
                                 "latest_development_date": _iso(20)},
        "Old solved case of Peter Hahn": {"status": "SOLVED", "confidence": 0.9,
                                          "supporting_urls": "ALL",
                                          "latest_development": "convicted 2014"},
    })
    prepared = asyncio.run(prepare_candidates(
        raws, found, IdentityIndex.from_db(db_session), count=3, include_unsolved=False,
        registry=reg, gen=gen))
    out = ingest_candidates(db_session, prepared, count=3, include_unsolved=False)
    titles = [c["title"] for c in out["candidates"]]
    assert titles[0] == "Murder of Jana Kluge"
    assert out["candidates"][0]["resolution_status"] == "SOLVED"
    assert "recent" in out["candidates"][0]["suggestion_reason"]
    assert "not used before" in out["candidates"][0]["suggestion_reason"]
    assert "Unsolved: the Tom Brandt case" not in titles
    rejected = {c["title"]: c for c in out["rejected"]}
    assert rejected["Unsolved: the Tom Brandt case"]["state"] == "filtered"
    dup = rejected["Erfurt woman found dead — Lisa Fogt"]
    assert dup["state"] == "duplicate" and dup["duplicate_of_case_id"] == existing.id
    assert "Lisa Vogt" in dup["duplicate_reason"]
    # B: the next discovery run never suggests the same case again
    again = asyncio.run(prepare_candidates(
        [_raw("Jana K. — the Gera trial", ["Jana Kluge"], "Gera", _iso(200), _iso(10),
              "SOLVED")], [], IdentityIndex.from_db(db_session), count=3,
        include_unsolved=False))
    assert again["candidates"][0]["selection"]["state"] == "duplicate"


def test_unverified_solved_claim_is_not_solved():
    v = {"status": "SOLVED", "confidence": 0.95, "supporting_urls": ["https://a.example/x"]}
    status, why = gate_solved(v, 0.8, 2)
    assert status == "STATUS_UNDER_REVIEW" and "independent" in why
    v["supporting_urls"].append("https://polizei.example.de/presse/1")
    assert gate_solved(v, 0.8, 2)[0] == "SOLVED"


def test_discovery_job_uses_recent_queries_and_identity_index(db_session):
    from app.research_engine.orchestrator import EngineConfig, ResearchOrchestrator

    reg = FakeRegistry({"verdict": [{"url": "https://x.example/a", "title": "Verdict in Gera",
                                     "snippet": "Jana", "published_at": _iso(3)}]})

    class Gen(FakeGen):
        async def generate_structured(self, role, system, user, images=None):
            if role == "case_discovery_agent":
                assert '"published_at"' in user
                return {"candidates": [_raw("Brand new Nora Lind case", ["Nora Lind"], "Ulm",
                                            _iso(90), _iso(5), "SOLVED")]}, None
            return await super().generate_structured(role, system, user)

    orch = ResearchOrchestrator(registry=reg, fetcher=FakeFetcher(), generation_provider=Gen(),
                                config=EngineConfig())
    result = asyncio.run(orch.discover_cases(2, ["en"], None, [], known_identities=[]))
    assert all(tr == ai_config.case_selection.discovery_time_range
               for _, _, tr in reg.calls[:3])
    assert result["candidates"][0]["selection"]["state"] in ("suggested", "eligible")
    assert result["selection_stats"]["checked_against"] == {"cases": 0, "suggestions": 0}


# ---------------------------------------------------------------------------
# C: unsolved labelling — API, archive, YouTube title
# ---------------------------------------------------------------------------


def test_unsolved_case_is_labelled_everywhere(client, db_session):
    case = _case(db_session, "Lifecycle unsolved Rita Haas", location="Celle, Germany")
    set_resolution(db_session, case, "UNSOLVED", changed_by="user", reason="cold case")
    film = _covered(db_session, case)
    assert film.youtube_title.startswith("UNSOLVED: ")
    assert "Status: UNSOLVED" in film.youtube_description
    assert film.status_at_publication == "UNSOLVED"
    listed = client.get("/api/cases", params={"resolution": "UNSOLVED"}).json()
    assert any(c["id"] == case.id and c["resolution_status"] == "UNSOLVED" for c in listed)
    arch = client.get("/api/archive", params={"status": "UNSOLVED"}).json()
    assert any(c["id"] == case.id for c in arch["cases"])
    assert all(c["resolution_status"] == "UNSOLVED" for c in arch["cases"])
    solved = client.get("/api/archive", params={"status": "SOLVED"}).json()
    assert not any(c["id"] == case.id for c in solved["cases"])
    de, rule = V.youtube_title(case, "de", "Rita Haas")
    assert de.startswith("UNGEKLÄRT:") and rule == "unsolved"


# ---------------------------------------------------------------------------
# D + E: the monitor
# ---------------------------------------------------------------------------


def test_monitor_without_news_stays_cheap(db_session):
    """D: no signal -> search calls only, no fetch, no LLM."""
    case = _case(db_session, "Lifecycle quiet case Udo Lenz", people_json='["Udo Lenz"]',
                 location="Bremen, Germany")
    set_resolution(db_session, case, "UNSOLVED", changed_by="user", reason="cold")
    reg = FakeRegistry({"Udo Lenz": [{"url": "https://n.example/1", "title": "Udo Lenz remembered",
                                      "snippet": "Ten years since Udo Lenz vanished in Bremen",
                                      "published_at": _iso(1)}]})
    fetcher, gen = FakeFetcher(), FakeGen()
    check = asyncio.run(UnsolvedCaseMonitor(FakeProvider(reg, fetcher), gen).check_case(
        db_session, case))
    assert check.outcome == "no_signal" and check.stage == "fast"
    assert check.search_calls >= 1 and check.fetch_calls == 0 and check.llm_calls == 0
    assert fetcher.calls == [] and gen.calls == []
    assert db_session.get(Case, case.id).resolution_status == "UNSOLVED"


def test_signal_words_need_the_case_name():
    names = {"title": "Udo Lenz case", "people": ["Udo Lenz"], "places": ["bremen"],
             "aliases": []}
    hits = detect_signals([
        {"url": "u1", "title": "Man arrested in Bremen", "snippet": "police arrested a man"},
        {"url": "u2", "title": "Udo Lenz: suspect arrested", "snippet": "arrested on Monday"},
    ], names, ["en"])
    assert [h["url"] for h in hits] == ["u2"] and hits[0]["signal"] == "arrest"


def test_unsolved_case_becomes_solved_and_waits_for_approval(client, db_session, monkeypatch):
    """E + F: signal -> deep verification with independent sources ->
    SOLVED with history -> follow-up candidate on the dashboard; no job
    until the user approves; approval creates exactly one linked job."""
    case = _case(db_session, "Lifecycle Mia Roth disappearance", people_json='["Mia Roth"]',
                 location="Passau, Germany")
    set_resolution(db_session, case, "UNSOLVED", changed_by="user", reason="open")
    original = _covered(db_session, case, episode=27)
    reg = FakeRegistry({"Mia Roth": [
        {"url": "https://br.example.de/mia-roth-urteil", "title": "Mia Roth: Täter verurteilt",
         "snippet": "Der Mörder von Mia Roth wurde verurteilt", "published_at": _iso(2)},
        {"url": "https://polizei.example.de/presse/mia-roth", "title": "Mia Roth case solved",
         "snippet": "Police: Mia Roth case solved, man convicted", "published_at": _iso(3)}]})
    gen = FakeGen({case.canonical_title: {
        "status": "SOLVED", "confidence": 0.93, "supporting_urls": "ALL",
        "latest_development": "A 41-year-old man was convicted of murdering Mia Roth.",
        "latest_development_date": _iso(2), "key_facts": [{"fact": "conviction", "url": "x"}]}})
    monitor = UnsolvedCaseMonitor(FakeProvider(reg), gen)
    run = asyncio.run(monitor.run(db_session, trigger="manual"))
    check = (db_session.query(CaseStatusCheck).filter(CaseStatusCheck.case_id == case.id)
             .order_by(CaseStatusCheck.id.desc()).first())
    assert check.stage == "deep" and check.outcome == "confirmed_change"
    assert check.previous_status == "UNSOLVED" and check.current_status == "SOLVED"
    assert check.llm_calls == 1 and check.fetch_calls >= 2
    assert run.status_changes >= 1 and run.follow_ups_created >= 1
    db_session.refresh(case)
    assert case.resolution_status == "SOLVED"
    hist = db_session.query(CaseStatusHistory).filter(
        CaseStatusHistory.case_id == case.id).order_by(CaseStatusHistory.id).all()
    assert [h.new_status for h in hist] == ["UNSOLVED", "SOLVED"] and hist[-1].changed_by == "monitor"
    assert json.loads(hist[-1].sources_json)

    # the dashboard asks; nothing is produced yet
    dash = client.get("/api/dashboard").json()
    fu = next(f for f in dash["follow_up_candidates"] if f["case_id"] == case.id)
    assert fu["original_video"]["id"] == original.id and fu["original_video"]["episode_number"] == 27
    assert fu["previous_status"] == "UNSOLVED" and fu["new_status"] == "SOLVED"
    assert "Do you want to create an update video?" in fu["question"]
    assert db_session.query(DocumentaryJob).filter(DocumentaryJob.case_id == case.id).count() == 0

    launched = []
    monkeypatch.setattr("app.documentary.jobs.launch", lambda job_id: launched.append(job_id))
    r = client.post(f"/api/follow-ups/{fu['id']}/approve", json={"mode": "pilot",
                                                                 "languages": ["en"]})
    assert r.status_code == 200
    job = db_session.get(DocumentaryJob, r.json()["job"]["id"])
    assert job.production_type == "follow_up" and job.follow_up_id == fu["id"]
    assert launched == [job.id]
    assert client.post(f"/api/follow-ups/{fu['id']}/approve", json={}).status_code == 409
    db_session.expire_all()
    assert db_session.query(DocumentaryJob).filter(DocumentaryJob.case_id == case.id).count() == 1


def test_monitor_skips_solved_and_never_covered_cases_get_no_follow_up(db_session):
    case = _case(db_session, "Lifecycle Ben Kraus case", people_json='["Ben Kraus"]')
    set_resolution(db_session, case, "UNSOLVED", changed_by="user", reason="open")
    reg = FakeRegistry({"Ben Kraus": [
        {"url": "https://a.example/ben", "title": "Ben Kraus killer convicted",
         "snippet": "Ben Kraus killer convicted", "published_at": _iso(1)},
        {"url": "https://b.example/ben", "title": "Ben Kraus verdict",
         "snippet": "found guilty of killing Ben Kraus", "published_at": _iso(1)}]})
    gen = FakeGen({case.canonical_title: {"status": "SOLVED", "confidence": 0.9,
                                          "supporting_urls": "ALL"}})
    asyncio.run(UnsolvedCaseMonitor(FakeProvider(reg), gen).check_case(db_session, case))
    db_session.refresh(case)
    assert case.resolution_status == "SOLVED"
    # solved now, but never covered -> no follow-up question
    assert not db_session.query(FollowUpCandidate).filter(
        FollowUpCandidate.case_id == case.id).first()
    assert case not in UnsolvedCaseMonitor().due_cases(db_session)


def test_scheduler_runs_about_twice_a_week(db_session, monkeypatch):
    runs = []

    class Mon:
        async def run(self, db, trigger="scheduled"):
            row = MonitorRun(trigger=trigger, status="completed", started_at=utc_now(),
                             finished_at=utc_now())
            db.add(row)
            db.commit()
            runs.append(row.id)
            return row

    for r in db_session.query(MonitorRun).all():
        r.started_at = utc_now() - timedelta(days=30)
    db_session.commit()
    assert asyncio.run(S.tick(monitor=Mon())) is not None
    assert asyncio.run(S.tick(monitor=Mon())) is None          # not due again yet
    last = db_session.query(MonitorRun).order_by(MonitorRun.id.desc()).first()
    last.started_at = utc_now() - timedelta(hours=ai_config.case_monitor.interval_hours + 1)
    db_session.commit()
    assert asyncio.run(S.tick(monitor=Mon())) is not None
    assert len(runs) == 2


# ---------------------------------------------------------------------------
# G: the follow-up story opens with the earlier coverage; titles
# ---------------------------------------------------------------------------


def test_follow_up_intro_and_title(db_session):
    case = _case(db_session, "The disappearance of Lena Sommer", people_json='["Lena Sommer"]')
    set_resolution(db_session, case, "UNSOLVED", changed_by="user", reason="open")
    original = _covered(db_session, case, episode=12)
    set_resolution(db_session, case, "SOLVED", changed_by="monitor", reason="convicted")
    fu = F.create_candidate(db_session, case, previous_status="UNSOLVED", new_status="SOLVED",
                            check=None, development="A neighbour was convicted.",
                            sources=[], confidence=0.9)
    ctx = F.context(db_session, fu)
    assert "Episode 12" in ctx["intro"] and original.youtube_title in ctx["intro"]
    assert "has now been solved" in ctx["intro"]
    text, inserted = F.ensure_intro("Lena Sommer left home on a Friday.", ctx)
    assert inserted and text.startswith(ctx["intro"])
    already = ctx["intro"] + "\n\nShe left home on a Friday."
    assert F.ensure_intro(already, ctx) == (already, False)
    title, rule = V.youtube_title(case, "en", "anything", production_type="follow_up")
    assert rule == "follow_up" and title.startswith("SOLVED: The Lena Sommer Case")
    assert "Original Video" in title


def test_story_director_gets_status_openings_and_follow_up(db_session):
    """The director prompt carries the opening catalogue, the openings
    of recent films to avoid, the case status and the follow-up intro;
    the master stores the opening and keeps the intro."""
    from app.agents.story import choose_opening, editorial_context

    prev = _case(db_session, "Lifecycle Opening previous film")
    db_session.add(StoryVersion(case_id=prev.id, version=1, kind="master", narrative_angle="{}",
                                language="en", story_text="x", narrative_structure=json.dumps(
                                    {"opening_strategy": "courtroom_outcome"})))
    db_session.commit()
    case = _case(db_session, "Lifecycle Opening case")
    set_resolution(db_session, case, "SOLVED", changed_by="user", reason="x")
    ctx = editorial_context(db_session, case)
    assert ctx["case_status"] == "SOLVED" and "courtroom_outcome" in ctx["avoid_openings"]
    assert "victim_introduction" in ctx["opening_strategies"]
    # unknown choice -> deterministic fallback that avoids the recent opening
    strategy, why = choose_opening({"opening_strategy": "dramatic"}, ctx)
    assert strategy != "courtroom_outcome" and "fallback" in why
    assert choose_opening({"opening_strategy": "Last sighting"}, ctx)[0] == "last_sighting"
    fu_ctx = {**ctx, "follow_up": {"intro": "We first told this story..."}}
    assert choose_opening({"opening_strategy": "contradiction"}, fu_ctx)[0] == "previous_coverage"


def test_audit_explains_decisions(client, db_session):
    case = _case(db_session, "Lifecycle audit case Eva Lorenz")
    set_resolution(db_session, case, "UNSOLVED", changed_by="user", reason="cold case file")
    data = client.get(f"/api/cases/{case.id}/audit").json()
    assert data["case"]["resolution_status"] == "UNSOLVED"
    assert data["status_history"][0]["reason"] == "cold case file"
    assert {"suggestions", "status_checks", "productions", "films", "follow_ups"} <= set(data)


def test_follow_up_master_opens_with_the_earlier_video(db_session, monkeypatch):
    """G: the master story of an update video references the previous
    video and says the case is now solved — even when the writer forgot."""
    from test_master_pipeline import ScriptGen, _add_fact, _mk_case, _pipeline

    fake = ScriptGen()
    seen = {}

    def director(system, user):
        seen["context"] = json.loads(user)["editorial_context"]
        return {"title": "t", "central_question": "q", "opening_strategy": "contradiction",
                "acts": [{"id": "act1", "purpose": "p", "evidence_ids": []},
                         {"id": "act2", "purpose": "p", "evidence_ids": []}]}

    fake.structured["story_director"] = director
    pipe = _pipeline(fake, monkeypatch)
    case = _mk_case(db_session)
    _add_fact(db_session, case)
    set_resolution(db_session, case, "UNSOLVED", changed_by="user", reason="open")
    _covered(db_session, case, episode=31)
    set_resolution(db_session, case, "SOLVED", changed_by="monitor", reason="verdict")
    fu = F.create_candidate(db_session, case, previous_status="UNSOLVED", new_status="SOLVED",
                            check=None, development="The killer confessed and was convicted.",
                            sources=[], confidence=0.9)
    ctx = F.context(db_session, fu)
    v = asyncio.run(pipe.run(db_session, case, target_minutes=10, language="en", tone="t",
                             iterations=1, follow_up=ctx))
    assert seen["context"]["follow_up"]["intro"] == ctx["intro"]
    assert seen["context"]["case_status"] == "SOLVED"
    assert v.story_text.startswith(ctx["intro"])
    assert "Episode 31" in v.story_text.split("\n\n")[0]
    struct = json.loads(v.narrative_structure)
    assert struct["opening_strategy"] == "previous_coverage"
    assert struct["follow_up"]["follow_up_id"] == fu.id
    assert struct["sections"][0]["text"].startswith(ctx["intro"])


def test_editing_a_published_film_keeps_its_history(db_session):
    case = _case(db_session, "Lifecycle publish history Ida Brink")
    set_resolution(db_session, case, "UNSOLVED", changed_by="user", reason="open")
    film = _covered(db_session, case, episode=5)
    first_title, first_date = film.youtube_title, film.published_at
    set_resolution(db_session, case, "SOLVED", changed_by="monitor", reason="verdict")
    film = V.publish(db_session, film, youtube_url="https://youtube.example/watch?v=1")
    assert film.status_at_publication == "UNSOLVED" and film.youtube_title == first_title
    assert film.published_at == first_date and film.youtube_url.endswith("v=1")


def test_a_new_case_beats_an_old_case_with_a_new_verdict():
    """Recency is about the case: a 2025 killing convicted this year ranks
    above a 1996 killing convicted this year."""
    from app.lifecycle.selection import case_recency

    recent, _ = case_recency(TODAY - timedelta(days=300), TODAY - timedelta(days=20))
    old, how = case_recency(date(1996, 9, 13), TODAY - timedelta(days=20))
    assert recent > old and "incident 1996" in how
    only_dev, how2 = case_recency(None, TODAY - timedelta(days=20))
    assert old < only_dev < 1.0 and "unknown" in how2
