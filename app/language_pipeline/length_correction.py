"""The bounded length correction: the pure decisions around the one call that expands the acts of a
too-short final text. It runs at most once per chain, only when the final is below the length
minimum, and sends only the acts that need words and have the material to give them. The code, not the
model, splices the answer into the final, so every other act stays byte-identical.

What is corrected is what the checks need, not the ideal: first each act below its own lower bound
(its target less the act tolerance) gets the words that bring it to that bound; then, if the whole
text is still below the total minimum, the rest is spread over the acts that can still grow towards
their target. The upper bounds (act ceiling, total maximum) are never crossed."""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass

from app.core.ai_config import ai_config
from app.language_pipeline.lengths import LengthRange
from app.language_pipeline.stages import marked
from app.services.writer_metrics import repeated_trigram_rate, words

SKIPPED, STARTED, REJECTED, STORED = "skipped", "started", "rejected", "stored"
# The inspector's kinds that ask for something to leave the text; their instructions bind the correction.
REMOVAL_KINDS = ("selection", "repetition")


def words_of(section: dict) -> int:
    return len(section["text"].split())


def act_floor(target: int) -> int:
    """The fewest words an act may have: its target less the act tolerance."""
    return math.ceil(target * (1 - ai_config.language_pipeline.act_length_tolerance))


def act_ceiling(target: int) -> int:
    return math.floor(target * (1 + ai_config.language_pipeline.act_length_tolerance))


def required_words(current: dict[str, int], targets: dict[str, int], span: LengthRange,
                   overhead: int = 0) -> dict[str, int]:
    """The words each act must gain: to its own lower bound first (an act that needs only a handful of
    words is left alone), then the rest of a total shortfall spread over the acts that can still grow
    towards their target, in proportion to the room they have. An empty result when the text needs
    nothing, or when the needed words would cross the total maximum. `overhead` is the words of the text
    that belong to no act (the act markers), which the total counts."""
    minimum = ai_config.language_pipeline.length_correction.min_needed_words
    floor_needs = {act: max(act_floor(target) - current.get(act, 0), 0) for act, target in targets.items()}
    needs = {act: n if n >= minimum else 0 for act, n in floor_needs.items()}
    remaining = max(span.gate_words_min - (sum(current.values()) + sum(needs.values()) + overhead), 0)
    if remaining:
        room = {act: max(min(targets[act], act_ceiling(targets[act])) - current.get(act, 0) - needs[act], 0)
                for act in targets}
        capacity = sum(room.values())
        added = dict.fromkeys(room, 0)
        if capacity:
            for act in sorted(room, key=lambda a: room[a], reverse=True):
                share = min(room[act], math.ceil(remaining * room[act] / capacity), remaining)
                added[act] += share
                remaining -= share
            for act in sorted(room, key=lambda a: room[a], reverse=True):
                more = min(room[act] - added[act], remaining)
                if more > 0:
                    added[act] += more
                    remaining -= more
        needs = {act: needs[act] + added.get(act, 0) for act in needs}
    if sum(current.values()) + sum(needs.values()) + overhead > span.gate_words_max:
        return {}
    return {act: n for act, n in needs.items() if n > 0}


@dataclass(frozen=True)
class ActShortfall:
    act_id: str
    current_words: int
    target_words: int            # the act's ideal target (act_word_targets)
    master_words: int
    needed_words: int = 0        # what the checks need this act to gain

    @property
    def shortfall_words(self) -> int:
        return self.needed_words

    @property
    def goal_words(self) -> int:
        """The words the correction aims at: the needed words with a margin, never above the act ceiling."""
        margin = ai_config.language_pipeline.length_correction.goal_margin_ratio
        goal = self.current_words + math.ceil(self.needed_words * (1 + margin))
        return min(goal, max(act_ceiling(self.target_words), self.current_words + self.needed_words))

    @property
    def qualifies(self) -> bool:
        """The act needs words and the master tells more of it than it needs."""
        ratio = ai_config.language_pipeline.length_correction.min_material_ratio
        return self.needed_words > 0 and self.master_words - self.current_words >= ratio * self.needed_words

    def as_dict(self) -> dict:
        return {"act_id": self.act_id, "current_words": self.current_words, "target_words": self.target_words,
                "floor_words": act_floor(self.target_words), "needed_words": self.needed_words,
                "goal_words": self.goal_words, "shortfall_words": self.shortfall_words,
                "master_words": self.master_words, "qualifies": self.qualifies}


def sentences_of(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?”\"])\s+|\n+", text) if s.strip()]


def removed_sentences(before: str, after: str, limit: int) -> list[str]:
    """The sentences of `before` that `after` no longer has (a change of wording counts as a removal of
    the old sentence), at most `limit`."""
    old, new = sentences_of(before), sentences_of(after)
    gone: list[str] = []
    for op, i1, i2, _j1, _j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if op in ("delete", "replace"):
            gone.extend(old[i1:i2])
    return [s for s in gone if len(s.split()) >= 6][:limit]


def _grams(text: str) -> set[tuple]:
    w = words(text)
    return {tuple(w[i:i + 3]) for i in range(len(w) - 2)}


class LengthCorrection:
    @staticmethod
    def is_due(final_text: str, span: LengthRange) -> bool:
        """Only a final below the minimum is corrected; one above the maximum never is."""
        return ai_config.language_pipeline.length_correction.enabled and \
            len(final_text.split()) < span.gate_words_min

    @staticmethod
    def shortfalls(final_sections: list[dict], master_sections: list[dict], targets: dict[str, int],
                   span: LengthRange, overhead: int = 0) -> list[ActShortfall]:
        final = {s["id"]: words_of(s) for s in final_sections}
        master = {s["id"]: words_of(s) for s in master_sections}
        needs = required_words(final, targets, span, overhead)
        return [ActShortfall(act, final.get(act, 0), target, master.get(act, 0), needs.get(act, 0))
                for act, target in targets.items()]

    @staticmethod
    def decisions(inspector_items: list[dict], rewrite_sections: list[dict], final_sections: list[dict]) -> dict[str, list]:
        """What the editors removed on purpose, per act: the inspector's instructions to remove, compress
        or merge, and the sentences the final writer took out of the rewrite. They bind the correction."""
        limit = ai_config.language_pipeline.length_correction.max_removed_sentences
        out: dict[str, list] = {}
        for item in inspector_items:
            if item.get("kind") in REMOVAL_KINDS:
                acts = [a.strip() for a in re.split(r"[,;/]| and ", item.get("act") or "") if a.strip()] or [""]
                for act in acts:
                    out.setdefault(act, []).append({"source": "inspector", "kind": item["kind"],
                                                    "quote": item.get("quote", ""),
                                                    "instruction": item.get("instruction", "")})
        before = {s["id"]: s["text"] for s in rewrite_sections}
        for section in final_sections:
            for sentence in removed_sentences(before.get(section["id"], ""), section["text"], limit):
                out.setdefault(section["id"], []).append({"source": "removed_in_final", "sentence": sentence})
        return out

    @staticmethod
    def request(shortfalls: list[ActShortfall], final_sections: list[dict], master_sections: list[dict],
                decisions: dict[str, list] | None = None) -> list[dict]:
        """The acts to send, in story order: the numbers, both texts and the binding editorial decisions
        of each qualifying act (those of the whole script included)."""
        final = {s["id"]: s["text"] for s in final_sections}
        master = {s["id"]: s["text"] for s in master_sections}
        decisions = decisions or {}
        return [{"act_id": a.act_id, "current_words": a.current_words, "target_words": a.goal_words,
                 "shortfall_words": a.shortfall_words, "current_text": final[a.act_id],
                 "master_text": master[a.act_id],
                 "editorial_decisions": decisions.get(a.act_id, []) + decisions.get("whole_script", [])
                 + decisions.get("", [])}
                for a in shortfalls if a.qualifies]

    @staticmethod
    def overshoot(corrected: list[dict], targets: dict[str, int]) -> list[str]:
        """Acts longer than the allowed share above their target (the whole correction is rejected)."""
        limit = ai_config.language_pipeline.length_correction.max_act_overshoot
        return [s["id"] for s in corrected if words_of(s) > round(limit * targets[s["id"]], 6)]

    @staticmethod
    def without_reintroduced(corrected: list[dict], decisions: dict[str, list]) -> tuple[list[dict], list[dict]]:
        """The corrected acts with the sentences taken out that tell again what the editors removed (most of
        a sentence's word sequences are in a removed sentence). The rest of the answer stands. Returns the
        acts and the sentences taken out."""
        threshold = ai_config.language_pipeline.length_correction.reintroduction_overlap
        out, taken = [], []
        # a sentence removed from one act is not welcome back in another either
        removed = [_grams(d["sentence"]) for ds in decisions.values() for d in ds if d.get("sentence")]
        for section in corrected:
            kept_paragraphs = []
            for paragraph in re.split(r"\n\s*\n", section["text"]):
                kept = []
                for sentence in sentences_of(paragraph):
                    grams = _grams(sentence)
                    if grams and any(len(grams & r) / len(grams) >= threshold for r in removed if r):
                        taken.append({"act": section["id"], "sentence": sentence})
                    else:
                        kept.append(sentence)
                if kept:
                    kept_paragraphs.append(" ".join(kept))
            out.append({**section, "text": "\n\n".join(kept_paragraphs)})
        return out, taken

    @staticmethod
    def reintroduced(corrected: list[dict], decisions: dict[str, list]) -> list[dict]:
        """Sentences the editors removed that the corrected acts tell again (most of their word sequences
        are back). The whole correction is rejected when there is one."""
        threshold = ai_config.language_pipeline.length_correction.reintroduction_overlap
        back = []
        for section in corrected:
            grams = _grams(section["text"])
            for decision in decisions.get(section["id"], []):
                sentence = decision.get("sentence")
                if not sentence:
                    continue
                wanted = _grams(sentence)
                if wanted and len(wanted & grams) / len(wanted) >= threshold:
                    back.append({"act": section["id"], "sentence": sentence})
        return back

    @staticmethod
    def splice(final_sections: list[dict], corrected: list[dict]) -> tuple[list[dict], str]:
        """The final with the corrected acts in place of their old text; the other acts are the
        same objects, untouched."""
        new = {s["id"]: s for s in corrected}
        sections = [new.get(s["id"], s) for s in final_sections]
        return sections, marked(sections)

    @staticmethod
    def repetition(before: list[dict], after: list[dict], changed: list[str]) -> dict[str, dict]:
        """Per changed act: the repeated-trigram rate before and after, and whether it rose by more
        than the configured factor ("likely padded"). Information only."""
        factor = ai_config.language_pipeline.length_correction.repetition_flag_factor
        old = {s["id"]: s["text"] for s in before}
        report = {}
        for section in after:
            if section["id"] not in changed:
                continue
            was, now = repeated_trigram_rate(old[section["id"]]), repeated_trigram_rate(section["text"])
            report[section["id"]] = {"trigram_before": was, "trigram_after": now,
                                     "likely_padded": now > was * factor}
        return report
