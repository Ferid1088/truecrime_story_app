"""The knowledge cutoff of the host (an input firewall, with a deterministic check behind it).

An appearance after act N gets, from the story, ONLY acts 1..N (their narration in the
appearance's language, their evidence, the questions they raise and have not answered) and the
memories it may use. The opening (N = 0) gets the case, its title and the riddles the film itself
poses at its start. Nothing from after N is put into any prompt of the writer or the critic.

The second layer works on the output: words that only the later acts use (rare ones: names,
objects, numbers) must not turn up in the host's words."""

from __future__ import annotations

import re

from app.core.ai_config import HostConfig, ai_config
from app.documentary.host_story import HostStory, words_of

_NOTE = re.compile(r"\s*\([^()]*\)")


def clean_question(text: str) -> str:
    """A planner's question without its bracketed notes (they name acts, outcomes and later
    returns)."""
    return " ".join(_NOTE.sub("", text or "").split())


def riddles(story: HostStory) -> list[str]:
    """What the film itself asks at its start: the questions its first act raises."""
    first = story.acts[0] if story.acts else None
    return [q for q in (clean_question(x) for x in (first.open_loops if first else ())) if q]


def open_questions(story: HostStory, cutoff: int) -> list[str]:
    """The questions acts 1..cutoff have raised and not answered."""
    if cutoff <= 0:
        return riddles(story)
    visible = story.visible(cutoff)
    answered = {clean_question(x).lower() for a in visible for x in a.resolved_loops}
    out: list[str] = []
    for act in visible:
        for loop in act.open_loops:
            question = clean_question(loop)
            if question and question.lower() not in answered and question not in out:
                out.append(question)
    return out


def visible_facts(story: HostStory, cutoff: int, cfg: HostConfig | None = None) -> list[dict]:
    """The evidence acts 1..cutoff tell, newest act first, at most `max_facts`."""
    cfg = cfg or ai_config.host
    out: list[dict] = []
    for act in reversed(story.visible(cutoff)):
        for eid in act.reveals:
            item = story.evidence.get(eid)
            if item:
                out.append(item)
    return out[:cfg.max_facts]


def segment_context(story: HostStory, seg: dict, cfg: HostConfig | None = None) -> dict:
    """What the story contributes to one appearance's prompts: nothing beyond its cutoff."""
    cfg = cfg or ai_config.host
    cutoff = int(seg["cutoff"])
    visible = story.visible(cutoff)
    return {
        "case": {"title": story.case_title},
        "story_so_far": [{"act": a.id, "title": a.title, "narration": a.text} for a in visible],
        "facts": visible_facts(story, cutoff, cfg),
        "open_questions": open_questions(story, cutoff),
    }


def _allowed_words(story: HostStory, cutoff: int, memory_text: str = "") -> set[str]:
    texts = [story.case_title, " ".join(riddles(story)), memory_text]
    texts += [a.text + " " + a.title for a in story.visible(cutoff)]
    texts += [i.get("claim") or i.get("contradiction") or ""
              for i in (story.evidence[e] for e in story.visible_evidence_ids(cutoff)
                        if e in story.evidence)]
    return {w for t in texts for w in words_of(t)}


def _fold(text: str) -> str:
    from app.language_pipeline.glossary import fold

    return f" {fold(text)} "


_CAPITALISED = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{3,}\b", re.M)


def later_only_terms(story: HostStory, cutoff: int, memory_text: str = "",
                     cfg: HostConfig | None = None) -> set[str]:
    """Specific terms (folded) that only a later act brings in: names and places of the
    glossary whose evidence the viewer has not heard and which no act up to the cutoff, no
    riddle and no allowed memory spells; numbers of three digits or more; and, for English,
    capitalised words. A host who says one of them knows something the viewer does not."""
    cfg = cfg or ai_config.host
    allowed = _allowed_words(story, cutoff, memory_text)
    allowed_text = _fold(" ".join([story.case_title, " ".join(riddles(story)), memory_text]
                                  + [a.text + " " + a.title for a in story.visible(cutoff)]))
    visible = set(story.visible_evidence_ids(cutoff))
    terms: set[str] = set()
    for entity in story.entities:
        if visible & set(entity.evidence_ids):
            continue
        for spelling in entity.spellings:
            folded = _fold(spelling)
            if folded.strip() and folded not in allowed_text:
                terms.add(folded.strip())
    hidden = " ".join(a.text for a in story.hidden(cutoff))
    terms |= {w for w in set(words_of(hidden)) if w.isdigit() and len(w) >= 3 and w not in allowed}
    if story.language in cfg.spoiler_name_case_languages:
        terms |= {w.lower() for w in _CAPITALISED.findall(hidden) if w.lower() not in allowed}
    return terms


def reveal_issues(story: HostStory, cutoff: int, dialogue: str, memory_text: str = "",
                  cfg: HostConfig | None = None) -> list[dict]:
    """Sentences of the host's words that use a detail only a later act tells."""
    terms = later_only_terms(story, cutoff, memory_text, cfg)
    issues: list[dict] = []
    for sentence in re.split(r"(?<=[.!?؟…])\s+", dialogue or ""):
        folded = _fold(sentence)
        if any(f" {t} " in folded for t in terms):
            issues.append({"type": "tells_what_comes_later", "quote": sentence.strip()[:200],
                           "fix": "This sentence uses something the viewer has not heard yet: drop it."})
    return issues
