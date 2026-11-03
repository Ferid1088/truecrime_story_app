"""The audit of the readings of a film against the lexical evidence. Two things are measured apart, because the first proves nothing about the
second: COVERAGE (how many words have evidence at all) and AGREEMENT (of the words with evidence, how many readings are among what the evidence
allows). A word is never called wrong by this audit alone: a reading that the evidence does not allow is a CONFLICT that needs a decision (the
contextual selector, or the owner), unless it is a transcription fault of format, which is certain."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from app.documentary.reading import scheme
from app.documentary.reading.block import BlockInput
from app.documentary.reading.morphology import Morphology, Option
from app.documentary.reading.transcription import Transcription, join
from app.documentary.reading.words import Word

VOWEL = re.compile("[aeoāiu]")


def compare_key(reading: str) -> str:
    """What two readings are compared by: no hyphen (except one that keeps kh, gh, zh, sh, ch apart), pause or glottal stop."""
    return join(re.sub(r"[|']", "", reading))


def skeleton(reading: str) -> str:
    return VOWEL.sub("", compare_key(reading).replace("ow", "o").replace("ey", "e"))


@dataclass
class Finding:
    block_id: str
    n: int
    surface: str
    reading: str
    status: str                       # number | no_evidence | supported | vowel_conflict | form_conflict
    format_fixes: list[tuple[str, str]] = field(default_factory=list)
    sound_doubts: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    basis: str = ""
    homograph: bool = False
    span: int = 1
    support: dict[str, list[str]] = field(default_factory=dict)      # for each allowed reading: what supports it (see morphology.strong)


class Auditor:
    def __init__(self, morphology: Morphology, transcription: Transcription | None = None):
        self.morphology = morphology
        self.tr = transcription or Transcription()

    def forms(self, latin: str) -> list[str]:
        """The reading as compared: with its ezafe, and without it (the writer may add an ezafe the written word does not show)."""
        whole = scheme.split_pause(self.tr.check(latin).normalized)[0]
        stem, _ = scheme.split_ezafe(whole)
        return list(dict.fromkeys([compare_key(whole), compare_key(stem)]))

    @staticmethod
    def homograph(options: list[Option]) -> bool:
        """True when the written word has entries with different meanings that are read with different sounds (کرم): the sentence decides."""
        by_entry: dict[int, set[str]] = {}
        for o in options:
            for e in o.entries if "romanization" in o.kinds else []:
                by_entry.setdefault(id(e), set()).update([compare_key(o.latin)])
        sets = list(by_entry.values())
        return any(not (a & b) for i, a in enumerate(sets) for b in sets[i + 1:])

    @staticmethod
    def support_of(options: list[Option]) -> dict[str, list[str]]:
        out: dict[str, set[str]] = {}
        for o in options:
            out.setdefault(compare_key(o.latin), set()).update(o.kinds)
        return {k: sorted(v) for k, v in out.items()}

    def judge(self, mine: list[str], options: list[Option]) -> tuple[str, list[str], str, bool]:
        allowed = list(dict.fromkeys(compare_key(o.latin) for o in options))
        if not allowed:
            return "no_evidence", [], "", False
        homograph = self.homograph(options)
        hit = next((o for o in options if compare_key(o.latin) in mine), None)
        if hit is not None:
            return "supported", allowed, hit.basis, homograph
        same = [a for a in allowed if skeleton(a) == skeleton(mine[-1])]
        return ("vowel_conflict" if same else "form_conflict"), (same or allowed), options[0].basis, homograph

    def block(self, item: BlockInput, readings: list[str]) -> list[Finding]:
        words: list[Word] = item.words
        out: list[Finding] = []
        i = 0
        surfaces = [w.key for w in words]
        while i < len(words):
            w, checked = words[i], self.tr.check(readings[i])
            fixes = [(f.before, f.after) for f in checked.fixes]
            doubts = [*checked.unknown, *checked.ambiguous, *checked.problems]
            if w.number:
                out.append(Finding(item.block_id, w.n, w.surface, readings[i], "number", fixes, doubts))
                i += 1
                continue
            phrase = next(((size, opts) for size, opts in reversed(self.morphology.phrases(surfaces, i)) if i + size <= len(words)), None)
            if phrase is not None:
                size, opts = phrase
                joined = ["".join(self.forms(readings[i + k])[-1] for k in range(size))]
                status, allowed, basis, homograph = self.judge(joined, opts)
                for k in range(size):
                    out.append(Finding(item.block_id, words[i + k].n, words[i + k].surface, readings[i + k], status, fixes if k == 0 else [], doubts if k == 0 else [],
                                       allowed, basis, homograph, size, self.support_of(opts)))
                i += size
                continue
            options = self.morphology.options(w.surface)
            status, allowed, basis, homograph = self.judge(self.forms(readings[i]), options)
            out.append(Finding(item.block_id, w.n, w.surface, readings[i], status, fixes, doubts, allowed, basis, homograph, 1, self.support_of(options)))
            i += 1
        return out


def summary(findings: list[Finding]) -> dict:
    counts = Counter(f.status for f in findings)
    evaluated = counts["supported"] + counts["vowel_conflict"] + counts["form_conflict"]
    total = len(findings) - counts["number"]
    return {"words": len(findings), "numbers": counts["number"], "by_status": dict(counts),
            "coverage": round(evaluated / total, 4) if total else 0.0,
            "agreement_with_evidence": round(counts["supported"] / evaluated, 4) if evaluated else 0.0,
            "format_faults": sum(1 for f in findings if f.format_fixes), "sound_doubts": sum(1 for f in findings if f.sound_doubts),
            "homographs": sum(1 for f in findings if f.homograph)}
