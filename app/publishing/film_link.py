"""The addresses of a language's full films inside the text of its Shorts: a viewer who wants the whole story finds them first, in the first rows of the caption (YouTube's related-video field has no
API and is set by hand in Studio; see docs/platform_specs.md).

A Short's caption is created with those rows from the start, whether the film has an address yet or not: a film without one has a reserved row, `{{film:youtube}}`, that the real address replaces as soon
as the film is uploaded. A reserved row is never sent to a platform (`without_reserved_rows`)."""

from __future__ import annotations

import re

RESERVED = re.compile(r"^\{\{film:[a-z]+\}\}$")


def reserved_row(film_platform: str) -> str:
    return f"{{{{film:{film_platform}}}}}"


def with_film_links(text: str, rows: list[str]) -> str:
    """The text with the film rows as its first rows, one to a row, in the given order (an address, or the reserved row of a film that has none yet) and nothing before them; the rest of the text
    follows after one blank row. A row that stood elsewhere in the text, and any reserved row, is taken out first, so the result is the same however often it is applied."""
    if not rows:
        return text
    body = [line for line in text.split("\n") if line.strip() not in rows and not RESERVED.match(line.strip())]
    return "\n".join([*rows, "", *_tidy(body)]).rstrip()


def without_reserved_rows(text: str) -> str:
    """The text as it is sent: the reserved rows of films without an address are left out (and the blank row that followed them)."""
    if not any(RESERVED.match(line.strip()) for line in text.split("\n")):
        return text
    return "\n".join(_tidy([line for line in text.split("\n") if not RESERVED.match(line.strip())]))


def _tidy(lines: list[str]) -> list[str]:
    """No blank rows at the top and no run of blank rows where a row was taken out."""
    while lines and not lines[0].strip():
        lines = lines[1:]
    out: list[str] = []
    for line in lines:
        if line.strip() or (out and out[-1].strip()):
            out.append(line)
    return out


def with_bio_note(text: str, note: str, before: str = "") -> str:
    """The text with a line that points to the link in the profile bio (where Instagram makes a link tappable), as its own paragraph just before the paragraph `before` (the music credit), else at the
    end. A text that holds the note already is returned as it is."""
    if not note or note in text:
        return text
    paragraphs = text.rstrip().split("\n\n")
    at = next((i for i, p in enumerate(paragraphs) if before and p.strip() == before), len(paragraphs))
    paragraphs.insert(at, note)
    return "\n\n".join(paragraphs)
