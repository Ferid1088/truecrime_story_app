"""The angle of one host appearance: what Fereidoun has to say at this point of the story, decided before a word of it is written.

A host that is given only the story and a persona writes abstract talk about "evidence" and "the truth", the same in every language, and at the end of a solved
case it asks who did it. The angle fixes that in code before the wording: the state of the story at the appearance (how far the viewer is, what the story has
settled, what is still open), and ONE concrete moment the host reacts to. The moment is quoted from the narration of the language the host speaks, and code checks
that the quote stands in the part of the story the viewer has heard and in no earlier appearance's angle: so it is concrete, it cannot come from the future, it is
never a made-up detail, and its words are the language's own (no translated detail). The writer then says it in its own words (the English or any other text of
the host is never an input of another language)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.ai_config import ai_config
from app.documentary.host_firewall import open_questions
from app.documentary.host_story import HostStory

PHASES = ("opening", "early", "middle", "late", "ended")
_MARKS = re.compile(r"[\W_]+", re.UNICODE)
_INVISIBLE = re.compile("[‌‍ً-ٰٟ]")       # half-space, joiners, Arabic and Persian vowel marks


def story_state(story: HostStory, cutoff: int) -> dict:
    """Where the viewer stands: the share of the story heard, the phase, what the story has settled (the questions its acts answered) and what is open, and
    whether the story is over (an outro speaks from the end: nothing of its central question is open unless the list says so)."""
    total = story.total_words() or 1
    heard = sum(a.words for a in story.visible(cutoff)) / total
    ended = cutoff >= len(story.acts) > 0
    phase = "opening" if cutoff <= 0 else "ended" if ended else "early" if heard < 1 / 3 else "middle" if heard < 2 / 3 else "late"
    visible = story.visible(cutoff)
    return {"phase": phase, "share_heard": round(heard, 2), "story_over": ended, "payoff_passed": any(a.payoff for a in visible),
            "settled": [q for a in visible for q in a.resolved_loops], "open_questions": open_questions(story, cutoff),
            "last_beats": [{"act": a.id, "title": a.title} for a in visible[-3:]]}


def normalized(text: str) -> str:
    """A text for comparing quotes: no vowel marks, half-spaces or punctuation, one case, one space."""
    return " ".join(_MARKS.sub(" ", _INVISIBLE.sub("", text or "").lower()).split())


FIELDS = ("moment", "quote", "feeling", "thought", "turn", "move", "question", "dimension")


@dataclass
class Angle:
    moment: str                  # the one concrete thing the host points at, in English (the film's working language): the host does not retell it
    quote: str                   # that thing as the narration of the host's language says it (verbatim): to find the right words, never to repeat
    feeling: str                 # what it does to the host, one personal reaction
    thought: str                 # the host's OWN take: an opinion or a hunch the host holds about it and can defend with what the viewer heard
    turn: str                    # a second thought that complicates the first: a doubt about the host's own take, a comparison from ordinary life, a link to another beat
    move: str                    # how the host talks about it (one of the persona's interaction moves)
    question: str                # the question for the viewer about that moment ("" when the role asks for none)
    dimension: str = ""          # the one or two sides of the host's character that show

    def describe(self) -> dict:
        return {name: getattr(self, name) for name in FIELDS}


def parse(raw: dict) -> Angle:
    raw = raw if isinstance(raw, dict) else {}
    return Angle(*(str(raw.get(k) or "").strip() for k in FIELDS))


def issues(angle: Angle, story: HostStory, seg: dict, earlier: list[Angle], cfg=None) -> list[str]:
    """What is wrong with an angle (empty when it is good): a missing part, a quote that is not in the narration the viewer has heard, a quote too short to be a
    moment, a moment an earlier appearance already used, a question the role does not want or lacks."""
    cfg = cfg or ai_config.host
    opening = seg["position"] == "opening"           # the opening has heard nothing: its moment comes from the case and its riddle, not from a quote
    needed = ("moment", "feeling", "thought", "move") + (() if opening else ("quote",)) + (("turn",) if seg["target_seconds"] >= cfg.angle_turn_from_seconds else ())
    out = [f"{name} is empty" for name in needed if not getattr(angle, name)]
    if not opening and angle.quote:
        wanted = normalized(angle.quote)
        if len(wanted.split()) < cfg.angle_min_quote_words:
            out.append(f"the quote has fewer than {cfg.angle_min_quote_words} words: quote the moment itself, word for word")
        elif wanted not in normalized(" ".join(a.text for a in story.visible(int(seg["cutoff"])))):
            out.append("the quote is not word for word in the narration the viewer has heard before this appearance: copy it exactly from story_so_far")
        if any(wanted and wanted in normalized(e.quote) for e in earlier if e.quote):
            out.append("an earlier appearance of this film already reacts to this moment: choose another")
    if cfg.roles[seg["role"]].requires_question and not angle.question:
        out.append("this role needs a question for the viewer about the moment")
    return out


def retelling_issues(dialogue: str, story: HostStory, cutoff: int, cfg=None) -> list[dict]:
    """The host never repeats the narration: a run of `narration_copy_words` words (or more) of the dialogue that stands word for word in the narration the
    viewer has heard is the narration's sentence, not the host's. Names and facts may come back in the host's own words."""
    cfg = cfg or ai_config.host
    n = cfg.narration_copy_words
    heard = normalized(" ".join(a.text for a in story.visible(cutoff))).split()
    runs = {tuple(heard[i:i + n]) for i in range(len(heard) - n + 1)}
    mine = normalized(dialogue).split()
    for i in range(len(mine) - n + 1):
        if tuple(mine[i:i + n]) in runs:
            return [{"type": "repeats_the_narration", "quote": " ".join(mine[i:i + n])}]
    return []
