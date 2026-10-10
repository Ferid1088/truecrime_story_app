"""Pronunciation check — does the voice SAY each word the way its meaning
needs?

Persian script leaves most short vowels unwritten: «ملک» is melk
(property), molk (realm), malek (king) or malak (angel); «جنت» is jannat,
not jennat; «اندام» is andam, not endam or ondam. A voice engine guesses.
Whisper cannot catch a wrong guess, because it writes Persian without
short vowels too — malk and molk both come back as «ملک».

So the narration is checked by LISTENING to the vowels:

1. Pronunciation key (text, before the voice; role pronunciation_editor):
   for every word a reader could misread, the reading the MEANING of the
   sentence needs (compared with the English source) and the harakat that
   force it: minimal (مُلک), full (مُلْک) and, if needed, an unambiguous
   spelling.
2. Listening (after every take): a phoneme recognizer (wav2vec2, IPA)
   hears the block; each risky word is found at its exact place in the
   audio (ElevenLabs character timing) and its vowels are compared with
   the reading the key asks for. Whisper still checks the words.
3. Correction loop: a word said wrong gets harakat in the text sent to
   the voice (then full harakat, then the unambiguous spelling); the
   block is spoken again and checked again — up to
   pronunciation.max_rounds. Words still wrong are flagged for a person.
Subtitles and the Whisper check use the text without the added harakat.
"""

from __future__ import annotations

from app.agents.runner import run_agent

from app.core.prompts import prompt

import json
import re
import threading
from functools import lru_cache

from app.core.ai_config import PronunciationConfig, ai_config
from app.core.concurrency import gather_limited
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

# Arabic-script short-vowel marks (harakat), shadda, sukun, superscript alef
HARAKAT = "\u064B\u064C\u064D\u064E\u064F\u0650\u0651\u0652\u0670"
_HARAKAT_RE = re.compile(f"[{HARAKAT}]")
_WORD_CH = f"\\w{HARAKAT}\u200c"


def strip_harakat(text: str) -> str:
    return _HARAKAT_RE.sub("", text or "")


# ---------------------------------------------------------------------------
# vowels: what the key asks for, what the recognizer heard
# ---------------------------------------------------------------------------

# Vowel classes: a (short a, æ), A (long ā, ɒː), e, i, o, u
_IPA_VOWELS = {
    "a": "a", "æ": "a", "ɐ": "a", "ʌ": "a", "aː": "A", "æː": "A",
    "ɑ": "A", "ɑː": "A", "ɒ": "A", "ɒː": "A",
    "ɔ": "O", "ɔː": "O",  # between ā and o in Persian TTS: matches both
    "e": "e", "eː": "e", "ɛ": "e", "ɛː": "e", "ə": "e", "ɪ": "i",
    "i": "i", "iː": "i", "y": "i",
    "o": "o", "oː": "o", "ɵ": "o",
    "u": "u", "uː": "u", "ʊ": "u", "ɯ": "u",
}
# substitution costs between classes (symmetric); everything else 1.0.
# Persian ā is a back [ɒ] that the recognizer often writes as "o"
# (بادام -> b o d o m): ā/o costs almost nothing. Long ā is written in
# Persian script anyway (ا / آ); the voice's mistakes are short vowels.
_CLOSE = {("a", "A"): 0.5, ("e", "i"): 0.5, ("o", "u"): 0.5, ("A", "o"): 0.1,
          ("O", "A"): 0.0, ("O", "o"): 0.0, ("O", "a"): 0.6, ("O", "u"): 0.5}


def _sub_cost(x: str, y: str) -> float:
    if x == y:
        return 0.0
    return _CLOSE.get((x, y), _CLOSE.get((y, x), 1.0))


def reading_vowels(read: str) -> list[str]:
    """Latin reading (molk, jannat, aakhare) -> vowel classes."""
    r = (read or "").lower().replace("ā", "aa").replace("â", "aa")
    out, i = [], 0
    while i < len(r):
        two = r[i:i + 2]
        if two in ("aa",):
            out.append("A")
            i += 2
        elif two in ("ii", "ee"):
            out.append("i")
            i += 2
        elif two in ("oo", "uu", "ou"):
            out.append("u")
            i += 2
        elif r[i] in "aeiou":
            out.append({"a": "a", "e": "e", "i": "i", "o": "o", "u": "u"}[r[i]])
            i += 1
        else:
            i += 1
    return out


def ipa_vowels(tokens: list[str]) -> list[str]:
    return [_IPA_VOWELS[t] for t in tokens if t in _IPA_VOWELS]


# reading consonants -> IPA tokens the recognizer may write for them
_CONS = {
    "kh": {"x", "χ", "h"}, "gh": {"q", "ɢ", "ʁ", "ɣ", "ɡ", "k"}, "sh": {"ʃ", "s"},
    "ch": {"tʃ", "ʃ"}, "zh": {"ʒ", "dʒ"}, "j": {"dʒ", "ʒ", "j"}, "y": {"j", "i", "iː"},
    "g": {"ɡ", "k"}, "k": {"k", "ɡ", "c"}, "v": {"v", "w", "β", "f"}, "w": {"v", "w"},
    "h": {"h", "ɦ", "x"}, "r": {"r", "ɾ", "ɹ"}, "q": {"q", "ɢ", "ʁ", "ɣ"},
    "t": {"t", "d"}, "d": {"d", "t"}, "b": {"b", "p"}, "p": {"p", "b"}, "s": {"s", "z"},
    "z": {"z", "s"}, "f": {"f", "v"}, "m": {"m", "n"}, "n": {"n", "m", "ŋ"},
    "l": {"l", "ɫ"}, "'": set(),
}


def reading_consonants(read: str) -> list[set[str]]:
    r = (read or "").lower()
    out, i = [], 0
    while i < len(r):
        two = r[i:i + 2]
        if two in ("kh", "gh", "sh", "ch", "zh"):
            out.append(_CONS[two])
            i += 2
            continue
        ch = r[i]
        if ch not in "aeiou -'":
            if not out or i == 0 or r[i - 1] != ch:  # doubled consonant = one sound
                out.append(_CONS.get(ch, {ch}))
        i += 1
    return out


def word_tokens(read: str, window: list[str]) -> list[str]:
    """The recognizer's tokens that belong to THIS word: its consonants
    are matched in order inside a generous window (the best-matching
    start wins), so the neighbours' sounds are left out. Falls back to the
    whole window."""
    cons = reading_consonants(read)
    if not cons or not window:
        return window
    best = None  # (matched, -span, a, b)
    for a, tok in enumerate(window):
        if tok not in cons[0]:
            continue
        k, b, matched = 1, a, 1
        for j in range(a + 1, len(window)):
            if k < len(cons) and window[j] in cons[k]:
                k, b, matched = k + 1, j, matched + 1
            elif window[j] not in _IPA_VOWELS and k >= len(cons):
                break
        key = (matched, -(b - a), a, b)
        if best is None or key > best:
            best = key
    if best is None:
        return window
    _, _, a, b = best
    if reading_vowels(read[:1]) and a > 0 and window[a - 1] in _IPA_VOWELS:
        a -= 1  # the word starts with a vowel (andam)
    # vowels after the last matched consonant up to the next consonant
    # belong to the word when it ends in a vowel or a consonant was missed
    matched = best[0]
    if reading_vowels(read[-1:]) or matched < len(cons) or len(cons) == 1:
        while b + 1 < len(window) and window[b + 1] in _IPA_VOWELS:
            b += 1
            if len(cons) == 1:
                break
    return window[a:b + 1]


def vowel_distance(expected: list[str], heard: list[str]) -> tuple[float, int]:
    """(normalized weighted edit distance, hard substitutions). A missing
    vowel (the recognizer often drops one) costs 0.7, an extra one 0.7 —
    except an extra final e (the ezafe the narrator adds before the next
    word), which is free."""
    if heard and expected and len(heard) == len(expected) + 1 and heard[-1] == "e":
        heard = heard[:-1]
    n, m = len(expected), len(heard)
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    hard = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + 0.7
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + 0.7
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            sub = _sub_cost(expected[i - 1], heard[j - 1])
            options = [
                (dp[i - 1][j - 1] + sub, hard[i - 1][j - 1] + (1 if sub >= 1.0 else 0)),
                (dp[i - 1][j] + 0.7, hard[i - 1][j]),
                (dp[i][j - 1] + 0.7, hard[i][j - 1]),
            ]
            dp[i][j], hard[i][j] = min(options)
    return dp[n][m] / max(n, 1), hard[n][m]


def judge(read: str, heard_tokens: list[str], cfg: PronunciationConfig | None = None) -> dict:
    """Was the word said with the vowels its meaning needs?"""
    cfg = cfg or ai_config.pronunciation
    exp, got = reading_vowels(read), ipa_vowels(word_tokens(read, heard_tokens))
    if not got:
        return {"ok": None, "expected": exp, "heard": got, "distance": None,
                "reason": "nothing_heard"}
    dist, hard = vowel_distance(exp, got)
    ok = hard == 0 and dist <= cfg.max_vowel_distance
    return {"ok": ok, "expected": exp, "heard": got, "distance": round(dist, 2),
            "hard_errors": hard}


# ---------------------------------------------------------------------------
# finding a word in the text and in the audio
# ---------------------------------------------------------------------------


def find_word(text: str, word: str, start: int = 0) -> tuple[int, int] | None:
    """Character span of `word` as a whole word in `text` (harakat in the
    text are allowed: «مُلک» matches «ملک»)."""
    pattern = "".join(re.escape(ch) + f"[{HARAKAT}]*" for ch in strip_harakat(word))
    m = re.compile(f"(?<![{_WORD_CH}]){pattern}(?![{_WORD_CH}])").search(text, start)
    return (m.start(), m.end()) if m else None


def replace_word(text: str, span: tuple[int, int], form: str) -> str:
    return text[:span[0]] + form + text[span[1]:]


def span_times(alignment: dict, text: str, span: tuple[int, int], t0: float
               ) -> tuple[float, float] | None:
    """Audio time (block-local after trimming) of a character span, from
    the provider's character timing of exactly this text."""
    chars, starts, ends = alignment["characters"], alignment["starts"], alignment["ends"]
    if "".join(chars) != text:
        return None
    idx = [i for i in range(span[0], min(span[1], len(chars))) if not chars[i].isspace()]
    if not idx:
        return None
    return max(starts[idx[0]] - t0, 0.0), max(ends[idx[-1]] - t0, 0.0)


# ---------------------------------------------------------------------------
# the listener (phoneme recognizer)
# ---------------------------------------------------------------------------


class ListenerUnavailable(RuntimeError):
    pass


class PhonemeListener:
    """wav2vec2 phoneme recognition (IPA). Loaded once per process."""

    FRAME = 0.02  # seconds per output frame (wav2vec2 stride)

    def __init__(self, model_name: str):
        try:
            import numpy  # noqa: F401
            import torch  # noqa: F401
            from huggingface_hub import hf_hub_download
            from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForCTC
        except ImportError as e:  # optional dependency
            raise ListenerUnavailable(
                "Phoneme listener needs torch + transformers "
                "(pip install -r requirements-documentary.txt)") from e
        self.fe = Wav2Vec2FeatureExtractor.from_pretrained(model_name)
        self.model = Wav2Vec2ForCTC.from_pretrained(model_name).eval()
        vocab = json.load(open(hf_hub_download(model_name, "vocab.json"), encoding="utf-8"))
        self.inv = {v: k for k, v in vocab.items()}
        self.lock = threading.Lock()
        self.name = model_name

    def frames(self, wav_path: str) -> list[tuple[str, float, float]]:
        """Phonemes of a whole recording: [(ipa, start, end)]."""
        import subprocess

        import numpy as np
        import torch

        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav_path), "-ac", "1",
                              "-ar", "16000", "-f", "f32le", "-"],
                             capture_output=True, check=True).stdout
        audio = np.frombuffer(raw, np.float32)
        with self.lock, torch.no_grad():
            logits = self.model(self.fe(audio, sampling_rate=16000,
                                        return_tensors="pt").input_values).logits
        ids = torch.argmax(logits, -1)[0].tolist()
        out: list[tuple[str, float, float]] = []
        prev = None
        for k, i in enumerate(ids):
            tok = self.inv.get(int(i), "")
            if i != prev and tok not in ("<pad>", "<s>", "</s>", "<unk>", "|", ""):
                out.append((tok, k * self.FRAME, (k + 1) * self.FRAME))
            elif i == prev and out and tok == out[-1][0]:
                out[-1] = (tok, out[-1][1], (k + 1) * self.FRAME)
            prev = i
        return out

    @staticmethod
    def between(frames: list[tuple[str, float, float]], start: float, end: float) -> list[str]:
        return [t for t, s, e in frames if e > start and s < end]


_listener_lock = threading.Lock()


@lru_cache(maxsize=2)
def _listener(model_name: str) -> PhonemeListener:
    return PhonemeListener(model_name)


def get_listener() -> PhonemeListener:
    with _listener_lock:
        return _listener(ai_config.pronunciation.phoneme_model)


# ---------------------------------------------------------------------------
# the pronunciation key (text side)
# ---------------------------------------------------------------------------

EDITOR_SYSTEM = prompt("documentary/pronunciation/editor_system")


def _tokens(sentence: str) -> set[str]:
    return {strip_harakat(t) for t in re.findall(f"[{_WORD_CH}]+", sentence or "")}


def _bare(text: str) -> str:
    return strip_harakat(text).replace("\u200c", "").strip()


_HARAKA_VOWEL = {"\u064E": "a", "\u0650": "e", "\u064F": "o"}


def harakat_agree(form: str, read: str) -> bool:
    """The harakat of a vowelled form say the same short vowels as the
    reading (fatha a, kasra e, damma o, in order). «مادَری» = maadari can
    never be the key for "maaderi": one of the two is wrong, so neither
    is trusted. A final kasra (ezafe) is allowed."""
    marks = [_HARAKA_VOWEL[c] for c in form if c in _HARAKA_VOWEL]
    short = [v for v in reading_vowels(read) if v in ("a", "e", "o")]
    k = 0
    for i, v in enumerate(marks):
        while k < len(short) and short[k] != v:
            k += 1
        if k == len(short):
            return i == len(marks) - 1 and v == "e" and form.endswith("\u0650")
        k += 1
    return True


def _letters_close(a: str, b: str) -> bool:
    from rapidfuzz.distance import Levenshtein

    return Levenshtein.distance(_bare(a), _bare(b)) <= 2


def validate_key(sentence: str, words: list[dict]) -> tuple[list[dict], list[str]]:
    """Keep only usable entries: the word is in the sentence, the
    vowelled/full forms keep its letters, the reading is Latin."""
    toks = _tokens(sentence)
    ok, issues = [], []
    for w in words or []:
        if not isinstance(w, dict):
            continue
        word = strip_harakat(str(w.get("w") or "")).strip()
        read = str(w.get("read") or "").strip().lower()
        if not word or word not in toks:
            issues.append(f"not_in_sentence:{word}")
            continue
        if not read or not re.fullmatch(r"[a-z'\- ]+", read) or not reading_vowels(read):
            issues.append(f"bad_reading:{word}")
            continue
        entry = {"w": word, "read": read, "meaning": str(w.get("meaning") or "")[:60]}
        disagree = False
        for key in ("vowelled", "full"):
            form = str(w.get(key) or "").strip()
            if form and strip_harakat(form) == word and form != word:
                if not harakat_agree(form, read):
                    disagree = True
                    continue
                entry[key] = form
        if disagree:
            # the key contradicts itself: this word is not checked at all
            issues.append(f"key_inconsistent:{word}")
            continue
        respell = str(w.get("respell") or "").strip()
        # a respelling is the SAME word written unambiguously (a letter or
        # two differ), never another word
        if (respell and respell != word and " " not in respell
                and _letters_close(strip_harakat(respell), word)):
            entry["respell"] = respell
        syn = " ".join(str(w.get("synonym") or "").split())
        syn_read = str(w.get("synonym_read") or "").strip().lower()
        if (syn and _bare(syn) != _bare(word) and syn_read
                and re.fullmatch(r"[a-z'\- ]+", syn_read) and len(syn.split()) <= 3):
            entry["synonym"], entry["synonym_read"] = syn, syn_read
        if not any(k in entry for k in ("vowelled", "full", "respell", "synonym")):
            issues.append(f"no_fix:{word}")
        ok.append(entry)
    return ok, issues


class PronunciationEditor:
    def __init__(self):
        self.gen = get_generation_provider()
        self.cfg = ai_config.pronunciation

    async def _call(self, db, case_id, language: str, chunk: list[dict]) -> dict[int, list]:
        payload = {"language": language, "passages": []}
        for it in chunk:
            if not payload["passages"] or payload["passages"][-1]["beat_id"] != it["beat_id"]:
                payload["passages"].append({"beat_id": it["beat_id"],
                                            "english_source": it.get("source", ""),
                                            "sentences": []})
            payload["passages"][-1]["sentences"].append({"i": it["i"], "text": it["text"]})
        with track_run(db, case_id, f"Pronunciation Key ({language})",
                       input_summary=f"{len(chunk)} sentences") as run:
            data, res = await run_agent("documentary.pronunciation", self.gen, "TASK: list the words of every sentence that a voice could misread, "
                "with the reading the meaning needs. Return JSON only.\n\nINPUT:\n"
                + json.dumps(payload, ensure_ascii=False), system=EDITOR_SYSTEM)
            stamp_run(run, res, "pronunciation_editor")
        out: dict[int, list] = {}
        if isinstance(data, dict):
            for s in data.get("sentences") or []:
                if isinstance(s, dict) and isinstance(s.get("i"), int):
                    out[s["i"]] = s.get("words") or []
        return out

    async def annotate(self, db, case_id, language: str, items: list[dict]
                       ) -> tuple[dict[int, list[dict]], dict]:
        """items: [{"i", "beat_id", "text", "source"}] -> ({i: risky words},
        report)."""
        size = self.cfg.sentences_per_call
        chunks = [items[k:k + size] for k in range(0, len(items), size)]
        results = await gather_limited(
            None, [self._call(db, case_id, language, c) for c in chunks],
            return_exceptions=True)
        keyed: dict[int, list[dict]] = {}
        issues: list[str] = []
        errors: list[str] = []
        texts = {it["i"]: it["text"] for it in items}
        for chunk, res in zip(chunks, results):
            if isinstance(res, Exception):
                errors.append(f"{type(res).__name__}: {str(res)[:160]}")
                continue
            for it in chunk:
                words, probs = validate_key(texts[it["i"]], res.get(it["i"]) or [])
                keyed[it["i"]] = words
                issues += probs
        report = {"sentences": len(items), "risky_words": sum(len(v) for v in keyed.values()),
                  "issues": issues[:40], "errors": errors}
        return keyed, report


# ---------------------------------------------------------------------------
# one listening round over a take
# ---------------------------------------------------------------------------


def risky_occurrences(sentences: list[dict]) -> list[dict]:
    """Every risky word of a block's sentences: [{"s": sentence index,
    "word", "read", "forms": [...fix forms in order], "level": 0}]."""
    order = ai_config.pronunciation.fix_order
    out = []
    for si, sn in enumerate(sentences or []):
        for w in sn.get("risky") or []:
            forms: list[dict] = []
            for k in order:
                if k == "synonym":
                    if w.get("synonym"):
                        forms.append({"kind": "synonym", "text": w["synonym"],
                                      "read": w.get("synonym_read") or w["read"]})
                elif w.get(k) and w[k] not in [f["text"] for f in forms]:
                    forms.append({"kind": k, "text": w[k], "read": w["read"]})
            out.append({"s": si, "word": w["w"], "current": w["w"], "read": w["read"],
                        "meaning": w.get("meaning"), "forms": forms, "level": 0})
    return out


def locate(text: str, sentences: list[dict], occ: dict) -> tuple[int, int] | None:
    """Span of an occurrence's word inside the block text (its sentence
    first, so the same word in another sentence is not confused)."""
    pos = 0
    for k, sn in enumerate(sentences):
        form = sn.get("tts") or sn["speech"]
        at = text.find(form, pos)
        if at < 0:
            return find_word(text, occ["word"])
        if k == occ["s"]:
            span = find_word(text[:at + len(form)], occ["word"], at)
            return span
        pos = at + len(form)
    return None


def listen(frames: list[tuple[str, float, float]], text: str, sentences: list[dict],
           occurrences: list[dict], alignment: dict, t0: float,
           cfg: PronunciationConfig | None = None) -> list[dict]:
    """Judge every risky word of a take."""
    cfg = cfg or ai_config.pronunciation
    results = []
    for occ in occurrences:
        span = locate(text, sentences, {**occ, "word": occ.get("current", occ["word"])})
        times = span_times(alignment, text, span, t0) if span else None
        if times is None:
            results.append({**_public(occ), "ok": None, "reason": "not_located"})
            continue
        heard = PhonemeListener.between(frames, times[0] - cfg.pad_seconds,
                                        times[1] + cfg.pad_seconds)
        verdict = judge(occ["read"], heard, cfg)
        results.append({**_public(occ), **verdict, "heard_ipa": " ".join(heard),
                        "at": [round(times[0], 2), round(times[1], 2)]})
    return results


def _public(occ: dict) -> dict:
    form = occ["forms"][occ["level"] - 1] if occ["level"] else None
    return {"word": occ["word"], "read": occ["read"], "sentence": occ["s"],
            "level": occ["level"], "form": form["text"] if form else occ["word"],
            "fix": form["kind"] if form else None}


def apply_fixes(sentences: list[dict], occurrences: list[dict], wrong: list[dict]
                ) -> tuple[list[dict], list[dict]]:
    """Next fix for every wrong word: harakat, full harakat, unambiguous
    spelling — the text the voice reads changes, what people read does
    not; last, a synonym (then the subtitles change with it).
    Returns (new sentences, applied fixes)."""
    sentences = [dict(s) for s in sentences]
    applied = []
    for res in wrong:
        occ = next((o for o in occurrences if o["s"] == res["sentence"]
                    and o["word"] == res["word"]), None)
        if occ is None or occ["level"] >= len(occ["forms"]):
            continue
        sn = sentences[occ["s"]]
        current = sn.get("tts") or sn["speech"]
        span = find_word(current, occ["current"])
        if span is None:
            continue
        form = occ["forms"][occ["level"]]
        occ["level"] += 1
        sn["tts"] = replace_word(current, span, form["text"])
        if form["kind"] == "synonym":
            for key in ("speech", "display"):
                sp = find_word(sn.get(key) or "", occ["current"])
                if sp:
                    sn[key] = replace_word(sn[key], sp, form["text"])
            occ["current"] = form["text"]
            occ["read"] = form["read"]
        else:
            occ["current"] = form["text"]
        applied.append({"word": occ["word"], "read": occ["read"], "form": form["text"],
                        "fix": form["kind"], "level": occ["level"], "sentence": occ["s"]})
    return sentences, applied
