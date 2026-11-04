"""What the voice said for each word of a take, heard locally (wav2vec2, free): every candidate reading of the word (candidates.py) is scored on the
word's own window of the original take by the CTC loss of its sounds (per frame: lower fits better), the slower copies are scored the same way as a
second signal, and the recognizer's own phones in the window are kept. No forced alignment of the expected text decides anything: the expected
reading has to beat its rivals by a margin.

Decisions: PASS (the expected reading wins by the margin), FAIL (a rival wins by the margin), UNCERTAIN (no clear winner), NOT_EVALUABLE (no window,
too short, no expected reading, or the sounds do not fit the window). While the recognizer has not passed its validation (validation.py) a PASS or
FAIL is only `suggested`: the word's status stays UNCERTAIN and the review is advisory."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.ai_config import ai_config
from app.documentary.ppie_review.candidates import Candidate
from app.documentary.reading.words import Word, key_of, tokenize
from app.documentary.voice_render import _spoken_span, tag_mask

FRAME = 0.02


@dataclass
class WordReview:
    n: int
    surface: str
    window: tuple[float, float] | None
    expected: str | None
    candidates: list[dict] = field(default_factory=list)     # {reading, kind, ipa, loss: {speed: loss per frame | None}}
    heard: str = ""
    suggested: str = "NOT_EVALUABLE"
    status: str = "NOT_EVALUABLE"
    evidence: list[str] = field(default_factory=list)
    model_validation: str = "UNVALIDATED"

    def describe(self) -> dict:
        return {"n": self.n, "word": self.surface, "audio_window": dict(zip(("start", "end"), self.window, strict=True)) if self.window else None,
                "expected": self.expected, "candidates": self.candidates, "heard": self.heard, "suggested": self.suggested, "status": self.status,
                "evidence": self.evidence, "model_validation": self.model_validation, "advisory": self.model_validation != "VALIDATED"}


@dataclass(frozen=True)
class Span:
    start: float              # seconds in the take
    end: float
    char_start: int           # where the word stands in the text the voice was given
    char_end: int

    @property
    def window(self) -> tuple[float, float]:
        return self.start, self.end


def windows(text: str, voice_text: str, alignment: dict) -> dict[int, Span]:
    """Each word of the block's approved text in its take: the words the voice was given are matched to the block's words in order (one to one, or
    a number in digits to the words it was read as), and each gets the voice's own timing of its characters. Empty when the take's timing is not of
    this text or its words cannot be matched (the voice was given other words: the take is not of the approved text)."""
    chars = alignment.get("characters") or []
    if "".join(chars) != voice_text:
        return {}
    mask, found = tag_mask(chars), {}
    for n, span in enumerate(matched(text, voice_text)):
        if not span:
            continue
        timed = _spoken_span(chars, mask, alignment["starts"], alignment["ends"], span[0].start, span[-1].end)
        if timed is not None:
            found[n] = Span(timed[0], timed[1], span[0].start, span[-1].end)
    return found


def matched(text: str, voice_text: str) -> list[list[Word]]:
    """For each word of the approved text, the words of the voice's text it was said as: one, or the words of a number. When the voice was given
    another text, the two are aligned on their plain words and a word with no counterpart gets none (it cannot be judged in this take)."""
    import difflib

    from app.documentary.reading import numbers as NUM

    words, sent = tokenize(text), tokenize(voice_text)
    sizes = [1] * len(words) if len(sent) == len(words) else [
        len((NUM.read(words, w.n).words or w.surface).split()) if NUM.has_digit(w) else 1 for w in words]
    if sum(sizes) == len(sent):                     # word for word (a respelled word is still that word)
        out, k = [], 0
        for size in sizes:
            out.append(sent[k:k + size])
            k += size
        return out
    out = [[] for _ in words]
    ops = difflib.SequenceMatcher(None, [key_of(w.surface) for w in words], [key_of(w.surface) for w in sent], autojunk=False).get_opcodes()
    for tag, a0, a1, b0, _ in ops:
        if tag == "equal":
            for i in range(a1 - a0):
                out[a0 + i] = [sent[b0 + i]]
    return out


def loss(listener, log_probs, start: float, end: float, ipa: str) -> float | None:
    try:
        return round(listener.fit(log_probs, start, end, ipa) / max(1, round((end - start) / FRAME)), 3)
    except ValueError:
        return None


def review_word(word: Word, window: tuple[float, float] | None, expected: str | None, candidates: list[Candidate], signals: dict[float, tuple],
                listener, frames, validated: bool) -> WordReview:
    """`signals`: {speed: log-probabilities of the copy} (1.0 is the original take). `frames`: the recognizer's phones of the original take."""
    cfg = ai_config.ppie_review.acoustic
    out = WordReview(word.n, word.surface, window, expected, model_validation="VALIDATED" if validated else "UNVALIDATED")
    if window is None:
        out.evidence.append("no timing of this word in the take")
        return out
    start, end = max(0.0, window[0] - cfg.pad_s), window[1] + cfg.pad_s
    out.heard = " ".join(listener.between(frames, start, end))
    if end - start < cfg.min_window_s:
        out.evidence.append("the word's window is too short to judge")
        return out
    if not expected or not candidates:
        out.evidence.append("no independent expected reading in this run")
        return out
    for c in candidates:
        out.candidates.append({"reading": c.reading, "kind": c.kind, "ipa": c.ipa,
                               "loss": {str(speed): loss(listener, lp, start / speed, end / speed, c.ipa) for speed, lp in signals.items()}})
    scored = [(c["loss"]["1.0"], c) for c in out.candidates if c["loss"].get("1.0") is not None]
    mine = next((s for s, c in scored if c["kind"] == "expected"), None)
    if mine is None or len(scored) < 2:
        out.evidence.append("the expected reading's sounds do not fit the window")
        return out
    rivals = sorted((s, c) for s, c in scored if c["kind"] != "expected")
    best_rival, rival = rivals[0]
    if best_rival - mine >= cfg.min_margin_per_frame:
        out.suggested = "PASS"
        out.evidence.append(f"the expected reading fits best, by {round(best_rival - mine, 3)} per frame over {rival['reading']}")
    elif mine - best_rival >= cfg.min_margin_per_frame:
        out.suggested = "FAIL"
        out.evidence.append(f"heard as {rival['reading']} ({rival['kind']}): it fits better by {round(mine - best_rival, 3)} per frame")
    else:
        out.suggested = "UNCERTAIN"
        out.evidence.append(f"no reading fits clearly better (margin {round(abs(best_rival - mine), 3)} per frame)")
    out.status = out.suggested if validated else "UNCERTAIN"
    if not validated:
        out.evidence.append("advisory: the recognizer has not passed its validation, so this is not a verified decision")
    slow = [s for s in signals if s != 1.0]
    for speed in slow:
        ranked = sorted((c["loss"][str(speed)], c["reading"]) for c in out.candidates if c["loss"].get(str(speed)) is not None)
        if ranked:
            out.evidence.append(f"slower copy {speed}: best fit {ranked[0][1]} (a second look at the same audio, not an independent recording)")
    return out
