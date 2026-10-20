from app.lifecycle.geography import country_name, country_options, location_matches_country


def test_country_options_cover_common_countries():
    codes = {c["code"] for c in country_options()}
    assert {"US", "DE", "GB", "FR", "IR"} <= codes
    assert all({"code", "name"} <= c.keys() for c in country_options())


def test_country_name_resolves_display_name():
    assert country_name("DE") == "Germany"
    assert country_name(None) is None
    assert country_name("zz-not-a-code") is None


def test_location_matches_country_direct_name():
    assert location_matches_country("Ulm, Germany", "DE") is True
    assert location_matches_country("Lagos, Nigeria", "DE") is False


def test_location_matches_country_via_alias():
    # "Türkiye" is the config's canonical name; "Turkey" is a configured alias.
    assert location_matches_country("a village near Izmir, Turkey", "TR") is True


def test_location_matches_country_unknown_when_no_location_text():
    assert location_matches_country(None, "DE") is None
    assert location_matches_country("", "DE") is None


def test_location_matches_country_does_not_false_positive_on_substrings():
    # "oman" is a substring of "woman" — a word-boundary match must not
    # mistake the sentence for a mention of Oman.
    assert location_matches_country("a woman was found near the river", "OM") is False


def test_topic_discovery_request_defaults_to_any_country_and_any_year():
    from app.schemas import TopicDiscoveryRequest

    req = TopicDiscoveryRequest(languages=["en"])
    assert req.country_code is None
    assert req.incident_year_from is None
    assert req.incident_year_to is None


def test_topic_discovery_request_normalizes_country_code_case():
    from app.schemas import TopicDiscoveryRequest

    assert TopicDiscoveryRequest(languages=["en"], country_code="de").country_code == "DE"


def test_topic_discovery_request_rejects_unknown_country_code():
    import pytest
    from pydantic import ValidationError

    from app.schemas import TopicDiscoveryRequest

    with pytest.raises(ValidationError):
        TopicDiscoveryRequest(languages=["en"], country_code="ZZ_NOT_REAL")


def test_topic_discovery_request_rejects_year_from_after_year_to():
    import pytest
    from pydantic import ValidationError

    from app.schemas import TopicDiscoveryRequest

    with pytest.raises(ValidationError):
        TopicDiscoveryRequest(languages=["en"], incident_year_from=2020, incident_year_to=2000)
