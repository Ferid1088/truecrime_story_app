from app.core.quote_check import quote_is_supported


def test_a_quote_present_in_the_source_is_supported():
    source = ("On the morning of May 22, 1924, the body of a fourteen-year-old "
              "boy was discovered in a culvert near the Indiana border.")
    assert quote_is_supported("the body of a fourteen-year-old boy was discovered", source) is True


def test_a_fabricated_quote_is_not_supported():
    source = ("On the morning of May 22, 1924, the body of a fourteen-year-old "
              "boy was discovered in a culvert near the Indiana border.")
    assert quote_is_supported("the defendant confessed to the murder in open court", source) is False


def test_no_quote_to_check_returns_none():
    assert quote_is_supported(None, "some source text") is None
    assert quote_is_supported("", "some source text") is None


def test_no_source_text_to_check_against_returns_none():
    assert quote_is_supported("a real quote", "") is None
    assert quote_is_supported("a real quote", None) is None
