"""The names of the published files: the full film's title words first, then the Short's, what it is, the channel and the language; Persian keeps its letters, Latin-alphabet languages lose their accents,
a long name is cut in the right order, and the named copy is a link that never touches or replaces its source."""

import os

from app.publishing import filenames as F
from app.publishing.config import publishing_config

FILM_FA = "همسرش در تخت مرد و گوشی‌اش رازهای تاریک را پنهان کرد"


def test_words_keep_persian_letters_break_at_half_spaces_and_fold_accents_only_in_latin_languages():
    assert F.words_of("گوشی‌اش، رازهای تاریک!", "fa") == ["گوشی", "اش", "رازهای", "تاریک"]
    assert F.words_of("Ünïcode Çafé — İstanbul ß", "tr") == ["unicode", "cafe", "istanbul", "ss"]
    assert F.words_of("وأسرار مدفونة", "ar") == ["وأسرار", "مدفونة"]               # Arabic keeps its letters
    assert F.words_of("a b c d e f", "en", limit=3) == ["a", "b", "c"]


def test_a_short_is_named_by_the_film_then_its_own_title_then_what_it_is_the_channel_and_the_language():
    name = F.file_name("de", "short", "Mord an der Frau", "Sofa oder Kinderzimmer?", 2)
    assert name == "mord-an-der-frau-sofa-oder-kinderzimmer-short-2-fallspur-de.mp4"
    film = F.file_name("fa", "film", FILM_FA)
    assert film.startswith("همسرش-در-تخت-مرد-و-گوشی-اش-رازهای-تاریک-را-پنهان-کرد-") and film.endswith("-رد-خاموش-fa.mp4") and "مستند-کامل" in film


def test_every_short_of_a_film_starts_with_the_films_title_words_and_differs_by_its_own_words_and_number():
    names = [F.file_name("en", "short", "A Mother Dead in Bed", title, n) for n, title in enumerate(("The call", "The gun", "The messages"), 1)]
    assert len(set(names)) == 3 and all(n.startswith("a-mother-dead-in-bed-") for n in names)


def test_a_long_name_is_cut_the_shorts_words_first_and_never_the_kind_number_channel_or_language():
    cfg = publishing_config().file_names
    name = F.file_name("fa", "short", FILM_FA * 3, "تماس نیمه‌شب شوهر داستان مهاجم را به راز تبدیل کرد " * 3, 4)
    assert len(name.encode("utf-8")) <= cfg.max_bytes and name.endswith("-شورت-4-رد-خاموش-fa.mp4")
    assert name.startswith("همسرش-در-تخت-مرد")                                                 # the film's words are kept, the Short's own words were cut


def test_the_named_copy_is_a_link_that_is_replaced_when_the_source_changes_and_the_source_is_untouched(tmp_path):
    source = tmp_path / "delivery_C1.mp4"
    source.write_bytes(b"video one")
    linked = F.link(source, "name-short-1-en.mp4")
    assert linked.parent.name == publishing_config().file_names.folder and linked.read_bytes() == b"video one" and os.stat(linked).st_ino == os.stat(source).st_ino
    assert F.link(source, "name-short-1-en.mp4") == linked                                      # nothing is made twice
    source.unlink()
    source.write_bytes(b"video two")                                                            # the clip was built again: a new file
    assert F.link(source, "name-short-1-en.mp4").read_bytes() == b"video two" and source.read_bytes() == b"video two"


def test_a_named_copy_that_is_no_longer_current_is_removed_and_only_links_go(tmp_path):
    source = tmp_path / "delivery_C1.mp4"
    source.write_bytes(b"x")
    keep, old = F.link(source, "keep-en.mp4"), F.link(source, "old-title-en.mp4")
    F.drop_stale([{"linked": str(keep)}])
    assert keep.exists() and not old.exists() and source.exists()
