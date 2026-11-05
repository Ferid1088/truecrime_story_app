"""Which passage of the approved English story each Persian narration block tells.

Both stories are cut into sentences with their offsets (the texts are never changed). Sentences are embedded locally (embeddings.py) and aligned in
story order by dynamic programming: a match joins one or two sentences on each side (one-to-one, one-to-many, many-to-one, many-to-many), a sentence
with no match good enough stays unmatched (MISSING_REFERENCE), and a match is only searched near the diagonal (config band). The similarity is a hint:
each match is checked against the numbers both sides write and against the best English sentence elsewhere. Thresholds are set by reasoning and are not calibrated: no alignment has been reviewed yet, so no confidence number is
given. A block gets the English spans of the sentences inside it and the worst status among them."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.core.ai_config import ai_config
from app.documentary.persian_numbers import DIGITS

def sentence_pattern() -> re.Pattern:
    """A sentence: text up to its end marks and the quotes or brackets that close it (config english_reference.alignment)."""
    cfg = ai_config.english_reference.alignment
    ends, closing = re.escape(cfg.sentence_end_marks), re.escape(cfg.closing_marks)
    return re.compile(f"[^{ends}\\n]+(?:[{ends}]+[{closing}]*)?")


def marker_pattern() -> re.Pattern:
    """Section markers ([[ACT:act1]]) and audio tags ([calm]): not story text."""
    return re.compile(f"\\[\\[[^\\]]*\\]\\]|\\[[^\\[\\]\\n]{{1,{ai_config.english_reference.alignment.max_tag_chars}}}\\]")

DIGIT_RUN = re.compile(r"[0-9۰-۹٠-٩]+")
WORD = re.compile(r"\w")
STATUSES = ("ALIGNED", "PARTIAL", "AMBIGUOUS", "MISMATCH", "MISSING_REFERENCE")      # best to worst


@dataclass(frozen=True)
class Unit:
    n: int
    start: int
    end: int
    text: str

    @property
    def numbers(self) -> frozenset[str]:
        return frozenset(m.group().translate(DIGITS) for m in DIGIT_RUN.finditer(self.text))


@dataclass
class Match:
    persian: tuple[int, int]              # units [first, last)
    english: tuple[int, int] | None       # units [first, last), None: no English match
    similarity: float | None
    best_elsewhere: float | None
    status: str
    evidence: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


@dataclass
class BlockAlignment:
    block_id: str
    persian_span: tuple[int, int] | None
    english_spans: list[tuple[int, int]]
    status: str
    evidence: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    english_text: str = ""

    def describe(self) -> dict:
        return {"block_id": self.block_id, "persian_span": dict(zip(("start", "end"), self.persian_span, strict=True)) if self.persian_span else None,
                "english_spans": [{"start": s, "end": e} for s, e in self.english_spans], "status": self.status, "confidence": None,
                "confidence_note": "uncalibrated: no reviewed alignment exists yet", "evidence": self.evidence, "issues": self.issues,
                "english_text": self.english_text}


def segments(text: str) -> list[Unit]:
    """The sentences of a story with their offsets in it (markers and tags blanked, so the offsets stay those of the text itself)."""
    blanked = marker_pattern().sub(lambda m: " " * len(m.group()), text)
    out = []
    for m in sentence_pattern().finditer(blanked):
        piece = m.group()
        stripped = piece.strip()
        if not WORD.search(stripped):
            continue
        start = m.start() + piece.index(stripped)
        out.append(Unit(len(out), start, start + len(stripped), stripped))
    return out


def grouped(vectors, size: int):
    """Row i: the normalised sum of rows i..i+size-1 (a group of sentences read as one)."""
    import numpy as np

    if len(vectors) < size:
        return np.zeros((0, vectors.shape[1]), dtype=vectors.dtype)
    summed = sum(vectors[k:len(vectors) - size + 1 + k] for k in range(size))
    return summed / np.linalg.norm(summed, axis=1, keepdims=True)


def align(persian: list[Unit], english: list[Unit], fa_vectors, en_vectors) -> list[Match]:
    """The monotonic alignment of the two sentence lists (see the module's doc)."""
    import numpy as np

    cfg = ai_config.english_reference.alignment
    n, m, k = len(persian), len(english), cfg.max_group
    sims = {(a, b): grouped(fa_vectors, a) @ grouped(en_vectors, b).T for a in range(1, k + 1) for b in range(1, k + 1)}
    single = sims[(1, 1)]
    score = np.full((n + 1, m + 1), -np.inf)
    move: dict[tuple[int, int], tuple[int, int]] = {}
    score[0, 0] = 0.0

    def near(i: int, j: int) -> bool:
        return abs(i / max(n, 1) - j / max(m, 1)) <= cfg.band or i == 0 or j == 0

    for i in range(n + 1):
        for j in range(m + 1):
            if (i, j) == (0, 0) or not near(i, j):
                continue
            best, how = -np.inf, None
            for a, b in [(1, 0), (0, 1)] + [(a, b) for a in range(1, k + 1) for b in range(1, k + 1)]:
                pi, pj = i - a, j - b
                if pi < 0 or pj < 0 or score[pi, pj] == -np.inf:
                    continue
                if a and b:
                    sim = float(sims[(a, b)][pi, pj])
                    if sim < cfg.match_floor:
                        continue
                    gain = (sim - cfg.match_floor) * (a + b) / 2
                else:
                    gain = cfg.skip_gain
                if score[pi, pj] + gain > best:
                    best, how = score[pi, pj] + gain, (a, b)
            score[i, j], move[(i, j)] = best, how
    i, j, steps = n, m, []
    while (i, j) != (0, 0):
        a, b = move[(i, j)]
        steps.append((i - a, i, j - b, j))
        i, j = i - a, j - b
    matches = []
    for p0, p1, e0, e1 in reversed(steps):
        if p1 == p0:
            continue                                          # an English sentence the Persian does not tell
        if e1 == e0:
            matches.extend(Match((p, p + 1), None, None, None, "MISSING_REFERENCE", issues=["no English sentence near it tells it"]) for p in range(p0, p1))
            continue
        sim = float(sims[(p1 - p0, e1 - e0)][p0, e0])
        reach = int(cfg.band * m)               # a rival is an English sentence the order allows here, not the one next to the match
        outside = [single[p, e] for p in range(p0, p1) for e in range(max(0, e0 - reach), min(m, e1 + reach)) if not e0 - 1 <= e <= e1]
        matches.append(judge(Match((p0, p1), (e0, e1), round(sim, 3), round(float(max(outside)), 3) if outside else None, ""), persian, english))
    return matches


def judge(match: Match, persian: list[Unit], english: list[Unit]) -> Match:
    """The status of a match from its evidence: similarity, the margin over the best English sentence elsewhere, and the numbers both sides write."""
    cfg = ai_config.english_reference.alignment
    fa = frozenset().union(*(persian[p].numbers for p in range(*match.persian)))
    en = frozenset().union(*(english[e].numbers for e in range(*match.english)))
    match.evidence = [f"similarity {match.similarity}", f"best English sentence elsewhere {match.best_elsewhere}",
                      f"{match.persian[1] - match.persian[0]} Persian to {match.english[1] - match.english[0]} English sentence(s)"]
    if fa and en and not fa & en:
        match.issues.append(f"the numbers differ: {sorted(fa)} / {sorted(en)}")
    if match.best_elsewhere is not None and match.similarity - match.best_elsewhere < cfg.ambiguous_margin:
        match.status = "AMBIGUOUS"
        match.issues.append("another English sentence is about as similar")
    elif match.similarity >= cfg.aligned_min and not match.issues:
        match.status = "ALIGNED"
    elif match.similarity >= cfg.partial_min:
        match.status = "PARTIAL"
    else:
        match.status = "MISMATCH"
    return match


def block_status(statuses: list[str]) -> str:
    """A block's status from its sentences': all unmatched is MISSING_REFERENCE, a mismatch is MISMATCH, an unmatched or partial sentence among
    matched ones makes the block PARTIAL, an ambiguous one AMBIGUOUS, else ALIGNED."""
    found = set(statuses)
    if not found or found == {"MISSING_REFERENCE"}:
        return "MISSING_REFERENCE"
    if "MISMATCH" in found:
        return "MISMATCH"
    if found & {"MISSING_REFERENCE", "PARTIAL"}:
        return "PARTIAL"
    return "AMBIGUOUS" if "AMBIGUOUS" in found else "ALIGNED"


def located(story: str, text: str, after: int) -> tuple[int, int] | None:
    """Where a block's text stands in the story from `after` on, whitespace and tags aside (None: not found, the block is not the approved text)."""
    words = [w for w in marker_pattern().sub(" ", text).split() if w]
    if not words:
        return None
    pattern = re.compile(r"\s+".join(re.escape(w) for w in words))
    found = pattern.search(story, after)
    return (found.start(), found.end()) if found else None


def blocks(plan_blocks: list[dict], story: str, english_story: str, persian: list[Unit], english: list[Unit], matches: list[Match]) -> list[BlockAlignment]:
    by_unit = {p: match for match in matches for p in range(*match.persian)}
    out, after = [], 0
    for b in plan_blocks:
        span = located(story, b.get("text") or "", after)
        if span is None:
            out.append(BlockAlignment(b["block_id"], None, [], "MISSING_REFERENCE", issues=["the block's text is not found in the approved text"]))
            continue
        after = span[1]
        inside = [u.n for u in persian if u.start < span[1] and u.end > span[0]]
        found = list(dict.fromkeys(id(by_unit[n]) for n in inside if n in by_unit))
        chosen = [m for m in matches if id(m) in found]
        spans = sorted({(english[m.english[0]].start, english[m.english[1] - 1].end) for m in chosen if m.english})
        status = block_status([by_unit[n].status if n in by_unit else "MISSING_REFERENCE" for n in inside])
        out.append(BlockAlignment(b["block_id"], span, spans, status,
                                  [f"{m.status}: {'; '.join(m.evidence)}" for m in chosen], [i for m in chosen for i in m.issues],
                                  " ".join(english_story[s:e] for s, e in spans)))
    return out


def prepare(story: str, english_story: str, encoder) -> tuple[list[Unit], list[Unit], list[Match]]:
    """The sentences of both stories and their alignment by code."""
    persian, english = segments(story), segments(english_story)
    return persian, english, align(persian, english, encoder.encode([u.text for u in persian]), encoder.encode([u.text for u in english]))


def summary(persian: list[Unit], english: list[Unit], matches: list[Match], found: list[BlockAlignment]) -> dict:
    return {"persian_sentences": len(persian), "english_sentences": len(english), "matches": len(matches),
            "sentences": {s: sum(m.persian[1] - m.persian[0] for m in matches if m.status == s) for s in STATUSES},
            "blocks": {s: sum(1 for b in found if b.status == s) for s in STATUSES}, "calibrated": False}


def save(path, found: list[BlockAlignment], summary: dict) -> None:
    from app.documentary import storage

    storage.write_atomic(path, json.dumps({"summary": summary, "blocks": [b.describe() for b in found]}, ensure_ascii=False, indent=1))
