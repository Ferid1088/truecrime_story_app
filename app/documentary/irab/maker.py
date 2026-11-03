"""The second text of a film: every word of every block vowelized (irab) in its sentence by two different models that do not see each other's
answer, from the English source, the Persian text and the meaning of each word. Words they agree on are accepted; the words they disagree on go
to a third decision by the sentence's meaning and grammar, and what that cannot settle is marked uncertain (the first answer is kept) for the owner.
The result keeps the original text, the fully vowelized text (the second text: the reference the voice is checked against) and the text the voice is
given (the original with only the spoken forms put in: a text full of marks breaks the flow of the voice), word by word."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt_sha256
from app.documentary.irab import rules as R
from app.documentary.reading.ask import Runner, ask
from app.documentary.reading.block import BlockInput
from app.documentary.reading.words import substitute

PROMPTS = ("write", "arbitrate")
EZAFE_MARKS = "ِٔ"


@dataclass
class IrabWord:
    n: int
    surface: str
    irab: str
    said: str = ""                      # the word as spoken in Persian letters when marks on its letters cannot show it
    status: str = "agreed"              # agreed | resolved | single (one editor kept the rules) | uncertain
    other: dict = field(default_factory=dict)    # the other editor's answer when they differed



@dataclass
class IrabBlock:
    block_id: str
    original: str
    irab_text: str
    voice_text: str                     # what the voice is given: the original with only the spoken forms put in (numbers, names, sounds the letters do not show)
    words: list[IrabWord]

    @property
    def uncertain(self) -> list[IrabWord]:
        return [w for w in self.words if w.status in ("uncertain", "single")]


def sha(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def context(item: BlockInput, only: list[int] | None = None) -> dict:
    """What an editor is given: the block as a whole, and the numbered words to answer (all of them, or only those an earlier answer got wrong)."""
    wanted = [w for w in item.words if only is None or w.n in only]
    return {"english_source": item.english, "previous": item.previous, "next": item.next, "block_text": item.sentence(),
            "words": [{"n": w.n, "text": w.plain} for w in wanted]}


class IrabMaker:
    def __init__(self, db: Session, case_id: int, version_id: int, language: str,
                 write_runner: Runner | None = None, check_runner: Runner | None = None, arbiter_runner: Runner | None = None):
        self.db, self.case_id = db, case_id
        self.runners = (write_runner or (lambda gen, body: run_agent("documentary.irab.write", gen, body)),
                        check_runner or (lambda gen, body: run_agent("documentary.irab.check", gen, body)),
                        arbiter_runner or (lambda gen, body: run_agent("documentary.irab.arbitrate", gen, body)))
        folder = Path(ai_config.voice.work_dir)
        from app.documentary.voice_render import ROOT

        self.path = (folder if folder.is_absolute() else ROOT / folder) / str(case_id) / ai_config.irab.folder / f"{language}_v{version_id}.json"
        try:
            self.cache: dict = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")

    async def edit(self, item: BlockInput, runner: Runner, role: str, label: str) -> tuple[dict[int, tuple[str, str]], dict[int, str]]:
        """One editor's answer for the words of the block: ({n: (irab, said)} of the words that keep the rules, {n: why} of the words that do not).
        A word that breaks a rule (a letter changed, a number without its spoken form) is asked again, alone, with the reason."""
        answers: dict[int, tuple[str, str]] = {}
        wrong: dict[int, str] = {w.n: "not answered" for w in item.words}
        for _ in range(ai_config.irab.max_retries + 1):
            if not wrong:
                break
            ask_for = sorted(wrong)
            payload = {**context(item, ask_for), **({"problems": [f"word {n}: {why}" for n, why in wrong.items() if why != "not answered"]} if any(
                why != "not answered" for why in wrong.values()) else {})}
            raw = await ask(self.db, self.case_id, f"Irab: {label} {item.block_id}", payload, role, runner)
            got = {e["n"]: e for e in (raw.get("words") if isinstance(raw, dict) else None) or [] if isinstance(e, dict) and "n" in e}
            for word in (w for w in item.words if w.n in ask_for):
                entry = got.get(word.n)
                if entry is None:
                    wrong[word.n] = "not answered"
                    continue
                irab, said = R.clean(str(entry.get("irab") or "")), R.clean(str(entry.get("said") or "").replace("،", " "))
                why = R.problem_with(word, irab, said)
                if why:
                    wrong[word.n] = why
                else:
                    answers[word.n] = (R.canonical(irab), R.canonical(said))
                    wrong.pop(word.n, None)
        return answers, wrong

    async def decide(self, item: BlockInput, disputed: list[int], first: dict, second: dict) -> dict[int, dict]:
        """The decision between the two answers for the disputed words, a few words per call (a long answer is cut off by the model's limit)."""
        out: dict[int, dict] = {}
        step = ai_config.irab.arbitrate_words_per_call
        for start in range(0, len(disputed), step):
            options = [{"n": n, "word": item.words[n].plain, "A": {"irab": first[n][0], "said": first[n][1]}, "B": {"irab": second[n][0], "said": second[n][1]}}
                       for n in disputed[start:start + step]]
            raw = await ask(self.db, self.case_id, f"Irab: arbitrate {item.block_id}", {"english_source": item.english, "text": item.sentence(), "words": options},
                            "reading_arbiter", self.runners[2])
            out.update({e["n"]: e for e in raw.get("words", []) if isinstance(e, dict) and "n" in e})
        return out

    async def block(self, item: BlockInput, gate: asyncio.Semaphore) -> IrabBlock:
        key = sha(context(item), [prompt_sha256(f"documentary/irab/{p}_system") for p in PROMPTS])
        saved = self.cache.get(item.block_id)
        if saved and saved["key"] == key:
            return IrabBlock(item.block_id, saved["original"], saved["irab_text"], saved["voice_text"], [IrabWord(**w) for w in saved["words"]])
        async with gate:
            first, bad_first = await self.edit(item, self.runners[0], "reading_editor", "write")            # one after the other: the calls share a session
            second, bad_second = await self.edit(item, self.runners[1], "reading_checker", "check")
            disputed = [w.n for w in item.words if w.n in first and w.n in second and first[w.n] != second[w.n]]
            decided = await self.decide(item, disputed, first, second) if disputed else {}
        words: list[IrabWord] = []
        for w in item.words:
            if w.n not in first and w.n not in second:      # neither editor could answer within the rules: the word stays as written, for the owner
                words.append(IrabWord(w.n, w.plain, w.plain, "", "uncertain", {"why": bad_first.get(w.n, "") or bad_second.get(w.n, "")}))
                continue
            if w.n not in first or w.n not in second:        # only one editor kept the rules for it
                irab, said = first.get(w.n) or second[w.n]
                status, other = "single", {}
            else:
                irab, said = first[w.n]
                status, other = "agreed", {}
                if w.n in disputed:
                    pick = decided.get(w.n, {})
                    other = {"irab": second[w.n][0], "said": second[w.n][1]}
                    if pick.get("sure") and pick.get("choice") == "B":
                        irab, said = second[w.n]
                        other = {"irab": first[w.n][0], "said": first[w.n][1]}
                        status = "resolved"
                    elif pick.get("sure") and pick.get("choice") == "A":
                        status = "resolved"
                    else:
                        status = "uncertain"
            words.append(IrabWord(w.n, w.plain, irab, said, status, other))
        irab_text = substitute(item.text, {x.n: x.irab for x in words})
        voice_text = substitute(item.text, {x.n: x.said for x in words if x.said})
        done = IrabBlock(item.block_id, item.text, irab_text, voice_text, words)
        self.cache[item.block_id] = {"key": key, "original": item.text, "irab_text": irab_text, "voice_text": voice_text, "words": [asdict(x) for x in words]}
        self.save()
        return done

    async def run(self, items: list[BlockInput]) -> dict[str, IrabBlock]:
        gate = asyncio.Semaphore(ai_config.irab.parallel_blocks)
        done = await asyncio.gather(*(self.block(i, gate) for i in items))
        return {b.block_id: b for b in done}
