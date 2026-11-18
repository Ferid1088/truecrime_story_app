"""The address of a language's full film inside the text of its YouTube Shorts: a viewer who wants the whole story finds the link in the description (YouTube's related-video field has no API and is
set by hand in Studio; see docs/platform_specs.md)."""

from __future__ import annotations


def with_film_link(text: str, cta: str, url: str) -> str:
    """The text with the film's address on a line of its own right after the paragraph that invites to the film (the call to action); else before the last paragraph (the music credit), else at
    the end. A text that holds the address already is returned as it is."""
    if url in text:
        return text
    paragraphs = text.rstrip().split("\n\n")
    probe = (cta or "").strip()[:20]
    at = next((i for i, p in enumerate(paragraphs) if probe and probe in p), None)
    if at is None:
        at = len(paragraphs) - 2 if len(paragraphs) > 1 else len(paragraphs) - 1
    paragraphs.insert(at + 1, url)
    return "\n\n".join(paragraphs)
