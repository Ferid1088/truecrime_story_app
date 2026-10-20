"""D3: per-case/per-language status rollup and per-Search counts — computed
live from Case/Video/DiscoveryCandidate, never a stored copy."""
from app.db.models import Case, DiscoveryCandidate, ResearchJob, Video
from app.lifecycle.search_status import candidate_status, language_badges, search_counts
from app.services import research_jobs


def _candidate(db, job_id, *, fingerprint, **kw) -> DiscoveryCandidate:
    defaults = {"title": "x", "query": "x", "rationale": "x",
                "state": "suggested", "discovery_job_id": job_id}
    defaults.update(kw)
    c = DiscoveryCandidate(fingerprint=fingerprint, **defaults)
    db.add(c)
    db.commit()
    return c


def _case(db, title) -> Case:
    case = Case(canonical_title=title, slug=title.lower().replace(" ", "-"), language="en")
    db.add(case)
    db.commit()
    return case


def _video(db, case_id, language, state) -> Video:
    v = Video(case_id=case_id, language=language, state=state)
    db.add(v)
    db.commit()
    return v


def _job(db) -> ResearchJob:
    return research_jobs.create_job(
        db, job_type="discovery", input_data={"count": 4, "languages": ["en"]})


def test_candidate_status_free_when_no_case(db_session):
    job = _job(db_session)
    c = _candidate(db_session, job.id, fingerprint="fp-free")
    assert candidate_status(db_session, c) == "free"


def test_candidate_status_created_not_published(db_session):
    job = _job(db_session)
    case = _case(db_session, "Created Not Published Case")
    c = _candidate(db_session, job.id, fingerprint="fp-cnp", case_id=case.id)
    _video(db_session, case.id, "en", "rendered")
    assert candidate_status(db_session, c) == "created_not_published"


def test_candidate_status_created_and_published(db_session):
    job = _job(db_session)
    case = _case(db_session, "Created And Published Case")
    c = _candidate(db_session, job.id, fingerprint="fp-pub", case_id=case.id)
    _video(db_session, case.id, "en", "rendered")
    _video(db_session, case.id, "de", "published")
    assert candidate_status(db_session, c) == "created_published"


def test_candidate_status_archived_candidate_before_any_case(db_session):
    job = _job(db_session)
    c = _candidate(db_session, job.id, fingerprint="fp-arch-cand", state="archived")
    assert candidate_status(db_session, c) == "archived"


def test_candidate_status_archived_case_overrides_published(db_session):
    """A case archived after publication still rolls up to Archived — the
    furthest state, not "created and published"."""
    job = _job(db_session)
    case = _case(db_session, "Archived After Publish Case")
    case.status = "archived"
    db_session.commit()
    c = _candidate(db_session, job.id, fingerprint="fp-arch-case", case_id=case.id)
    _video(db_session, case.id, "en", "published")
    assert candidate_status(db_session, c) == "archived"


def test_language_badges_report_per_language_video_state(db_session):
    case = _case(db_session, "Multi Language Case")
    _video(db_session, case.id, "en", "published")
    _video(db_session, case.id, "de", "rendered")
    badges = language_badges(db_session, case.id)
    assert badges == {"en": "published", "de": "rendered", "fa": "not_created", "ar": "not_created"}


def test_language_badges_all_not_created_when_no_case():
    assert language_badges(None, None) == {
        "en": "not_created", "de": "not_created", "fa": "not_created", "ar": "not_created"}


def test_search_counts_cover_every_state(db_session):
    job = _job(db_session)
    _candidate(db_session, job.id, fingerprint="fp-1")  # free
    case2 = _case(db_session, "Count Case Not Published")
    _candidate(db_session, job.id, fingerprint="fp-2", case_id=case2.id)
    _video(db_session, case2.id, "en", "rendered")
    case3 = _case(db_session, "Count Case Published")
    _candidate(db_session, job.id, fingerprint="fp-3", case_id=case3.id)
    _video(db_session, case3.id, "en", "published")
    _candidate(db_session, job.id, fingerprint="fp-4", state="archived")
    # a filtered (never suggested) candidate must not count toward the total
    _candidate(db_session, job.id, fingerprint="fp-5", state="filtered")

    counts = search_counts(db_session, job.id)
    assert counts == {"total": 4, "free": 1, "created_not_published": 1,
                      "created_published": 1, "archived": 1}


def test_search_counts_reflect_a_real_publish_and_a_real_archive(db_session):
    """The counts are computed live — not a stored snapshot — so a
    publish or an archive immediately changes them without touching the
    DiscoveryCandidate row itself."""
    job = _job(db_session)
    case = _case(db_session, "Lifecycle Case")
    c = _candidate(db_session, job.id, fingerprint="fp-lifecycle", case_id=case.id)
    v = _video(db_session, case.id, "en", "rendered")

    assert search_counts(db_session, job.id)["created_not_published"] == 1
    assert search_counts(db_session, job.id)["created_published"] == 0

    v.state = "published"
    db_session.commit()
    assert search_counts(db_session, job.id)["created_published"] == 1
    assert search_counts(db_session, job.id)["created_not_published"] == 0

    case.status = "archived"
    db_session.commit()
    counts = search_counts(db_session, job.id)
    assert counts["archived"] == 1
    assert counts["created_published"] == 0
    assert candidate_status(db_session, c) == "archived"
