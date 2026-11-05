"""Controlled recordings for validating the acoustic review (config/benchmarks/*.json). Every item is a new sentence voiced twice by the production
voice and settings: the target word given once as the right reading and once as a typical error, both as inline IPA, so that nothing but the target
differs. The word is cut out by the voice's own timing and becomes a benchmark clip (purpose validation) with both readings as candidates, the
expected reading, and the reading the voice was told to say (`intended_key`), which the person who labels never sees before labelling: the voice may
not have obeyed, so only the person's ear is the label. Billable (ElevenLabs): the run is capped and every request counted; a take already made is
never paid again."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.documentary import phonetic_benchmark as PB
from app.documentary.reading import scheme
from app.documentary.reading.words import tokenize
from app.providers.voice import VoiceRequest

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Item:
    id: str
    category: str
    sentence: str
    word: str
    right: str
    wrong: str


def load(name: str) -> tuple[str, list[Item]]:
    data = json.loads((ROOT / "config" / "benchmarks" / f"{name}.json").read_text(encoding="utf-8"))
    return data["name"], [Item(**i) for i in data["items"]]


def ipa(reading: str) -> str:
    return " ".join(scheme.to_ipa(part) for part in reading.split())


def request_text(item: Item, reading: str) -> tuple[str, int, int]:
    """The sentence with the target word as /IPA/, and where the IPA stands in it."""
    word = next(w for w in tokenize(item.sentence) if w.surface == item.word)
    form = f"/{ipa(reading)}/"
    text = item.sentence[:word.start] + form + item.sentence[word.end:]
    return text, word.start, word.start + len(form)


def request(item: Item, reading: str) -> VoiceRequest:
    lang = ai_config.voice.for_language("fa")
    return VoiceRequest(text=request_text(item, reading)[0], voice_id=lang.voice_id, model_id=lang.model_id,
                        settings=ai_config.voice.settings_for(ai_config.voice.default_style, "fa"), seed=ai_config.voice.seed, language="fa",
                        language_code=lang.language_code)


async def build(db: Session, name: str, renderer, case_id: int | None) -> dict:
    """Voices every item twice (the renderer's take cache keeps a made take from being paid again) and adds each target word as a clip."""
    set_name, items = load(name)
    folder = PB.BASE / "controlled" / set_name
    takes_dir = folder / "takes"
    takes_dir.mkdir(parents=True, exist_ok=True)
    added = []
    for item in items:
        for variant, reading in (("right", item.right), ("wrong", item.wrong)):
            req = request(item, reading)
            take = await renderer._take({"block_id": f"{item.id}_{variant}"}, req, takes_dir, "fa")
            al = take["alignment"]
            _, c0, c1 = request_text(item, reading)
            spoken = [i for i in range(c0, c1) if i < len(al["characters"])]
            if not spoken:
                continue
            start, end = al["starts"][spoken[0]], al["ends"][spoken[-1]]
            rel = f"controlled/{set_name}/{item.id}_{variant}.wav"
            PB.cut(Path(take["raw_path"]), start, end, PB.BASE / rel)
            candidates = [{"key": r, "irab": "", "ipa": ipa(r)} for r in (item.right, item.wrong)]
            sample, new = PB.add_sample(db, language="fa", source="controlled", case_id=case_id, block_id=f"{item.id}_{variant}", surface=item.word,
                                        sentence=item.sentence, audio_path=rel, start_s=start, end_s=end, voice_id=req.voice_id,
                                        model_id=req.model_id, speaker=req.voice_id, speed="voice 1.2",
                                        category=item.category, candidates=json.dumps(candidates, ensure_ascii=False), expected_key=item.right,
                                        purpose="validation", intended_key=reading)
            added.append({"id": sample.id, "item": item.id, "variant": variant, "new": new, "cache_hit": take["cache_hit"]})
    return {"set": set_name, "clips": len(added), "new_clips": sum(a["new"] for a in added), "paid_takes": sum(not a["cache_hit"] for a in added),
            "folder": str(folder), "samples": added}


def read_aloud(phones: str) -> bool:
    """Whether the recognizer heard "slash" (config ppie_review.notation_patterns): the voice read the notation aloud."""
    return any(n in phones for n in ai_config.ppie_review.notation_patterns)


def flag_defective(db: Session, listener, set_name: str) -> list[int]:
    """Marks the controlled clips whose take read the IPA notation aloud ("slash ...") as defective recordings: they say nothing about a vowel and
    never count in the validation (they stay as observations of the voice). A machine check of the recording, not a label of what was said."""
    from app.db.models import PhoneticSample

    found = []
    for s in db.query(PhoneticSample).filter(PhoneticSample.source == "controlled", PhoneticSample.audio_path.like(f"controlled/{set_name}/%")):
        if read_aloud(" ".join(t for t, _, _ in listener.frames(str(PB.BASE / s.audio_path)))):
            s.purpose = "defective_recording"
            found.append(s.id)
    db.commit()
    return found

