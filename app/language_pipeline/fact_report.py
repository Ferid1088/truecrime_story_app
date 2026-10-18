"""The English film text against the English master: the numbers, dates and names one has and the
other lacks. A report for the owner, nothing more: it blocks no stage, triggers no repair and
makes no model call (config post_master_fact_report). Whether an addition is an error or a
legitimate re-telling is the owner's reading, not the report's."""

from __future__ import annotations

import re

from app.language_pipeline import figures
from app.language_pipeline.glossary import entities_of
from app.language_pipeline.materials import SharedMaterial

_CAPITALISED = re.compile(r"[A-Z][\w'’.-]*")
_CONNECTORS = {"of", "the", "de", "van", "von", "der", "la", "le", "du", "del", "and"}
_MARKER = re.compile(r"\[\[ACT:[^\]]+\]\]")
_SENTENCE_START = re.compile(r"(?:^|[.!?]['\"”’)]*\s+|\n)\s*$")


def _fold(text: str) -> str:
    return " ".join(text.casefold().split())


def proper_names(text: str) -> set[str]:
    """Runs of capitalised words (a connector such as "of" may sit inside a run). A single
    capitalised word that only starts a sentence is not taken for a name."""
    text = _MARKER.sub(" ", text or "")
    words = list(re.finditer(r"[\w'’.-]+", text))
    names: set[str] = set()
    run: list[re.Match] = []

    def close() -> None:
        while run and run[-1].group(0).casefold() in _CONNECTORS:
            run.pop()
        if not run:
            return
        starts_sentence = _SENTENCE_START.search(text[:run[0].start()]) is not None
        if len(run) > 1 or not starts_sentence:
            names.add(" ".join(m.group(0).strip(".'’-") for m in run))
        run.clear()

    for match in words:
        word = match.group(0)
        if _CAPITALISED.fullmatch(word) or run and word.casefold() in _CONNECTORS:
            run.append(match)
        else:
            close()
    close()
    return {n for n in names if n}


def _figure_set(text: str) -> set[str]:
    return figures.supported_figures(text)


def _present(name: str, folded_text: str) -> bool:
    """A name is present when it stands in the text whole or every one of its words does."""
    folded = _fold(name)
    return folded in folded_text or all(w in folded_text for w in folded.split() if len(w) > 2)


NAME_NOTES = {
    "added_and_missing": "Numbers and names both ways.",
    "missing": ("Numbers both ways. Names: only those of the master that the text lacks; names the text "
                "adds are not reported (they cannot be told reliably from other capitalised words in "
                "this language)."),
    "none": ("Numbers only. Names are not compared (transliteration between scripts cannot be matched "
             "reliably)."),
}
WRITTEN_OUT = " A number written out in words is not recognised in the film text and may show as missing."


def compare(master_text: str, film_text: str, material: SharedMaterial,
            names: str = "added_and_missing") -> dict:
    """What the film text states that the master does not (added) and what the master states that
    the film text lacks (missing): figures (years, dates, amounts, durations; the digits of every
    script count as Western digits, a thousands separator or a decimal comma is normalised) and,
    by `names` (the language's config): "added_and_missing", "missing" (only the master's names
    the text lacks) or "none"."""
    master_figures, film_figures = _figure_set(master_text), _figure_set(film_text)
    contexts = {u["figure"]: u["context"] for u in figures.unsupported(film_text, master_figures)}
    master_names = proper_names(master_text) if names != "none" else set()
    film_names = proper_names(film_text) if names == "added_and_missing" else set()
    folded_master, folded_film = _fold(master_text), _fold(film_text)
    evidence_names = [e.name for e in entities_of(material)] if names != "none" else []
    return {
        "added": {
            "figures": [{"figure": f, "context": contexts.get(f)}
                        for f in sorted(film_figures - master_figures)],
            "names": sorted(n for n in film_names if not _present(n, folded_master))},
        "missing": {
            "figures": sorted(master_figures - film_figures),
            "names": sorted(n for n in master_names if not _present(n, folded_film)),
            "evidence_names": sorted(n for n in evidence_names
                                     if _present(n, folded_master) and not _present(n, folded_film))},
        "master_figures": len(master_figures), "film_figures": len(film_figures),
        "master_names": len(master_names), "film_names": len(film_names),
        "names_mode": names, "names_compared": names != "none",
        "blocking": False, "model_calls": 0, "note": NAME_NOTES[names] + WRITTEN_OUT,
    }
