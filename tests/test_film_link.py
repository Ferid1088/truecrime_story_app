"""The link from a language's YouTube Shorts to its full film (docs/platform_specs.md)."""

from __future__ import annotations

from app.publishing.film_link import with_film_link

URL = "https://www.youtube.com/watch?v=FILM123"
CTA = "Watch the whole story in the film."


def test_the_address_goes_on_its_own_line_after_the_call_to_action_and_never_twice():
    text = f"Hook line.\n\nA question?\n\n{CTA} More words here.\n\nMusic made with a tool."
    linked = with_film_link(text, CTA, URL)
    assert linked.split("\n\n") == ["Hook line.", "A question?", f"{CTA} More words here.", URL, "Music made with a tool."]
    assert with_film_link(linked, CTA, URL) == linked


def test_without_a_matching_call_to_action_the_address_comes_before_the_last_paragraph_or_at_the_end():
    assert with_film_link("Hook.\n\nMusic credit.", "unrelated words", URL).split("\n\n") == ["Hook.", URL, "Music credit."]
    assert with_film_link("Only one paragraph.", "", URL).split("\n\n") == ["Only one paragraph.", URL]
