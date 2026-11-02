"""The semantic audio review of the Persian voice.

A model LISTENS to the audio of a voiced block (and later to the voice of the avatar video made from it) and judges it against the source text:
meaning, skipped or added phrases, dates, numbers, names, URLs, paths, technical terms, pronunciation and delivery. It runs after the existing
voice-repair loop (speech-to-text, pronunciation key, retakes), which stays as it is. A block that fails goes back to that loop with a
correction of what the voice is given (its text, a respelled word, an audio tag); the viewer's text never changes. After the allowed
corrections a block that still fails is marked failed and stops the film. Every block keeps a record of what was heard and decided."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt_sha256
from app.documentary import audio as A
from app.documentary import lexicon as LEX
from app.documentary.persian_numbers import NUMBER
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)

ERROR_TYPES = {"meaning", "omission", "addition", "date", "number", "name", "url", "path", "technical_term", "pronunciation", "delivery"}
BLOCKING = {"critical", "major"}
TAG = re.compile(r"\[[^\[\]]*\]\s*")
NOTATION_ALOUD = re.compile(r"slash|اسلش", re.IGNORECASE)
URL = re.compile(r"https?://\S+|www\.\S+")
PATH = re.compile(r"(?<![\w/])(?:/[\w.\-]+){1,}/?")
SENTENCE_END = re.compile(r"(?<=[.!?؟…])\s+")
TASK_WORD = "Listen to the clip and answer with the JSON described in your instructions. The input follows.\n"
PUNCT = ".,;:!?؟،؛«»\"'()[]…"
MARKS_ONLY = re.compile("[\u064b-\u0652\u0670\u0654]")
VOWEL_FINAL = "اآویئه"                 # a word ending in one of these takes "ye" as its ezafe, any other word takes a short "e"
EZAFE_OPTIONS = [("short_e", "one short 'e' sound right after the word"),
                 ("extra_ye", "a short 'e' followed by an extra 'y' glide, as in '-eye'"),
                 ("ye_glide", "a 'ye' sound right after the word (the ezafe of a word that ends in a vowel)"),
                 ("doubled_consonant", "the last consonant of the word is lengthened or doubled before the ezafe"),
                 ("no_ezafe", "no ezafe sound at all after the word"),
                 ("cannot_tell", "the clip is too short, noisy or cut off to tell")]
READING_OPTIONS = [("as_expected", "the word sounds like the expected reading"),
                   ("different", "the word sounds clearly different from the expected reading"),
                   ("cannot_tell", "the clip is too short, noisy or cut off to tell")]
TASK = "Review the audio and answer with the JSON described in your instructions. The input follows.\n"


class SemanticReviewFailed(RuntimeError):
    """Blocks whose audio still fails the review after the allowed corrections (or a review that could not be made)."""

    def __init__(self, failed: list[dict]):
        self.failed = failed
        super().__init__("Semantic audio review failed for " + ", ".join(f"{f['block_id']} ({f['reason']})" for f in failed[:12])
                         + (f" and {len(failed) - 12} more" if len(failed) > 12 else ""))


def required(language: str) -> bool:
    return language in ai_config.semantic_review.languages


def protected_items(*texts: str) -> list[str]:
    """Dates, numbers, URLs and paths of the texts, which must come out exactly right."""
    found: list[str] = []
    for text in texts:
        for pattern in (URL, PATH, NUMBER):
            found += [m.group().rstrip(".,;:") for m in pattern.finditer(text or "")]
    return list(dict.fromkeys(found))


def normalise(raw: dict) -> dict:
    """The reviewer's answer in its fixed shape. A pass needs the model's pass, no critical or major error and enough confidence."""
    errors = []
    for e in raw.get("errors") or []:
        if isinstance(e, dict):
            errors.append({"type": e.get("type") if e.get("type") in ERROR_TYPES else "meaning",
                           "severity": e.get("severity") if e.get("severity") in {"critical", "major", "minor"} else "major",
                           "expected": str(e.get("expected") or ""), "heard": str(e.get("heard") or ""), "reason": str(e.get("reason") or "")})
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.0
    for check in raw.get("word_checks") or []:           # a word the reviewer was told to listen to and found wrong is an error even when it left it out of `errors`
        if isinstance(check, dict) and (check.get("ok") is False or (check.get("joined") is False and len(str(check.get("word") or "").split()) > 1)) and not any(check.get("word") and check["word"] in (e["expected"] + e["heard"]) for e in errors):
            errors.append({"type": "pronunciation", "severity": "major", "expected": str(check.get("word") or ""), "heard": str(check.get("heard") or ""),
                           "reason": str(check.get("problem") or ("a pause inside the name: it is not said as one unit" if check.get("joined") is False
                                                                  else "the word is not said right"))})
    passed = (raw.get("verdict") == "pass" and not any(e["severity"] in BLOCKING for e in errors)
              and confidence >= ai_config.semantic_review.min_confidence)
    rules = [{"surface": str(r["surface"]), "replacement": str(r["replacement"])} for r in raw.get("corrected_pronunciation_rules") or []
             if isinstance(r, dict) and r.get("surface") and r.get("replacement")]
    text, delivery = raw.get("corrected_text"), raw.get("corrected_delivery_instruction")
    missing = [{"phrase": str(m["phrase"]), "head": str(m["head"]), "heard": str(m.get("heard") or "")} for m in raw.get("ezafe_missing") or []
               if isinstance(m, dict) and m.get("phrase") and m.get("head")]
    for m in missing:            # a place where grammar needs an ezafe that is not said is an error
        errors.append({"type": "pronunciation", "severity": "major", "expected": m["phrase"] + " (with the ezafe)", "heard": m["heard"],
                       "reason": f"the ezafe after {m['head']} is not said"})
    passed = passed and not missing
    aloud = bool(raw.get("notation_read_aloud")) or any(NOTATION_ALOUD.search(e["heard"]) for e in errors)     # the voice reads a pronunciation rule out as symbols
    return {"verdict": "pass" if passed else "fail", "confidence": confidence, "errors": errors, "ezafe_missing": missing, "notation_read_aloud": aloud,
            "corrected_text": text if isinstance(text, str) and text.strip() else None, "corrected_pronunciation_rules": rules,
            "corrected_delivery_instruction": delivery if isinstance(delivery, str) and delivery in ai_config.semantic_review.delivery_tags else None}


EZAFE = re.compile(r"[\w\u200c]+(?:\u0650|\u0654|\u06c0|\u0647\u0654)(?=\s)")


def tokens_of(text: str) -> list[str]:
    """The words of a text as the voice's word timing counts them: audio tags and pure punctuation are not words."""
    return [t.strip(PUNCT) for t in TAG.sub(" ", text).split() if any(ch.isalnum() for ch in t)]


def locate(text: str, words: list[dict], target: str, keep_marks: bool) -> list[tuple[int, int]]:
    """Where `target` (one word or a name of several) is in the voiced text, as indexes into the voice's word timing. Nothing is guessed when the
    text and the timing do not have the same number of words. An ezafe target must match with its marks, so that the same word without an
    ezafe elsewhere in the block is not taken for it."""
    tokens = tokens_of(text)
    if len(tokens) != len(words):
        return []
    norm = (lambda s: s) if keep_marks else (lambda s: MARKS_ONLY.sub("", s).replace("\u200c", ""))
    want = [norm(w.strip(PUNCT)) for w in target.split()]
    return [(i, i + len(want) - 1) for i in range(len(tokens) - len(want) + 1) if [norm(t) for t in tokens[i:i + len(want)]] == want]


def word_options(kind: str) -> list[tuple[str, str]]:
    return EZAFE_OPTIONS if kind == "ezafe" else READING_OPTIONS


def expected_choice(kind: str, word: str) -> str:
    if kind != "ezafe":
        return "as_expected"
    return "ye_glide" if MARKS_ONLY.sub("", word).rstrip("\u200c")[-1:] in VOWEL_FINAL else "short_e"


def words_to_check(tts_text: str, rules: list[dict]) -> list[str]:
    """The words the reviewer must listen to one by one: those that carry an ezafe mark (the voice may add a "ye" or drop the "e") and those with a
    pronunciation rule of the film."""
    found = [m.group() for m in EZAFE.finditer(tts_text)]
    found += [r["string_to_replace"] for r in rules if whole_word(r["string_to_replace"]).search(tts_text)]
    return list(dict.fromkeys(found))


def whole_word(surface: str) -> re.Pattern:
    return re.compile(rf"(?<![\w‌]){re.escape(surface)}(?![\w‌])")


def without_dictionary(block: dict, rules: list[dict]) -> tuple[dict, list[str]]:
    """The block for a voice that reads the pronunciation notation aloud ("slash F-E-V-R-I-G-E slash"): it goes to the voice without the film's
    dictionary, the words whose rule is a respelling are written as that respelling in the voice text, and when it happens again the audio tags
    go too. What people read is not touched."""
    sentences = [dict(s) for s in block.get("sentences") or []]
    forms = [s.get("tts") or s["speech"] for s in sentences]
    applied = ["without the dictionary"]
    for rule in rules:
        if rule.get("type") == "alias" and rule.get("alias") and any(whole_word(rule["string_to_replace"]).search(f) for f in forms):
            pattern = whole_word(rule["string_to_replace"])
            forms = [pattern.sub(rule["alias"], f) for f in forms]
            applied.append(f"respelled {rule['string_to_replace']}")
    if block.get("no_dictionary"):
        forms = [TAG.sub("", f).strip() for f in forms]
        applied.append("without audio tags")
    out = {**block, "no_dictionary": True, "sentences": [{**s, "tts": f} for s, f in zip(sentences, forms)]}
    out["tts_text"] = " ".join(forms)
    return out, applied


def corrected_block(block: dict, review: dict) -> tuple[dict, list[str], list[dict]]:
    """The block with the reviewer's corrections applied to what the voice is given (the sentences' tts forms), what was applied, and the
    respellings among it ({"surface", "replacement"}). The
    sentences' speech and display text, which people read, are never touched. A corrected text is taken only when it has the same
    sentences as the block."""
    sentences = [dict(s) for s in block.get("sentences") or []]
    applied: list[str] = []
    respelled: list[dict] = []
    forms = [s.get("tts") or s["speech"] for s in sentences]
    text = review.get("corrected_text")
    if text and sentences:
        parts = [p for p in SENTENCE_END.split(text.strip()) if p]
        if len(parts) == len(forms):
            forms = parts
            applied.append("text")
        else:
            applied.append("text ignored: its sentences are not those of the block")
    for rule in review.get("corrected_pronunciation_rules") or []:
        pattern = whole_word(rule["surface"])
        if any(pattern.search(f) for f in forms):
            forms = [pattern.sub(rule["replacement"], f) for f in forms]
            applied.append(f"respelled {rule['surface']}")
            respelled.append(rule)
    tag = review.get("corrected_delivery_instruction")
    if tag and forms and not forms[0].startswith(tag):
        forms[0] = f"{tag} {forms[0]}"
        applied.append(f"tag {tag}")
    if not sentences:
        return block, [], []
    out = {**block, "sentences": [{**s, "tts": f} for s, f in zip(sentences, forms)]}
    out["tts_text"] = " ".join(forms)
    return out, [a for a in applied if not a.startswith("text ignored")] + [a for a in applied if a.startswith("text ignored")], respelled


class Reviewer:
    """Listens to audio for the semantic review and keeps the record of every block."""

    def __init__(self, db: Session, case_id: int, sources: dict[str, str] | None = None, tones: dict[str, dict] | None = None,
                 rules: list[dict] | None = None, runner: Callable[[object, str, list[str]], Awaitable[tuple[dict, object]]] | None = None,
                 word_runner: Callable[[object, str, list[str]], Awaitable[tuple[dict, object]]] | None = None):
        self.db, self.case_id = db, case_id
        self.sources, self.tones, self.rules = sources or {}, tones or {}, rules or []
        self.runner = runner or (lambda gen, body, audio: run_agent("documentary.semantic_review.audio", gen, TASK + body, images=audio))
        self.word_runner = word_runner or (lambda gen, body, audio: run_agent("documentary.semantic_review.word", gen, TASK_WORD + body, images=audio))
        self.records: dict[str, dict] = {}

    def context(self, block: dict) -> dict:
        cfg = ai_config.semantic_review
        sentences = block.get("sentences") or []
        display = " ".join(s.get("display") or s["speech"] for s in sentences) or block.get("display_text") or block["text"]
        tts = block.get("tts_text") or " ".join(s.get("tts") or s["speech"] for s in sentences) or block["text"]
        section = block.get("section_id")
        return {"source_text": (block.get("source_text") or self.sources.get(section, ""))[:cfg.source_chars],
                "scene_context": self.tones.get(section) or block.get("scene_context") or {},
                "display_text": display, "tts_text": tts, "protected_items": protected_items(display, tts),
                "pronunciation_rules": self.rules, "words_to_check": words_to_check(tts, self.rules), "previous_text": (block.get("previous_text") or "")[-cfg.neighbour_chars:] if cfg.neighbour_chars else "",
                "next_text": (block.get("next_text") or "")[:cfg.neighbour_chars], "allowed_delivery_tags": cfg.delivery_tags}

    async def _ask(self, runner, body: str, data: str, label: str) -> dict:
        """One model call on one audio clip, made again when the provider hiccups; the block is not judged when it keeps failing."""
        cfg = ai_config.semantic_review
        for attempt in range(cfg.call_retries + 1):
            try:
                with track_run(self.db, self.case_id, f"semantic review {label}", input_summary=label) as run:
                    got, res = await runner(get_generation_provider(), body, [data])
                    stamp_run(run, res, "semantic_audio_reviewer")
                return got if isinstance(got, dict) else {}
            except GenerationError as e:
                if attempt == cfg.call_retries:
                    raise SemanticReviewFailed([{"block_id": label, "reason": f"the review could not be made: {type(e).__name__}: {e}"[:200]}]) from e
                await asyncio.sleep(cfg.retry_pause_s)
        return {}

    async def listen(self, context: dict, audio: Path, label: str) -> dict:
        """One review of one audio file against its context."""
        mp3 = audio.with_suffix(".review.mp3")
        if not mp3.exists():
            await asyncio.to_thread(A.to_review_mp3, audio, mp3)
        body = json.dumps(context, ensure_ascii=False)
        saved = audio.with_suffix(".review.json")          # the same audio, context and instructions are never judged twice (a resumed run)
        key = hashlib.sha256(mp3.read_bytes() + body.encode() + prompt_sha256("documentary/semantic_review/system").encode()).hexdigest()
        try:
            cached = json.loads(saved.read_text(encoding="utf-8"))
            if cached.get("key") == key:
                return cached["result"]
        except (OSError, ValueError, KeyError):
            pass
        data = "data:audio/mpeg;base64," + base64.b64encode(mp3.read_bytes()).decode()
        result = normalise(await self._ask(self.runner, body, data, label))
        saved.write_text(json.dumps({"key": key, "result": result}, ensure_ascii=False), encoding="utf-8")
        return result

    async def listen_word(self, wav: Path, span: tuple[float, float], word: str, kind: str, following: str, label: str) -> list[str]:
        """The options chosen by two independent listenings to a short clip around one word (the options are offered in opposite orders, so that a
        habit of the model for the first one does not decide). Saved with the clip, so a resumed run does not listen again."""
        cfg = ai_config.semantic_review
        clip = wav.with_name(f"{wav.stem}.w{int(span[0] * 1000)}-{int(span[1] * 1000)}.mp3")
        await asyncio.to_thread(A.cut_to_mp3, wav, clip, span[0] - cfg.word_pad_before_s, span[1] + cfg.word_pad_after_s)
        options = word_options(kind)
        reading = next((r.get("alias") or r.get("phoneme") for r in self.rules if r["string_to_replace"] == word), "")
        bodies = [json.dumps({"word": word, "kind": kind, "following_word": following, "expected_reading": reading,
                              "options": [{"id": i, "description": d} for i, d in order]}, ensure_ascii=False) for order in (options, options[::-1])]
        saved = clip.with_suffix(".json")
        key = hashlib.sha256(clip.read_bytes() + "".join(bodies).encode() + prompt_sha256("documentary/semantic_review/word_system").encode()).hexdigest()
        try:
            cached = json.loads(saved.read_text(encoding="utf-8"))
            if cached.get("key") == key:
                return cached["choices"]
        except (OSError, ValueError, KeyError):
            pass
        data = "data:audio/mpeg;base64," + base64.b64encode(clip.read_bytes()).decode()
        answers = [await self._ask(self.word_runner, body, data, f"{label} {word}") for body in bodies]
        choices = [str(a.get("choice") or "cannot_tell") for a in answers]
        saved.write_text(json.dumps({"key": key, "choices": choices, "heard": [a.get("heard") for a in answers]}, ensure_ascii=False), encoding="utf-8")
        return choices

    async def check_words(self, result: dict) -> tuple[list[dict], int]:
        """Every word that needs a check, listened to alone in a short clip. Returns the errors (a definite wrong sound in either listening) and
        how many words could be located and checked."""
        cfg = ai_config.semantic_review
        take = result["take"]
        words, text = take.get("words") or [], take.get("text") or ""
        jobs = []
        for target in words_to_check(text, self.rules):
            kind = "ezafe" if LEX.has_ezafe(target) else "reading"
            for first, last in locate(text, words, target, keep_marks=kind == "ezafe"):
                jobs.append((target, kind, (words[first]["start"], words[last]["end"]), words[last + 1]["word"] if last + 1 < len(words) else ""))
        jobs = jobs[:cfg.max_words_per_block]
        label = result["block"]["block_id"]
        wav = Path(take["wav_path"])
        got = await asyncio.gather(*(self.listen_word(wav, span, word, kind, following, label) for word, kind, span, following in jobs))
        errors = []
        for (word, kind, _span, _following), choices in zip(jobs, got):
            wrong = [c for c in choices if c not in (expected_choice(kind, word), "cannot_tell")]
            if wrong:
                said = dict(word_options(kind)).get(wrong[0], wrong[0])
                errors.append({"type": "pronunciation", "severity": "major", "expected": word, "heard": said,
                               "reason": f"listened to alone: {said}"})
        return errors, len(jobs)

    async def review_block(self, result: dict) -> dict:
        """The review of a voiced block: the whole block first; only a block that passes it goes on to the last check, each word that needs one
        listened to on its own (a failed block is made again anyway, so its words are not listened to)."""
        review = await self.listen(self.context(result["block"]), Path(result["take"]["wav_path"]), result["block"]["block_id"])
        if ai_config.semantic_review.word_review and review["verdict"] == "pass":
            errors, checked = await self.check_words(result)
            review = {**review, "words_checked": checked}
            if errors:
                review = {**review, "errors": review["errors"] + errors, "verdict": "fail"}
        return review

    async def run(self, result: dict, speak: Callable[[dict, int], Awaitable[dict]], seed: int, language: str = "fa") -> dict:
        """The review of a voiced block, with the corrections: `result` is what the voice-repair loop made, `speak(block, seed)` runs that loop
        again for a corrected block. Returns the result that is kept; its record says whether it was approved."""
        cfg = ai_config.semantic_review
        block = result["block"]
        record = {"block_id": block["block_id"], "voice_attempts": 1, "semantic_review_attempts": 0, "voice_review_result": "fail",
                  "avatar_review_result": "not_applicable", "errors": [], "final_status": "failed", "reviews": []}
        self.records[block["block_id"]] = record
        pending: list[dict] = []           # respellings the next review has to prove before the lexicon keeps them
        for attempt in range(cfg.max_corrections + 1):
            try:
                review = await self.review_block(result)
            except SemanticReviewFailed as e:           # this block could not be reviewed: it fails alone, the others go on
                record["failure"] = e.failed[0]["reason"]
                break
            record["semantic_review_attempts"] += 1
            record["reviews"].append({"attempt": attempt + 1, **review})
            record["errors"] = review["errors"]
            if review["verdict"] == "pass":
                record["voice_review_result"], record["final_status"] = "pass", "approved"
                if self.db is not None:
                    record["learned"] = (LEX.learn(self.db, language, pending, self.case_id)
                                         + LEX.confirm(self.db, language, self.rules, self.context(result["block"])["tts_text"], self.case_id))
                break
            if attempt == cfg.max_corrections:
                record["failure"] = "still failing after the allowed corrections"
                break
            fixed, applied, respelled = corrected_block(result["block"], review)
            if review["notation_read_aloud"]:
                fixed, extra = without_dictionary(fixed, self.rules)
                applied += extra
            record["reviews"][-1]["applied"] = applied
            if {e["type"] for e in review["errors"]} & set(cfg.learn_types):
                pending += respelled
            seed += 1                      # a correction that changes nothing in the request is still a new take
            result = await speak(fixed, seed)
            record["voice_attempts"] += 1
        result["semantic_review"] = record
        return result


def save_records(out: Path, records: dict[str, dict]) -> Path:
    path = out / "semantic_review.json"
    path.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def failed_blocks(records: dict[str, dict]) -> list[dict]:
    return [{"block_id": r["block_id"], "reason": r.get("failure") or "failed"} for r in records.values() if r["final_status"] != "approved"]
