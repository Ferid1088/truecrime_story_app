"""The outlets a graphic may print as its source: public newspapers and important TV broadcasters, and nothing else.
Anything not on the configured lists is not allowed as a source, so a card that needs one is not made."""

from __future__ import annotations

from app.documentary.graphics import theme as T
from app.documentary.graphics.headline import known_source


def outlet_of(url: str) -> str | None:
    """The configured name of the newspaper or TV broadcaster behind an address (subdomains count), else None."""
    paper = known_source(url)
    if paper:
        return paper
    host = url.split("//", 1)[-1].split("/", 1)[0].lower().removeprefix("www.")
    names = T.config().quote["tv_source_names"]
    return next((name for domain, name in sorted(names.items(), key=lambda d: -len(d[0]))
                 if host == domain or host.endswith("." + domain)), None)
