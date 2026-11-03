"""Word-level reading of a phoneme recognizer's output.

A recognizer (wav2vec2, Allosaurus) returns phones with frame times for a whole recording. The words' own windows come from the voice's timeline (the
character alignment of the provider), never from the recognizer: a forced alignment of the expected text would 'find' the expected phones whether or not they were
said. Each recognized phone goes to the one word it overlaps most; the phones of a word are then compared with the candidate pronunciations of the word by edit
distance. The answer is an opinion of the recognizer, not a verified fact: it is `unclear` when the word's window holds too little speech or no candidate is clearly
the nearest, and nothing here approves a pronunciation."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.ai_config import ai_config
from app.documentary.phonetic_benchmark import edit_distance_to_stretch, normalise


@dataclass(frozen=True)
class Window:
    n: int
    surface: str
    start: float
    end: float


@dataclass
class WordPhones:
    window: Window
    heard: list[str] = field(default_factory=list)
    coverage: float = 0.0                          # the share of the window covered by the phones it was given
    boundary_phones: int = 0                       # phones that overlap another word's window too: the boundary is uncertain


@dataclass
class WordReading:
    window: Window
    heard: list[str]
    coverage: float
    distances: dict[str, int]
    choice: str                                    # a candidate key, or "unclear"
    why: str

    def describe(self) -> dict:
        return {"n": self.window.n, "surface": self.window.surface, "heard": " ".join(self.heard), "coverage": round(self.coverage, 2), "distances": self.distances,
                "choice": self.choice, "why": self.why}


def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def assign(frames: list[tuple[str, float, float]], windows: list[Window]) -> list[WordPhones]:
    """Each phone to the word whose (widened) window it overlaps most; none to a phone in a gap between words."""
    tol = ai_config.phoneme_alignment.tolerance_s
    out = [WordPhones(w) for w in windows]
    for phone, start, end in frames:
        shares = [overlap(start, end, w.start - tol, w.end + tol) for w in windows]
        best = max(range(len(windows)), key=lambda i: shares[i], default=None)
        if best is None or shares[best] <= 0:
            continue
        out[best].heard.append(phone)
        out[best].coverage += overlap(start, end, windows[best].start, windows[best].end)
        out[best].boundary_phones += sum(1 for s in shares if s > 0) > 1
    for item in out:
        span = item.window.end - item.window.start
        item.coverage = min(1.0, item.coverage / span) if span > 0 else 0.0
    return out


def read(word: WordPhones, candidates: dict[str, str]) -> WordReading:
    """The nearest candidate (key -> IPA) to what the recognizer heard in the word's window, or `unclear`, with the reason."""
    cfg = ai_config.phoneme_alignment
    heard = normalise(word.heard)
    distances = {key: edit_distance_to_stretch(normalise(list(ipa)), heard) for key, ipa in candidates.items()}

    def made(choice: str, why: str) -> WordReading:
        return WordReading(word.window, heard, word.coverage, distances, choice, why)

    if not heard or word.coverage < cfg.min_coverage:
        return made("unclear", f"too little speech recognized in the word's window (coverage {word.coverage:.2f} < {cfg.min_coverage})")
    ranked = sorted(distances.items(), key=lambda kv: kv[1])
    if len(ranked) > 1 and ranked[1][1] - ranked[0][1] < cfg.min_margin:
        return made("unclear", f"no candidate is clearly nearest (margin {ranked[1][1] - ranked[0][1]} < {cfg.min_margin} edits)")
    return made(ranked[0][0], "nearest candidate by edit distance: a recognizer's opinion, not a verified fact")


def read_block(frames: list[tuple[str, float, float]], windows: list[Window], candidates: dict[int, dict[str, str]]) -> list[WordReading]:
    """The reading of every word that has candidates (word number -> {key: ipa})."""
    return [read(w, candidates[w.window.n]) for w in assign(frames, windows) if w.window.n in candidates]
