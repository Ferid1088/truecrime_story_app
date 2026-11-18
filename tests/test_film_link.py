"""The links from a language's Shorts to its full films: first rows of the caption, nothing before them, a reserved row while a film has no address (docs/platform_specs.md)."""

from __future__ import annotations

from app.publishing.film_link import (
    reserved_row,
    with_bio_note,
    with_film_links,
    without_reserved_rows,
)

YOUTUBE, FACEBOOK = "https://www.youtube.com/watch?v=FILM123", "https://www.facebook.com/reel/77/"


def test_the_addresses_are_the_first_rows_in_the_given_order_with_the_text_after_them():
    text = "Hook line.\n\nA question?\n\nMusic made with a tool."
    assert with_film_links(text, [YOUTUBE, FACEBOOK]) == f"{YOUTUBE}\n{FACEBOOK}\n\n{text}"
    assert with_film_links(text, [YOUTUBE]) == f"{YOUTUBE}\n\n{text}"


def test_an_address_that_stood_further_down_is_moved_up_and_applying_it_again_changes_nothing():
    old = f"Hook line.\n\nWatch the film.\n{YOUTUBE}\n\nMusic credit.\n\n{FACEBOOK}"
    moved = with_film_links(old, [YOUTUBE, FACEBOOK])
    assert moved.split("\n")[:3] == [YOUTUBE, FACEBOOK, ""] and moved.count(YOUTUBE) == 1 and moved.count(FACEBOOK) == 1
    assert moved.split("\n\n") == [f"{YOUTUBE}\n{FACEBOOK}", "Hook line.", "Watch the film.", "Music credit."]            # no blank gaps where they stood
    assert with_film_links(moved, [YOUTUBE, FACEBOOK]) == moved
    assert with_film_links(f"{YOUTUBE}\n\nText.", [YOUTUBE, FACEBOOK]) == f"{YOUTUBE}\n{FACEBOOK}\n\nText."           # a second film's address joins the first


def test_a_film_without_an_address_has_a_reserved_row_that_the_address_replaces_and_that_is_never_sent():
    text = "Hook line.\n\nMusic credit."
    planned = with_film_links(text, [reserved_row("youtube"), reserved_row("facebook")])
    assert planned.split("\n")[:3] == ["{{film:youtube}}", "{{film:facebook}}", ""]
    assert without_reserved_rows(planned) == text                                                                    # what is sent holds no reserved row
    half = with_film_links(planned, [YOUTUBE, reserved_row("facebook")])
    assert half.split("\n")[:3] == [YOUTUBE, "{{film:facebook}}", ""] and half.count("{{film") == 1
    assert without_reserved_rows(half) == f"{YOUTUBE}\n\n{text}"
    done = with_film_links(half, [YOUTUBE, FACEBOOK])
    assert "{{film" not in done and done == f"{YOUTUBE}\n{FACEBOOK}\n\n{text}" and without_reserved_rows(done) == done


def test_without_rows_the_text_is_untouched():
    assert with_film_links("Only text.\n\nMore.", []) == "Only text.\n\nMore."


def test_the_bio_line_goes_just_before_the_music_credit_or_at_the_end_and_never_twice():
    text = "Hook.\n\nA question?\n\nMusic credit."
    noted = with_bio_note(text, "Full film: link in bio.", "Music credit.")
    assert noted.split("\n\n") == ["Hook.", "A question?", "Full film: link in bio.", "Music credit."]
    assert with_bio_note(noted, "Full film: link in bio.", "Music credit.") == noted
    assert with_bio_note("Only text.", "Note.").split("\n\n") == ["Only text.", "Note."] and with_bio_note("Only text.", "") == "Only text."
