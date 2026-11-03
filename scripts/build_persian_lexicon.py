"""Downloads the Persian Wiktionary extract (Kaikki) and builds the lexical evidence index (data/lexicon, not committed)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.documentary.reading.evidence import Lexicon  # noqa: E402


def main() -> None:
    lexicon = Lexicon()
    raw = Lexicon.download() if len(sys.argv) < 2 else Path(sys.argv[1])
    print(f"{lexicon.build(raw)} written forms indexed -> {Lexicon.path()}")


if __name__ == "__main__":
    main()
