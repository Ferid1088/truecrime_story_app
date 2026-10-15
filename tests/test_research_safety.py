from app.research_engine.content_filter import inspect_result
from app.research_engine.search import SearchResult


def result(url: str, title: str, snippet: str = "") -> SearchResult:
    return SearchResult(backend="fixture", query="case", language="en", rank=1,
                        url=url, title=title, snippet=snippet)


def test_adult_domain_is_blocked_before_fetch():
    reason, score = inspect_result(
        result("https://tabootube.xxx/video", "Case documentary"),
        {"title": "Harbor disappearance"},
    )
    assert reason == "blocked_adult content"
    assert score == 0.0


def test_irrelevant_dictionary_page_is_dropped():
    reason, score = inspect_result(
        result("https://dictionary.example/term", "Dictionary definition", "word meaning"),
        {"title": "Harbor disappearance", "location": "Rotterdam"},
    )
    assert reason == "irrelevant"
    assert score == 0.0


def test_relevant_fixture_page_is_kept():
    reason, score = inspect_result(
        result("https://news.example/harbor", "Harbor disappearance investigation",
               "Police in Rotterdam reopened the Harbor disappearance case."),
        {"title": "Harbor disappearance", "location": "Rotterdam"},
    )
    assert reason is None
    assert score > 0.12


CASE = {"title": "Leopold and Loeb murder of Bobby Franks", "language": "en",
        "location": "Chicago, Illinois", "key_people": ["Nathan Leopold", "Richard Loeb"]}


def test_other_script_result_is_judged_against_its_own_language_query():
    """The case terms are English; an Arabic page about the case shares no
    token with them, so it is judged against the Arabic query that found it."""
    arabic = result("https://ar.wikipedia.org/wiki/x", "ليوبولد ولوب - ويكيبيديا",
                    "قضية ليوبولد ولوب جريمة قتل بوبي فرانكس في شيكاغو")
    reason, score = inspect_result(arabic, CASE, query="قضية ليوبولد ولوب 1924 شيكاغو",
                                   language="ar")
    assert reason is None
    assert score > 0.12


def test_other_script_result_unrelated_to_its_query_is_still_dropped():
    unrelated = result("https://ar.example/recipe", "وصفة الكنافة", "طريقة تحضير الحلويات")
    reason, _ = inspect_result(unrelated, CASE, query="قضية ليوبولد ولوب 1924 شيكاغو",
                               language="ar")
    assert reason == "irrelevant"
