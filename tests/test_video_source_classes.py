"""S6b: the source classes a video can be a film candidate from, with the evidence that put it there;
other creators' edited films and re-uploaders stay research only."""

from types import SimpleNamespace

from app.core.ai_config import ai_config
from app.services.video_rights import (
    FILM_CANDIDATE,
    RESEARCH_ONLY,
    RightsRefused,
    VideoRightsGate,
    VideoUseClassifier,
)


def yt(**kw):
    return {"url": "https://www.youtube.com/watch?v=abcdefghijk", "title": "Case X", "channel_name": "Somebody",
            "classification": "unknown", **kw}


def classify(**kw):
    return VideoUseClassifier().classify(yt(**kw))


def test_a_police_department_video_is_an_official_body_clip_with_its_evidence():
    use = classify(classification="official", channel_name="Moscow Police Department",
                   title="Press conference on the King Road case")
    assert use.use == FILM_CANDIDATE and use.source_class == "official_body"
    assert "Moscow Police Department" in use.class_evidence and "police" in use.class_evidence
    assert use.attribution_text.endswith("Moscow Police Department")


def test_an_official_channel_is_recognised_by_its_name_even_when_the_kind_is_unknown():
    use = classify(channel_name="Latah County Sheriff's Office", title="Update")
    assert use.use == FILM_CANDIDATE and use.source_class == "official_body"


def test_an_official_government_or_court_file_on_its_own_host_is_an_official_body_clip():
    use = VideoUseClassifier().classify({"url": "https://www.courts.state.gov/media/hearing.mp4", "title": "Hearing",
                                         "license": ""})
    assert use.use == FILM_CANDIDATE and use.source_class == "official_body"
    assert "courts.state.gov" in use.class_evidence


def test_raw_footage_from_a_news_agency_is_a_film_candidate_but_its_edited_report_is_not():
    raw = classify(classification="news", channel_name="Associated Press", title="RAW VIDEO: suspect arrives at court")
    assert raw.use == FILM_CANDIDATE and raw.source_class == "news_raw"
    assert "raw video" in raw.class_evidence.lower()
    edited = classify(classification="news", channel_name="Associated Press", title="Suspect arrives at court: what we know")
    assert edited.use == RESEARCH_ONLY
    unknown_outlet = classify(classification="news", channel_name="Daily Gossip Network", title="RAW VIDEO of the arrest")
    assert unknown_outlet.use == RESEARCH_ONLY


def test_witness_and_family_videos_are_film_candidates_by_their_own_words():
    for title in ("Eyewitness video of the street that night", "Family home video of the victims", "Doorbell camera footage"):
        use = classify(classification="interview", title=title)
        assert use.use == FILM_CANDIDATE and use.source_class == "witness_family", title
        assert use.class_evidence


def test_a_creative_commons_video_of_a_channel_that_is_not_a_commentator_is_a_film_candidate():
    use = classify(classification="news", channel_name="A Local Station", license="creativeCommon",
                   license_url="https://www.youtube.com/t/creative_commons")
    assert use.use == FILM_CANDIDATE and use.source_class == "creative_commons"
    assert use.license_evidence_url and use.rights_status == "creative_commons"


def test_another_creators_edited_film_is_always_research_only_whatever_its_markers_or_licence():
    for classification in ("documentary", "creator_narration", "analysis"):
        use = classify(classification=classification, channel_name="Dark Files Police Watch",
                       title="RAW eyewitness home video explained", license="creativeCommon",
                       license_url="https://www.youtube.com/t/creative_commons")
        assert use.use == RESEARCH_ONLY and use.source_class is None, classification
        assert "another creator" in use.reason


def test_a_re_uploader_is_research_only_even_with_an_official_looking_name():
    use = classify(classification="official", channel_name="Police Clips Mirror", title="Press conference")
    assert use.use == RESEARCH_ONLY and "re-uploads" in use.reason


def test_a_video_of_no_class_is_research_only_doubt_means_research_only():
    use = classify(classification="unknown", channel_name="Somebody", title="Thoughts on the case")
    assert use.use == RESEARCH_ONLY and use.source_class is None


def test_the_gate_needs_the_class_evidence_or_the_licence_evidence():
    video = SimpleNamespace(use=FILM_CANDIDATE, use_reason="x", rights_status="editorial_review_required",
                            license_evidence_url=None, license_checked_at=None, class_evidence=None,
                            source_class="official_body")
    assert "no stored license evidence" in VideoRightsGate().check(video)
    video.class_evidence = "channel matches the official marker 'police'"
    assert VideoRightsGate().check(video) is None
    video.use = RESEARCH_ONLY
    try:
        VideoRightsGate().require(video)
    except RightsRefused:
        pass
    else:
        raise AssertionError("a research-only video is never fetched")


def test_the_classes_are_configured_and_the_footage_limits_are_raised():
    classes = ai_config.video_rights.source_classes
    assert {"official_body", "news_raw", "witness_family", "creative_commons"} <= set(classes)
    f = ai_config.footage
    assert (f.max_pieces_per_source, f.piece_min_seconds) == (30, 3.0)
    assert (f.max_third_party_clip_seconds, f.max_third_party_share) == (24.0, 0.30)
    assert "youtube_clip" in f.third_party_providers


def test_stored_videos_are_classified_again_under_the_current_classes(db_session):
    import uuid

    from app.db.models import Case, VideoSource
    from app.services.video_rights import reclassify_case

    case = Case(canonical_title="Reclass", slug=uuid.uuid4().hex, language="en")
    db_session.add(case)
    db_session.commit()
    rows = [("a", "Moscow Police Department", "official", "Press conference"),
            ("b", "Dark Files", "creator_narration", "RAW eyewitness video explained"),
            ("c", "Someone", "unknown", "My thoughts")]
    for vid, channel, kind, title in rows:
        db_session.add(VideoSource(case_id=case.id, video_id=vid, url=f"https://www.youtube.com/watch?v={vid}",
                                   title=title, channel_name=channel, classification=kind))
    db_session.commit()
    out = reclassify_case(db_session, case.id)
    got = {v.video_id: (v.use, v.source_class) for v in db_session.query(VideoSource).filter_by(case_id=case.id)}
    assert got == {"a": ("film_candidate", "official_body"), "b": ("research_only", None), "c": ("research_only", None)}
    assert out == {"film_candidate": 1, "research_only": 2, "by_class": {"official_body": 1}}


def test_a_commentary_channel_titled_like_a_witness_video_is_not_a_witness(db_session):
    """V2: the witness/family class needs more than a raw marker in the title — a creator,
    reaction or news channel titled 'Bodycam' is never the witness's own video."""
    yt = dict(url="https://www.youtube.com/watch?v=x")
    for channel in ("Julez with True Crime Reaction", "Law&Crime Network",
                    "True Crime Rocket Science", "Moscow True Crime Podcast"):
        use = VideoUseClassifier().classify(
            {**yt, "title": "Crime scene bodycam released", "channel_name": channel,
             "classification": "unknown"})
        assert use.use == "research_only", channel
    # a private channel's own bodycam/dashcam upload still qualifies
    use = VideoUseClassifier().classify(
        {**yt, "title": "My dashcam caught the white sedan", "channel_name": "Sara Temp",
         "classification": "unknown"})
    assert use.use == "film_candidate" and use.source_class == "witness_family"


def test_an_official_looking_kind_without_an_official_channel_name_is_not_enough():
    """Case 9: 'Court TV Podcast', a book review and a podcast episode were typed 'official' by a guess."""
    for channel in (None, "Court TV Podcast", "Crime Weekly"):
        use = classify(classification="official", channel_name=channel, title="The Idaho Student Murders: Sentencing")
        assert use.use == RESEARCH_ONLY, channel
