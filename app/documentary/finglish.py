"""Persian narration in Finglish — written so the voice cannot misread it.

Persian script leaves most short vowels unwritten: «ملک» can be molk
(property), malek (king), melk (estate) or malak (angel). A voice engine
guesses — and guesses wrong often enough to ruin a film. So the spoken
Persian is written directly in FINGLISH: everyday spoken (Tehrani)
Persian in Latin letters with every vowel written, the way the producer
writes it himself ("khunevaade", "khune", "nemidunest", "molk").

Two independent agents:
  * the spoken writer (role spoken_writer) tells the English script
    directly in colloquial Persian Finglish — no Persian-script step in
    between that could lose the vowels;
  * the Finglish verifier (role finglish_verifier, a different model —
    review group "spoken_adaptation") checks EVERY word of every
    sentence: a real spoken Persian word, with exactly these vowels, and
    the meaning the sentence needs. It returns the corrected sentence and
    the same sentence in Persian script (subtitles, speech-to-text check,
    native critics).
The Finglish text is what the narrator reads (ElevenLabs v3 with
language_code "fa"), all the way to the end of the pipeline.
"""

from __future__ import annotations

import json
import re

from rapidfuzz import fuzz

from app.core.ai_config import ai_config
from app.core.concurrency import gather_limited
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

# Arabic-script letters (Persian/Arabic) — must never appear in Finglish.
_ARABIC_SCRIPT = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_DIGIT = re.compile(r"\d")

FINGLISH_RULES = """\
FINGLISH (how the narration is written — exactly these rules)
Register: everyday SPOKEN Persian (Tehrani colloquial), the way a good
storyteller really talks — not written/book Persian, not slang:
  khaanevaade -> khunevaade, khaane -> khune, aan -> un, aanhaa -> unaa,
  miguyad -> mige, miravad -> mire, nemidaanest -> nemidunest,
  raa -> ro / -o ("khune ro", "maashino"), ast -> -e ("in ajibe"),
  hast, bud, nabud, budan, mitunest, bayad, chon, vaghti, hanuz.
  Respectful and clear: no vulgar words, no youth slang.
Letters:
- Latin letters only. Never Persian/Arabic script, never accents,
  macrons or other special letters.
- Every vowel is written. Short: a (dar), e (del), o (molk).
  Long: aa (kaar, aab), i (zir, chiz), u (ruz, khune, un).
  After a vowel, a final long i is "ii" (daaraaii, nahaaii).
- kh = خ, gh = ق/غ, sh = ش, ch = چ, zh = ژ, j = ج, y = ی (consonant),
  v = و (consonant), h = ه/ح; s for س/ص/ث, z for ز/ذ/ض/ظ, t for ت/ط.
- A doubled (stressed) consonant is written double: avval, moddat,
  ettefaagh, mohemm.
- An apostrophe only for a clearly spoken glottal stop (so'aal, ta'sir).
- Ezafe is attached: "-e" after a consonant (matne nahaaii, dare khune),
  "-ye" after a vowel (khuneye un, noskheye farsi).
- Verb prefixes and endings attached: mikone, nemidunest, raftan,
  saalhaa, unaa.
- Numbers, years and dates ALWAYS as spoken words, never digits:
  "saale do hezaar o paanzdah", "panj saalesh bud".
- Foreign names as Persian speakers say them, one fixed spelling for the
  whole film (e.g. "Inga Gerike").
- Capital letter only at a sentence start and for names; never a whole
  word in capitals.
- Punctuation: . , ? ! and ... (Latin marks only)."""

VERIFIER_SYSTEM = f"""
You are a meticulous native Persian (Tehran) editor and pronunciation
checker. The narration of a Persian true-crime documentary is written
in FINGLISH — colloquial spoken Persian in Latin letters with every
vowel written — because a voice engine reads it aloud letter by letter.
One wrong vowel makes the voice say a different word: "malk" is not a
word; the house as property is "molk" (ملک); "malek" is a king, "melk"
real estate, "malak" an angel. Your job is to check EVERY word of EVERY
sentence.

For each sentence:
1. Read it word by word exactly as the rules below make a reader say it.
2. For every word ask:
   - Is it a real (colloquial) Persian word with exactly these vowels and
     consonants?
   - Is it the word this sentence needs — right meaning (compare the
     English source), right form (tense, person, plural, ezafe, "ro")?
   - Would a reader following the rules pronounce it correctly
     (aa / a, u / o, i / e, gh / kh / sh / ch / zh, doubled consonants)?
   - Is it written in the spoken register (khune, un, mige) and not in
     book Persian (khaane, aan, miguyad)?
   - Numbers must be words, never digits; names spelled the same way as
     elsewhere in the film.
3. Write the sentence in Persian script as "fa": exactly the same words
   in the same order (after your fixes), spoken-Persian spelling as used
   in Persian subtitles (خونه، می‌گه، نمی‌دونست، رو), ZWNJ in می‌/ها,
   Persian punctuation (، ؟), years and numbers as Persian digits
   (۲۰۱۵). Never improve the wording in "fa" — it must say exactly what
   the (fixed) Finglish says.

Only flag real problems (wrong word, wrong meaning, wrong vowels, book
register, digits, inconsistent names). Style is not your job. In
"fixed" change only the flagged words, nothing else.

{FINGLISH_RULES}

Return JSON only, one entry per input sentence, in input order:
{{"sentences": [{{"i": 0, "fa": "...",
  "issues": [{{"word": "malk", "fix": "molk", "why": "property = molk (ملک); malk is not a word"}}],
  "fixed": "the full corrected Finglish sentence, or null if no issues"}}]}}
"""


def has_persian_script(text: str) -> bool:
    return bool(_ARABIC_SCRIPT.search(text or ""))


def finglish_problems(sentence: str) -> list[str]:
    """Deterministic checks of one Finglish sentence."""
    out = []
    if has_persian_script(sentence):
        out.append("persian_script")
    if _DIGIT.search(sentence or ""):
        out.append("digits")
    if any(len(w) > 2 and w.isupper() and w.isalpha() for w in re.findall(r"[A-Za-z]+", sentence)):
        out.append("all_caps_word")
    return out


def accept_fix(original: str, fixed: str | None, min_similarity: float) -> str | None:
    """The verifier's corrected sentence, if it is a word-level fix (not
    a rewrite) and itself clean Finglish."""
    if not fixed or not isinstance(fixed, str):
        return None
    fixed = " ".join(fixed.split())
    if not fixed or fixed == original or has_persian_script(fixed):
        return None
    if abs(len(fixed.split()) - len(original.split())) > max(3, len(original.split()) // 4):
        return None
    if fuzz.ratio(original, fixed) < min_similarity:
        return None
    return fixed


class FinglishVerifier:
    """Checks and fixes Finglish sentences; remembers results per
    sentence so a re-check after a repair only pays for new sentences."""

    def __init__(self):
        self.gen = get_generation_provider()
        self.cfg = ai_config.spoken
        # finglish sentence -> {"fa", "issues", "fixed"}
        self.cache: dict[str, dict] = {}
        self.log: list[str] = []

    async def _call(self, db, case_id, chunk: list[dict]) -> dict[int, dict]:
        payload = {"beats": []}
        for item in chunk:
            if not payload["beats"] or payload["beats"][-1]["beat_id"] != item["beat_id"]:
                payload["beats"].append({"beat_id": item["beat_id"],
                                         "english_source": item["source"],
                                         "sentences": []})
            payload["beats"][-1]["sentences"].append({"i": item["i"], "finglish": item["text"]})
        with track_run(db, case_id, "Finglish Verifier",
                       input_summary=f"{len(chunk)} sentences") as run:
            data, res = await self.gen.generate_structured(
                "finglish_verifier", VERIFIER_SYSTEM,
                "TASK: check every word of every sentence below; return JSON only.\n\n"
                "INPUT:\n" + json.dumps(payload, ensure_ascii=False))
            stamp_run(run, res, "finglish_verifier")
        out: dict[int, dict] = {}
        for s in (data or {}).get("sentences") or [] if isinstance(data, dict) else []:
            if isinstance(s, dict) and isinstance(s.get("i"), int):
                out[s["i"]] = s
        return out

    async def check(self, db, case_id, items: list[dict], force: bool = False
                    ) -> dict[int, dict]:
        """items: [{"i", "beat_id", "text", "source"}] -> results by i.
        Cached sentences are not sent again (unless force)."""
        todo = list(items) if force else [it for it in items if it["text"] not in self.cache]
        size = self.cfg.finglish_sentences_per_call
        chunks: list[list[dict]] = []
        cur: list[dict] = []
        for it in todo:
            # keep whole beats together where possible
            if len(cur) >= size and cur[-1]["beat_id"] != it["beat_id"]:
                chunks.append(cur)
                cur = []
            cur.append(it)
            if len(cur) >= size * 1.5:
                chunks.append(cur)
                cur = []
        if cur:
            chunks.append(cur)
        results = await gather_limited(None, [self._call(db, case_id, c) for c in chunks],
                                       return_exceptions=True)
        for chunk, res in zip(chunks, results):
            if isinstance(res, Exception):
                self.log.append(f"verifier_error:{type(res).__name__}:{str(res)[:120]}")
                continue
            for it in chunk:
                r = res.get(it["i"])
                if r and isinstance(r.get("fa"), str) and r["fa"].strip():
                    self.cache[it["text"]] = {
                        "fa": " ".join(r["fa"].split()),
                        "issues": [x for x in r.get("issues") or [] if isinstance(x, dict)][:8],
                        "fixed": r.get("fixed"),
                    }
        return {it["i"]: self.cache.get(it["text"]) for it in items}

    async def process(self, db, case_id, beats: list[dict], sources: dict[str, str]
                      ) -> tuple[list[dict], dict, dict]:
        """beats: [{"id", "text"}] Finglish (paragraphs = blank lines).
        Returns (fixed beats, display {beat_id: [[sentence fa]]} aligned
        with speech {beat_id: [[sentence]]}, report)."""
        from app.agents.story import _paragraphs
        from app.documentary.voice_blocks import split_sentences

        # sentence table: (beat, paragraph, k) -> text
        table: list[dict] = []
        for b in beats:
            for pi, para in enumerate(_paragraphs(b["text"])):
                for k, s in enumerate(split_sentences(para, "en")):
                    table.append({"i": len(table), "beat_id": b["id"], "p": pi, "k": k,
                                  "text": s, "source": sources.get(b["id"], "")})
        fixed_count = 0
        rejected_fixes: list[dict] = []
        pending = list(table)
        for rnd in range(1 + self.cfg.finglish_fix_rounds):
            if not pending:
                break
            results = await self.check(db, case_id, pending, force=rnd > 0)
            changed = []
            for it in pending:
                r = results.get(it["i"])
                if not r or not r.get("issues"):
                    continue
                new = accept_fix(it["text"], r.get("fixed"), self.cfg.finglish_min_fix_similarity)
                if new is None:
                    if r.get("fixed"):
                        rejected_fixes.append({"sentence": it["text"], "fixed": r.get("fixed")})
                    continue
                # The verifier's Persian text describes the FIXED sentence.
                self.cache.setdefault(new, {"fa": r["fa"], "issues": [], "fixed": None,
                                            "unconfirmed": True})
                it["text"] = new
                fixed_count += 1
                changed.append(it)
            # re-check fixed sentences (catches errors a fix introduced);
            # until then the fix's own Persian text stands
            pending = [it for it in changed if self.cache.get(it["text"], {}).get("unconfirmed")]

        speech: dict[str, list[list[str]]] = {}
        display: dict[str, list[list[str | None]]] = {}
        open_issues, unverified, deterministic = [], 0, []
        for it in table:
            r = self.cache.get(it["text"])
            sp = speech.setdefault(it["beat_id"], [])
            dp = display.setdefault(it["beat_id"], [])
            while len(sp) <= it["p"]:
                sp.append([])
                dp.append([])
            sp[it["p"]].append(it["text"])
            dp[it["p"]].append(r["fa"] if r else None)
            if r is None:
                unverified += 1
            elif r.get("issues") and not accept_fix(it["text"], r.get("fixed"),
                                                     self.cfg.finglish_min_fix_similarity):
                open_issues.append({"beat_id": it["beat_id"], "sentence": it["text"],
                                    "issues": r["issues"]})
            probs = finglish_problems(it["text"])
            if probs:
                deterministic.append({"beat_id": it["beat_id"], "sentence": it["text"],
                                      "problems": probs})
        out_beats = []
        for b in beats:
            paras = speech.get(b["id"]) or []
            out_beats.append({"id": b["id"],
                              "text": "\n\n".join(" ".join(p) for p in paras if p) or b["text"]})
        report = {
            "sentences": len(table), "fixed": fixed_count, "unverified": unverified,
            "open_issues": open_issues[:40], "open_issue_count": len(open_issues),
            "deterministic": deterministic[:40],
            "deterministic_count": len(deterministic),
            "rejected_fixes": rejected_fixes[:20], "log": self.log[-20:],
        }
        return out_beats, {"speech": speech, "display": display}, report


def display_sections(structure: dict, beats: list[dict]) -> list[dict]:
    """Persian-script text per beat (paragraphs), for critics/metrics.
    A sentence without a verified Persian text keeps its Finglish."""
    out = []
    for b in beats:
        sp = (structure.get("speech") or {}).get(b["id"]) or []
        dp = (structure.get("display") or {}).get(b["id"]) or []
        paras = []
        for p_s, p_d in zip(sp, dp):
            paras.append(" ".join(d or s for s, d in zip(p_s, p_d)))
        out.append({"id": b["id"], "text": "\n\n".join(paras) or b["text"]})
    return out
