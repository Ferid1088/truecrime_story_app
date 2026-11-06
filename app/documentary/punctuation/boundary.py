"""Where an ezafe is required, forbidden or depends on the context, for every word boundary of a block.

Rules that hold whatever the meaning decide first: the end of a block, digits, punctuation between the words (an ezafe never crosses it), a word
that never takes an ezafe, a word no ezafe comes before, a verb as the next word (reading/ezafe.py's lists), and an ezafe the text writes itself
(a kasra, ۀ, ه‌ی). Every other boundary is EZAFE_CONTEXT_DEPENDENT unless the syntax agent (syntax.py) named the relation; its decision is kept
with its reason, and an ambiguity it reported stays context-dependent. Nothing is guessed from adjacency.

For a required ezafe the spoken form is given when the letters decide it (-ye after a vowel letter or a written ه‌ی / ۀ, -e after a consonant
letter); after a plain ه it is left open (the ه may be said e or h). `punctuation_can_clarify`: a forbidden boundary the syntax agent says a comma
may mark; `needs_phonetic_guidance`: a required ezafe the text does not write (PPIE has to write it)."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.ai_config import ai_config
from app.documentary.reading.ezafe import EzafeAnalyzer, base_key, written_ezafe
from app.documentary.reading.words import TAG, Word

REQUIRED, FORBIDDEN, CONTEXT, NONE = "EZAFE_REQUIRED", "EZAFE_FORBIDDEN", "EZAFE_CONTEXT_DEPENDENT", "NO_EZAFE_ANALYSIS_NEEDED"
COHESIVE = "CONSTRUCTION_UNBROKEN"        # a language without an ezafe: the syntax agent says the two words are one construction, no mark may cut it


@dataclass(frozen=True)
class BoundaryDecision:
    n: int                       # between word n and word n + 1
    status: str
    reason: str
    source: str                  # rule | text | syntax_agent | none
    pronunciation: str = ""      # -e | -ye | "" (none, or the letters do not decide)
    punctuation_can_clarify: bool = False
    needs_phonetic_guidance: bool = False
    relation: str = ""

    def describe(self, words: list[Word]) -> dict:
        nxt = words[self.n + 1] if self.n + 1 < len(words) else None
        return {"id": f"b{self.n}", "after_word": self.n, "words": [words[self.n].surface, nxt.surface if nxt else None], "status": self.status,
                "reason": self.reason, "source": self.source, "relation": self.relation, "pronunciation": self.pronunciation,
                "span": [words[self.n].start, nxt.end if nxt else words[self.n].end], "punctuation_can_clarify": self.punctuation_can_clarify,
                "needs_phonetic_guidance": self.needs_phonetic_guidance}


def spoken_form(word: Word) -> str:
    plain = word.plain.rstrip("ِ")
    if word.surface.endswith(("ۀ", "هٔ")) or plain.endswith("ه‌ی"):
        return "-ye"
    if plain.endswith(tuple(ai_config.semantic_punctuation.profile("fa").vowel_letters)):
        return "-ye"
    return "" if plain.endswith("ه") else "-e"


def analyze(words: list[Word], gaps: list[str], syntax: dict[int, dict] | None = None, ambiguous: set[int] | None = None,
            language: str = "fa") -> list[BoundaryDecision]:
    """One decision per word of the block (the last word's boundary is the end of the block). `gaps`: the text between the words (gaps[n + 1]
    follows word n; tags and all). `syntax`: the syntax agent's boundaries by word number ({"ezafe": "required|forbidden|context", "relation",
    "reason", "comma_ok"}); `ambiguous`: the boundaries it could not settle. A language without an ezafe has nothing to decide: no boundary is
    required, forbidden or open, the punctuation agent's own judgement (and the checker's) is all that applies."""
    if not ai_config.semantic_punctuation.profile(language).ezafe:
        syntax = syntax or {}
        return [BoundaryDecision(w.n, COHESIVE, str(found.get("reason") or "one construction"), "syntax_agent", relation=str(found.get("relation") or ""))
                if (found := syntax.get(w.n)) and found.get("comma_ok") is False and w.n + 1 < len(words)
                else BoundaryDecision(w.n, NONE, "this language has no ezafe", "rule") for w in words]
    rules, syntax, ambiguous = EzafeAnalyzer(), syntax or {}, ambiguous or set()
    out = []
    for w in words:
        nxt = words[w.n + 1] if w.n + 1 < len(words) else None
        if nxt is None:
            out.append(BoundaryDecision(w.n, NONE, "the end of the block", "rule"))
            continue
        if w.number or nxt.number:
            out.append(BoundaryDecision(w.n, NONE, "a number: read by code", "rule"))
            continue
        between = TAG.sub(" ", gaps[w.n + 1]).strip()
        if any(ch in ai_config.semantic_punctuation.profile(language).phrase_end_marks for ch in between):
            out.append(BoundaryDecision(w.n, FORBIDDEN, f"punctuation {between!r} between the words: an ezafe never crosses it", "rule"))
            continue
        if base_key(w) in rules.never_takes:
            out.append(BoundaryDecision(w.n, NONE, f"{w.plain} never takes an ezafe", "rule"))
            continue
        if nxt.key in rules.never_before or any(nxt.surface.startswith(p) for p in rules.cfg.verb_prefixes):
            out.append(BoundaryDecision(w.n, FORBIDDEN, f"no ezafe before {nxt.plain}", "rule"))
            continue
        if written_ezafe(w, ""):
            out.append(BoundaryDecision(w.n, REQUIRED, "the text writes the ezafe", "text", spoken_form(w)))
            continue
        found = syntax.get(w.n)
        if found and w.n not in ambiguous and found.get("ezafe") in ("required", "forbidden"):
            required = found["ezafe"] == "required"
            out.append(BoundaryDecision(w.n, REQUIRED if required else FORBIDDEN, str(found.get("reason") or ""), "syntax_agent",
                                        spoken_form(w) if required else "", not required and bool(found.get("comma_ok")), required,
                                        str(found.get("relation") or "")))
            continue
        why = "the syntax agent could not settle it" if w.n in ambiguous else "the meaning decides (no rule does): needs the syntax analysis"
        out.append(BoundaryDecision(w.n, CONTEXT, why, "syntax_agent" if found or w.n in ambiguous else "none", relation=str((found or {}).get("relation") or "")))
    return out
