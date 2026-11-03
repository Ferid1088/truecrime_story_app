"""The phonetic benchmark: audio clips of words, the pronunciations each might have, and every opinion about what was really said.

Where the clips come from: words cut out of a case's existing narration (nothing is generated for it), recordings made in the page and uploaded
files. What is known about a clip is kept in four separate levels: unlabeled; agent_consensus (independent agents and recognizers agree: NOT ground
truth); disputed; human_verified (a person listened: the only ground truth). Agent and recognizer opinions are kept as they are (kind, actor, choice,
confidence) and never averaged into one number; the person's label is what recognizers are scored against, and where a person has labelled a clip
that the agents agreed on, the benchmark measures how far agent consensus can be trusted."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import subprocess
from collections import Counter
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import PhoneticLabel, PhoneticSample
from app.documentary.reading import scheme, spelling
from app.documentary.reading.chooser import LETTERS, variants
from app.documentary.reading.words import key_of, tokenize

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data" / "phonetic_benchmark"
CATEGORIES = ("short_vowel", "ezafe", "colloquial", "name", "connected", "other")
QUOTAS = {"short_vowel": 70, "ezafe": 30, "colloquial": 30, "name": 30}
MAX_PER_BLOCK = 2
EDGE_S = 0.12
VOWELS = "aeo"
COLLOQUIAL_ENDINGS = ("esh", "ash", "eshun", "ashun", "emun", "amun", "etun", "atun", "un", "unā", "ā")


# --- the candidates of a word -----------------------------------------------------------------------------------------------------------

def candidate(latin: str) -> dict:
    return {"key": latin, "irab": spelling.persian(latin), "ipa": scheme.to_ipa(latin)}


def candidates_for(reading: str, category: str) -> list[dict]:
    """The pronunciations a clip of this word may have: the expected reading and the most likely wrong ones. For an ezafe the pair is the word with and
    without its ezafe; for the rest the expected reading and up to two forms with one short vowel changed."""
    stem, ezafe = scheme.split_ezafe(scheme.split_pause(reading)[0])
    if category == "ezafe" and ezafe:
        return [candidate(reading), candidate(stem)]
    forms = [reading]
    for v in variants(reading):
        changed = [a for a, b in zip(v, reading, strict=False) if a != b]
        if v not in forms and (not changed or changed[0] in VOWELS):
            forms.append(v)
    return [candidate(f) for f in forms[:3]]


def category_of(surface: str, reading: str, names: set[str]) -> str | None:
    if key_of(surface) in names:
        return "name"
    stem, ezafe = scheme.split_ezafe(scheme.split_pause(reading)[0])
    if ezafe:
        return "ezafe"
    if len(stem) >= 5 and stem.endswith(COLLOQUIAL_ENDINGS):
        return "colloquial"
    if len(stem) >= 4 and any(ch in VOWELS for ch in stem) and not reading.isupper() and reading != "NUM":
        return "short_vowel"
    return None


def names_of(reader: dict) -> set[str]:
    out: set[str] = set()
    for p in reader.get("people", []):
        out |= {key_of(p[k]["fa"]) for k in ("first", "last") if p.get(k, {}).get("fa")}
    for k in ("places", "foreign"):
        out |= {key_of(w) for item in reader.get(k, []) for w in item.get("fa", "").split()}
    return out


# --- samples ---------------------------------------------------------------------------------------------------------------------------

def cut(wav: Path, start: float, end: float, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, start - EDGE_S):.3f}", "-t", f"{end - start + 2 * EDGE_S:.3f}", "-i", str(wav), "-ac", "1", "-ar", "16000", str(target)],
                   check=True)


def add_sample(db: Session, **fields) -> tuple[PhoneticSample, bool]:
    found = db.query(PhoneticSample).filter_by(language=fields["language"], audio_path=fields["audio_path"]).first()
    if found:
        return found, False
    sample = PhoneticSample(**fields)
    db.add(sample)
    db.commit()
    return sample, True


def extract(db: Session, case_id: int, version_id: int, folder: Path, reading_cache: dict, language: str = "fa", seed: int = 11, quotas: dict | None = None) -> dict:
    """Cuts a diverse set of words out of a case's existing narration (no generation). The narration's blocks must have as many readings as words."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    blocks = {b["block_id"]: b for b in manifest["timeline"]["blocks"]}
    by_block: dict[str, list[dict]] = {}
    for w in manifest["timeline"]["words"]:
        by_block.setdefault(w["block_id"], []).append(w)
    names = names_of(reading_cache.get("reader", {}))
    pool: dict[str, list[tuple]] = {c: [] for c in QUOTAS}
    for block_id, spoken in by_block.items():
        readings = [r["latin"] for r in reading_cache["blocks"].get(block_id, {}).get("readings", [])]
        wav = ROOT / blocks[block_id]["wav"]
        meta = json.loads(wav.with_suffix(".json").read_text(encoding="utf-8"))
        tokens = tokenize(meta["text"])
        if not (len(tokens) == len(readings) == len(spoken)):
            continue
        sentence = " ".join(t.surface for t in tokens)
        for token, reading, w in zip(tokens, readings, spoken, strict=True):
            category = category_of(token.surface, reading, names)
            if category and 0.25 <= w["end"] - w["start"] <= 1.4:
                pool[category].append((block_id, token, reading, w, sentence, wav, meta))
    rng, made, created, skipped = random.Random(seed), Counter(), Counter(), 0
    for category, quota in (quotas or QUOTAS).items():
        rng.shuffle(pool[category])
        per_block: Counter = Counter()
        for block_id, token, reading, w, sentence, wav, meta in pool[category]:
            if made[category] >= quota:
                break
            if per_block[block_id] >= MAX_PER_BLOCK:
                continue
            path = f"clips/case{case_id}_v{version_id}_{block_id}_{token.n}.wav"
            cut(wav, w["start"] - blocks[block_id]["start"], w["end"] - blocks[block_id]["start"], BASE / path)
            try:
                cands = candidates_for(reading, category)
            except ValueError:                       # a reading the spelling scheme cannot write: nothing to compare against
                cands = []
            if len(cands) < 2:
                skipped += 1
                continue
            _, new = add_sample(db, language=language, source="case_audio", case_id=case_id, story_version_id=version_id, block_id=block_id, word_n=token.n, surface=token.surface,
                                sentence=sentence, audio_path=path, start_s=w["start"], end_s=w["end"], voice_id=meta.get("voice_id", ""), model_id=meta.get("model_id", ""),
                                speaker=meta.get("voice_id", ""), speed="film", category=category, candidates=json.dumps(cands, ensure_ascii=False), expected_key=cands[0]["key"])
            per_block[block_id] += 1
            made[category] += 1                      # a clip that exists already counts: a rerun reaches the same set
            created[category] += int(new)
    return {"created": dict(created), "available": {c: len(v) for c, v in pool.items()}, "skipped_without_candidates": skipped}


def add_recording(db: Session, *, data: bytes, suffix: str, surface: str, sentence: str, candidates: list[dict], category: str, speaker: str, speed: str, language: str = "fa",
                  expected_key: str = "") -> PhoneticSample:
    """A clip recorded in the page or uploaded: converted to 16 kHz mono and kept."""
    digest = hashlib.sha256(data).hexdigest()[:16]
    BASE.joinpath("uploads").mkdir(parents=True, exist_ok=True)
    raw = BASE / "uploads" / f"{digest}.upload{suffix}"
    raw.write_bytes(data)
    path = f"uploads/{digest}.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-ac", "1", "-ar", "16000", str(BASE / path)], check=True)
    raw.unlink()
    sample, _ = add_sample(db, language=language, source="recording", surface=surface, sentence=sentence, audio_path=path, speaker=speaker, speed=speed, category=category,
                           candidates=json.dumps(candidates, ensure_ascii=False), expected_key=expected_key or candidates[0]["key"])
    return sample


# --- opinions --------------------------------------------------------------------------------------------------------------------------

def record_label(db: Session, sample: PhoneticSample, *, kind: str, actor: str, choice: str, confidence: float = 0.0, detail: str = "") -> PhoneticLabel:
    keys = {c["key"] for c in json.loads(sample.candidates)}
    if choice not in keys | {"none", "unclear"}:
        raise ValueError(f"{choice!r} is not a candidate of this clip")
    label = PhoneticLabel(sample_id=sample.id, kind=kind, actor=actor, choice=choice, confidence=confidence, detail=detail)
    db.add(label)
    db.commit()
    return label


def latest(db: Session, sample: PhoneticSample) -> dict[str, PhoneticLabel]:
    """The newest opinion of every actor."""
    out: dict[str, PhoneticLabel] = {}
    for label in db.query(PhoneticLabel).filter_by(sample_id=sample.id).order_by(PhoneticLabel.id):
        out[label.actor] = label
    return out


def settle(db: Session, sample: PhoneticSample) -> PhoneticSample:
    """The status from the opinions. A person's label wins and is the only ground truth; otherwise at least two independent non-human opinions that all
    pick the same candidate make an agent consensus (not ground truth); different picks are disputed."""
    opinions = latest(db, sample)
    human = [o for o in opinions.values() if o.kind == "human"]
    if human:
        choice = human[-1].choice
        sample.human_key, sample.status = (None if choice == "unclear" else choice), ("unclear" if choice == "unclear" else "human_verified")
    else:
        machine = [o.choice for o in opinions.values() if o.kind in ("agent", "recognizer")]
        decided = [c for c in machine if c != "unclear"]
        if len(machine) < 2:
            sample.status, sample.consensus_key = "unlabeled", None
        elif len(decided) >= 2 and len(set(decided)) == 1 and len(decided) == len(machine):
            sample.status, sample.consensus_key = "agent_consensus", decided[0]
        else:
            sample.status, sample.consensus_key = "disputed", None
    db.commit()
    return sample


def human_label(db: Session, sample_id: int, choice: str, reviewer: str) -> PhoneticSample:
    if not reviewer.strip():
        raise PermissionError("a label needs the name of the person who listened")
    sample = db.get(PhoneticSample, sample_id)
    record_label(db, sample, kind="human", actor=f"human:{reviewer}", choice=choice, confidence=1.0)
    return settle(db, sample)


# --- the agents and the recognizers ----------------------------------------------------------------------------------------------------

def audio_data_url(sample: PhoneticSample) -> str:
    """The clip as mp3 (the format the listening agent is given everywhere else)."""
    mp3 = subprocess.run(["ffmpeg", "-v", "error", "-i", str(BASE / sample.audio_path), "-b:a", "64k", "-f", "mp3", "-"], capture_output=True, check=True).stdout
    return "data:audio/mpeg;base64," + base64.b64encode(mp3).decode()


def agent_runner(db: Session, case_id: int):
    """The listening agent as the benchmark's runner: tracked and costed on the case, and made again when the provider hiccups."""
    from app.agents.runner import run_agent
    from app.documentary.reading.ask import ask

    async def runner(payload: dict, audio: str) -> dict:
        return await ask(db, case_id, "Phonetic benchmark: listen", payload, "reading_listener",
                         lambda gen, body: run_agent("documentary.reading.choose", gen, body, images=[audio]))
    return runner


def wav2vec2_phones(path: Path) -> list[str]:
    from app.documentary.pronunciation import get_listener

    return [t for t, _, _ in get_listener().frames(str(path))]


async def agent_opinions(db: Session, sample: PhoneticSample, runner, rounds: int = 2) -> list[PhoneticLabel]:
    """Independent listening agents choose which candidate they hear; each round shows the options in another order and letter."""
    cands = json.loads(sample.candidates)
    out = []
    for r in range(rounds):
        order = sorted(cands, key=lambda c: hashlib.sha256(f"{sample.id}:{r}:{c['key']}".encode()).hexdigest())
        options = dict(zip(LETTERS, [c["key"] for c in order], strict=False))
        answer = await runner({"word": sample.surface, "options": options}, audio_data_url(sample))
        choice = options.get(str(answer.get("choice") or "none"), "none")
        confidence = 0.8 if answer.get("sure", True) else 0.4
        out.append(record_label(db, sample, kind="agent", actor=f"listener-{r + 1}", choice="unclear" if choice == "none" else choice, confidence=confidence,
                                detail=json.dumps(answer, ensure_ascii=False)))
    return out


def edit_distance_to_stretch(candidate: list[str], heard: list[str]) -> int:
    prev = [0] * (len(heard) + 1)
    for i, c in enumerate(candidate, 1):
        cur = [i] + [0] * len(heard)
        for j, h in enumerate(heard, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (c != h))
        prev = cur
    return min(prev)


STRIP = set("ʰ̪̃ː ˈˌ.̥ʲ‿")
SAME = {"ɑ": "ɒ", "a": "æ", "ɐ": "æ", "ɛ": "e", "ɪ": "i", "ʊ": "u", "ɾ": "r", "ɹ": "r", "ɡ": "g", "ɦ": "h"}


def normalise(phones: list[str]) -> list[str]:
    out = []
    for p in phones:
        p = "".join(ch for ch in p if ch not in STRIP)
        if p:
            out.append(SAME.get(p, p))
    return out


def recognizer_opinion(db: Session, sample: PhoneticSample, name: str, phones: list[str]) -> PhoneticLabel:
    """What a phoneme recognizer heard, compared with each candidate: the nearer one, or `unclear` when two are equally near. Not a forced alignment."""
    heard = normalise(phones)
    distances = {c["key"]: edit_distance_to_stretch(normalise(list(c["ipa"])), heard) for c in json.loads(sample.candidates)}
    best = min(distances.values())
    winners = [k for k, d in distances.items() if d == best]
    return record_label(db, sample, kind="recognizer", actor=name, choice=winners[0] if len(winners) == 1 else "unclear",
                        confidence=0.0 if len(winners) > 1 else round(min(1.0, (sorted(distances.values())[1] - best) / max(1, best + 1)), 2),
                        detail=json.dumps({"heard": " ".join(heard), "distances": distances}, ensure_ascii=False))


async def propose_all(db: Session, runner, recognizers: dict, status: str = "unlabeled", limit: int | None = None, language: str = "fa") -> dict:
    """Agents and recognizers give their opinions on the clips that have none; the consensus is settled. Costs agent calls only."""
    samples = db.query(PhoneticSample).filter_by(language=language, status=status).order_by(PhoneticSample.id).all()[:limit]
    for s in samples:
        await agent_opinions(db, s, runner)
        for name, recognize in recognizers.items():
            await asyncio.to_thread(lambda s=s, n=name, r=recognize: recognizer_opinion(db, s, n, r(BASE / s.audio_path)))
        settle(db, s)
    return report(db, language)


# --- the report ------------------------------------------------------------------------------------------------------------------------

def report(db: Session, language: str = "fa") -> dict:
    samples = db.query(PhoneticSample).filter_by(language=language).all()
    counts = Counter(s.status for s in samples)
    humans = [s for s in samples if s.status == "human_verified"]
    by_actor: dict[str, list[int]] = {}
    consensus_vs_human = [0, 0]
    for s in humans:
        for actor, label in latest(db, s).items():
            if label.kind != "human":
                by_actor.setdefault(actor, [0, 0, 0])
                by_actor[actor][0] += 1
                by_actor[actor][1] += int(label.choice == s.human_key)
                by_actor[actor][2] += int(label.choice == "unclear")
        machine = [o.choice for o in latest(db, s).values() if o.kind != "human"]
        if len(machine) >= 2 and len(set(machine)) == 1 and machine[0] != "unclear":
            consensus_vs_human[0] += 1
            consensus_vs_human[1] += int(machine[0] == s.human_key)
    return {"counts": dict(counts), "total": len(samples), "by_category": dict(Counter(s.category for s in samples)), "by_source": dict(Counter(s.source for s in samples)),
            "human_verified": len(humans),
            "against_human_labels": {a: {"clips": v[0], "right": v[1], "unclear": v[2], "accuracy": round(v[1] / v[0], 3) if v[0] else None} for a, v in by_actor.items()},
            "consensus_checked_by_a_person": {"clips": consensus_vs_human[0], "consensus_right": consensus_vs_human[1]},
            "enough_for_a_verdict": len(humans) >= 60}


def describe(db: Session, sample: PhoneticSample) -> dict:
    """A clip for the page: what is known, and every opinion with who gave it and what kind it is."""
    return {"id": sample.id, "surface": sample.surface, "sentence": sample.sentence, "category": sample.category, "source": sample.source, "speaker": sample.speaker,
            "speed": sample.speed, "status": sample.status, "candidates": json.loads(sample.candidates), "expected_key": sample.expected_key,
            "consensus_key": sample.consensus_key, "human_key": sample.human_key, "case_id": sample.case_id, "block_id": sample.block_id,
            "opinions": [{"kind": o.kind, "actor": o.actor, "choice": o.choice, "confidence": o.confidence} for o in latest(db, sample).values()]}


PRIORITY = {"disputed": 0, "agent_consensus": 1, "unlabeled": 2, "unclear": 3, "human_verified": 4}


def listing(db: Session, language: str = "fa", status: str | None = None, category: str | None = None, limit: int = 60) -> dict:
    """The clips to look at, the disputed ones first (where an agent cannot settle it, a person's ear is needed), and the report."""
    query = db.query(PhoneticSample).filter_by(language=language)
    for column, value in ((PhoneticSample.status, status), (PhoneticSample.category, category)):
        if value:
            query = query.filter(column == value)
    rows = sorted(query.all(), key=lambda s: (PRIORITY.get(s.status, 9), s.id))[:limit]
    return {"samples": [describe(db, s) for s in rows], "report": report(db, language)}
