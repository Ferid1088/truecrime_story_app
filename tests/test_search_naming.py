from app.lifecycle.search_naming import generate_search_name


def _base(**overrides) -> dict:
    data = {
        "theme": "true crime", "languages": ["en"],
        "country_code": None, "incident_year_from": None, "incident_year_to": None,
        "resolution_filter": "solved_only",
    }
    data.update(overrides)
    return data


def test_generated_name_any_country_any_year_solved_only():
    assert generate_search_name(_base(), 42) == "True crime · Worldwide · Any year · Solved · en · #42"


def test_generated_name_with_country_and_full_year_range():
    name = generate_search_name(_base(country_code="DE", incident_year_from=2000, incident_year_to=2020), 1)
    assert name == "True crime · Germany · 2000–2020 · Solved · en · #1"


def test_generated_name_open_ended_year_ranges():
    only_from = generate_search_name(_base(incident_year_from=1990), 2)
    assert "1990–now" in only_from
    only_to = generate_search_name(_base(incident_year_to=2010), 3)
    assert "…–2010" in only_to


def test_generated_name_resolution_labels():
    assert "· Unsolved · " in generate_search_name(_base(resolution_filter="unsolved_only"), 4)
    assert "· Solved+Unsolved · " in generate_search_name(_base(resolution_filter="both"), 5)


def test_generated_name_multiple_languages_preserve_order():
    name = generate_search_name(_base(languages=["de", "en", "fa"]), 6)
    assert "· de+en+fa · #6" in name


def test_generated_name_custom_theme_is_capitalized():
    name = generate_search_name(_base(theme="missing persons in Scandinavia"), 7)
    assert name.startswith("Missing persons in Scandinavia · ")


def test_generated_name_unknown_country_code_falls_back_to_the_code_itself():
    # Defensive: a stored code that no longer resolves (config changed) must
    # never crash the name — show the raw code rather than hide the criterion.
    name = generate_search_name(_base(country_code="ZZ"), 8)
    assert "· ZZ · " in name


def test_generated_name_backfill_unknown_criteria_are_not_recorded():
    """D2 backfill: a pre-D1 discovery job's input_json never had
    country_code/incident_year_*/resolution_filter keys at all — that is
    genuinely unknown, not the same as a search that explicitly asked for
    'any country'/'any year' (which stores the key as null)."""
    legacy = {"count": 5, "languages": ["en", "de"], "theme": "true crime"}
    name = generate_search_name(legacy, 2)
    assert name == "True crime · not recorded · not recorded · not recorded · en+de · #2"


def test_generated_name_backfill_reconstructs_resolution_from_legacy_include_unsolved():
    legacy_true = {"languages": ["en"], "theme": "true crime", "include_unsolved": True}
    legacy_false = {"languages": ["en"], "theme": "true crime", "include_unsolved": False}
    assert "· Solved+Unsolved · " in generate_search_name(legacy_true, 9)
    assert "· Solved · " in generate_search_name(legacy_false, 10)


def test_generated_name_no_languages_at_all_is_not_recorded():
    name = generate_search_name({"theme": "true crime", "languages": []}, 11)
    assert "· not recorded · #11" in name
