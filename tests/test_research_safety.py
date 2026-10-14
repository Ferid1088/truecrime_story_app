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
