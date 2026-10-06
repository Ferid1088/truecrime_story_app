"""Independent speech-to-text check for rendered voice blocks.

The text-to-speech engine can skip words, repeat a phrase, invent a word
or mispronounce a name. A different system (local Whisper via
faster-whisper — not the TTS vendor, so mistakes are not shared) listens
to each block; its transcript is compared with the script.

The comparison normalizes both sides first (case, punctuation, number
and date spelling — "16 July" vs "the 16th of July" is not an error),
then reports the word error rate, runs of missing / extra words and the
exact differing phrases so a person can see what went wrong.
"""

from __future__ import annotations

import importlib.util
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

from rapidfuzz import fuzz

from app.core.ai_config import ASRCheckConfig, ai_config

_MONTHS = {
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
}
_ORDINAL = re.compile(r"^(\d+)(st|nd|rd|th)$")
# Clause separators: normalizing clause by clause stops "2007, forty-five"
# from being merged into one number by the English number normalizer.
_CLAUSE_SPLIT = re.compile(r"[,;:.!?…—–()\"“”„«»]+")
_ARABIC_DIACRITICS = re.compile(r"[ً-ٰٟۖ-ۭ]")
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


class ASRUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------


def _basic_tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower().translate(_DIGITS)
    text = _ARABIC_DIACRITICS.sub("", text)
    text = text.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    text = re.sub(r"[^\w\s']", " ", text)
    return [t.strip("'") for t in text.split() if t.strip("'")]


@lru_cache(maxsize=1)
def _english_normalizer():
    try:
        from whisper_normalizer.english import EnglishTextNormalizer
    except ImportError:  # optional dependency
        return None
    return EnglishTextNormalizer()


def _english_tokens(text: str) -> list[str]:
    norm = _english_normalizer()
    if norm is None:
        return _basic_tokens(text)
    tokens: list[str] = []
    for clause in _CLAUSE_SPLIT.split(text):
        if clause.strip():
            tokens.extend(norm(clause).split())
    return tokens


def _canonical_dates(tokens: list[str]) -> list[str]:
    """'the 16th of july' / 'july 16' / '16 july' -> '16 july'."""
    out = [_ORDINAL.sub(r"\1", t) for t in tokens]
    res: list[str] = []
    i = 0
    while i < len(out):
        t = out[i]
        nxt = out[i + 1] if i + 1 < len(out) else ""
        if t == "the" and nxt.isdigit():
            i += 1
            continue
        if t == "of" and res and res[-1].isdigit() and nxt in _MONTHS:
            i += 1
            continue
        if t in _MONTHS and nxt.isdigit() and len(nxt) <= 2:
            res.extend([nxt, t])
            i += 2
            continue
        res.append(t)
        i += 1
    return res


_APOSTROPHES = re.compile(r"[’‘`´ʼ]")
_POSSESSIVE = re.compile(r"(\w)'s\b")
_PLURAL_POSSESSIVE = re.compile(r"(\w)s'(?=\W|$)")


# --- Persian -------------------------------------------------------------
# Whisper and a careful script spell the same Persian speech differently:
# ZWNJ or space or nothing before "می"/"ها", the ezafe "ی" after "ه",
# آ/ا, Arabic letter forms, number words vs digits. Both sides are brought
# to one form so only real speech errors remain.
_FA_PREFIXES = {"می", "نمی", "همی"}
_FA_SUFFIXES = {"ها", "های", "هایی", "هایم", "هایش", "هایت", "هایشان", "ای",
                "ام", "اش", "ات", "اند", "ایم", "اید", "تر", "ترین", "ی"}
_FA_UNITS = {"صفر": 0, "یک": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6,
             "شیش": 6, "هفت": 7, "هشت": 8, "نه": 9}
_FA_TEENS = {"ده": 10, "یازده": 11, "دوازده": 12, "سیزده": 13, "چهارده": 14,
             "پانزده": 15, "پونزده": 15, "شانزده": 16, "شونزده": 16, "هفده": 17,
             "هجده": 18, "هیجده": 18, "نوزده": 19}
_FA_TENS = {"بیست": 20, "سی": 30, "چهل": 40, "پنجاه": 50, "شصت": 60,
            "هفتاد": 70, "هشتاد": 80, "نود": 90}
_FA_HUNDREDS = {"صد": 100, "یکصد": 100, "دویست": 200, "سیصد": 300, "چهارصد": 400,
                "پانصد": 500, "پونصد": 500, "ششصد": 600, "هفتصد": 700,
                "هشتصد": 800, "نهصد": 900}
_FA_SCALES = {"هزار": 1000, "میلیون": 10 ** 6, "میلیارد": 10 ** 9}
_FA_SMALL = {**_FA_UNITS, **_FA_TEENS, **_FA_TENS, **_FA_HUNDREDS}
# Words that are also ordinary words ("no", "village", "thirty"/"sea"):
# converted only inside a longer number.
_FA_AMBIGUOUS = {"نه", "ده", "سی", "صد"}


def _fa_letters(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(_DIGITS)
    text = _ARABIC_DIACRITICS.sub("", text)
    for a, b in (("ي", "ی"), ("ى", "ی"), ("ك", "ک"), ("ة", "ه"), ("ۀ", "ه"),
                 ("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ٱ", "ا"), ("ؤ", "و"),
                 ("ئ", "ی"), ("ء", ""), ("ـ", "")):
        text = text.replace(a, b)
    # ZWNJ joins (خانه‌ها -> خانهها, می‌کند -> میکند)
    return text.replace("\u200c", "").replace("\u200d", "")


def _fa_numbers(tokens: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t not in _FA_SMALL and t not in _FA_SCALES:
            out.append(t)
            i += 1
            continue
        run = [t]
        j = i + 1
        while j < len(tokens):
            if tokens[j] in _FA_SCALES:
                run.append(tokens[j])
                j += 1
            elif (tokens[j] == "و" and j + 1 < len(tokens)
                  and (tokens[j + 1] in _FA_SMALL or tokens[j + 1] in _FA_SCALES)):
                run.append(tokens[j + 1])
                j += 2
            else:
                break
        if len(run) == 1 and (t in _FA_AMBIGUOUS or t in _FA_SCALES):
            out.append(t)
            i += 1
            continue
        total, cur = 0, 0
        for w in run:
            if w in _FA_SCALES:
                total += max(cur, 1) * _FA_SCALES[w]
                cur = 0
            else:
                cur += _FA_SMALL[w]
        out.append(str(total + cur))
        i = j
    return out


def persian_tokens(text: str) -> list[str]:
    text = _fa_letters(text)
    text = re.sub(r"[^\w\s]", " ", text)
    raw = [t for t in text.split() if t]
    joined: list[str] = []
    k = 0
    while k < len(raw):
        t = raw[k]
        if t in _FA_PREFIXES and k + 1 < len(raw):
            joined.append(t + raw[k + 1])
            k += 2
            continue
        if t in _FA_SUFFIXES and joined and t != "ی":
            joined[-1] += t
            k += 1
            continue
        if t == "ی" and joined and joined[-1].endswith("ه"):
            k += 1  # detached ezafe after ه
            continue
        joined.append(t)
        k += 1
    out = []
    for t in _fa_numbers(joined):
        # ezafe / indefinite ی after ه, written or not: خانهی == خانه
        if len(t) > 2 and t.endswith("هی"):
            t = t[:-1]
        out.append(t)
    return out


def normalize_tokens(text: str, language: str) -> list[str]:
    # One apostrophe, and possessives as plain words, on BOTH sides:
    # otherwise "Kadwill’s" becomes "kadwill s" while Whisper's
    # "Kadwill's" becomes "kadwill is" (contraction expansion).
    text = _APOSTROPHES.sub("'", text or "")
    text = _PLURAL_POSSESSIVE.sub(r"\1s", _POSSESSIVE.sub(r"\1s", text))
    if language == "fa":
        return persian_tokens(text)
    tokens = _english_tokens(text) if language == "en" else _basic_tokens(text)
    return _canonical_dates(tokens)


def _is_spelling_variant(ref: list[str], hyp: list[str], min_similarity: float) -> bool:
    """Same words, different spelling or spacing — e.g. a name Whisper
    spells differently ('Chantelle'/'Chantel'), or 'south west' vs
    'southwest'. Not a speech error, but still reported."""
    if "".join(ref) == "".join(hyp):
        return True
    if len(ref) != len(hyp):
        return False
    return all(
        fuzz.ratio(a, b) >= min_similarity and not (a.isdigit() or b.isdigit())
        for a, b in zip(ref, hyp)
    )


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------


def compare_transcript(
    script: str, heard: str, language: str, cfg: ASRCheckConfig | None = None
) -> dict:
    cfg = (cfg or ai_config.asr_check).for_language(language)
    ref = normalize_tokens(script, language)
    hyp = normalize_tokens(heard, language)
    sm = SequenceMatcher(a=ref, b=hyp, autojunk=False)
    subs = dels = ins = 0
    missing_run = extra_run = 0
    diffs: list[dict] = []
    variants: list[dict] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        if op == "replace" and _is_spelling_variant(
            ref[i1:i2], hyp[j1:j2], cfg.spelling_variant_similarity
        ):
            variants.append({"script": " ".join(ref[i1:i2]),
                             "heard": " ".join(hyp[j1:j2])})
            continue
        a, b = i2 - i1, j2 - j1
        subs += min(a, b)
        dels += max(a - b, 0)
        ins += max(b - a, 0)
        missing_run = max(missing_run, a - b if op != "insert" else 0)
        extra_run = max(extra_run, b - a if op != "delete" else 0)
        diffs.append({
            "type": {"replace": "changed", "delete": "missing",
                     "insert": "extra"}[op],
            "script": " ".join(ref[i1:i2]),
            "heard": " ".join(hyp[j1:j2]),
        })
    n = max(len(ref), 1)
    wer = (subs + dels + ins) / n
    failures = []
    if wer > cfg.max_word_error_rate:
        failures.append("word_error_rate")
    if missing_run >= cfg.max_missing_run:
        failures.append("missing_words")
    if extra_run >= cfg.max_extra_run:
        failures.append("extra_words")
    return {
        "passed": not failures,
        "failures": failures,
        "word_error_rate": round(wer, 4),
        "script_words": len(ref),
        "substitutions": subs,
        "missing": dels,
        "extra": ins,
        "longest_missing_run": missing_run,
        "longest_extra_run": extra_run,
        "diffs": diffs,
        "spelling_variants": variants,
    }


# ---------------------------------------------------------------------------
# transcription
# ---------------------------------------------------------------------------


class FasterWhisperASR:
    """Local Whisper (CTranslate2). Model loads once per process."""

    name = "faster_whisper"
    _models: dict = {}

    def __init__(self, cfg: ASRCheckConfig | None = None):
        self.cfg = cfg or ai_config.asr_check
        if importlib.util.find_spec("faster_whisper") is None:
            raise ASRUnavailable(
                "faster-whisper is not installed "
                "(pip install -r requirements-documentary.txt)"
            )

    def _model(self, language: str | None = None):
        cfg = self.cfg.for_language(language)
        key = (cfg.model, cfg.compute_type)
        if key not in self._models:
            from faster_whisper import WhisperModel

            self._models[key] = WhisperModel(
                cfg.model, device="cpu", compute_type=cfg.compute_type
            )
        return self._models[key]

    def transcribe(self, audio_path: str, language: str) -> dict:
        from app.documentary.audio import pcm16k_float32

        segments, _ = self._model(language).transcribe(
            pcm16k_float32(audio_path), language=language,
            word_timestamps=True, vad_filter=False,
            # No script prompt: priming with the expected text would make
            # the check agree with the script instead of the audio.
            initial_prompt=None, condition_on_previous_text=False,
        )
        words, texts = [], []
        for seg in segments:
            texts.append(seg.text.strip())
            for w in seg.words or []:
                words.append({
                    "word": w.word.strip(), "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3),
                    "probability": round(float(w.probability), 3),
                })
        return {"text": " ".join(texts).strip(), "words": words}


def get_asr():
    """The configured ASR checker, or None when disabled/unavailable
    (callers record 'asr_unavailable' instead of silently passing)."""
    cfg = ai_config.asr_check
    if not cfg.enabled:
        return None
    if cfg.provider == "faster_whisper":
        return FasterWhisperASR(cfg)
    raise ValueError(f"Unknown ASR provider: {cfg.provider!r}")
