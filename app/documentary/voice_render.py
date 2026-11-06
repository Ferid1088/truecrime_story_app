"""Render a voice-block plan into checked, loudness-matched narration.

For every block:
  1. text-to-speech with character timestamps (provider: ElevenLabs),
     with the neighbouring sentences as context for continuity;
  2. trim to the speech the provider reports (+ small pads) — so the
     app, not the TTS engine, owns every pause;
  3. word timestamps from the provider's character timing;
  4. independent speech-to-text check; a failing block gets a re-take
     with a different seed and the better take is kept;
  5. loudness measured per block (outliers flagged).
Then all blocks are joined with app-level pauses, the whole narration is
normalized to one loudness target (one constant gain — timings stay
exact) and a timeline with global word times is written.

Results are cached by content: an unchanged block is never paid for
twice. Output: <work_dir>/<case>/audio/<lang>/v<story_version>/
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import statistics
from pathlib import Path

from app.core.ai_config import ai_config
from app.core.concurrency import slot
from app.documentary import audio as A
from app.documentary import storage
from app.documentary.asr import ASRUnavailable, compare_transcript, get_asr
from app.documentary.pronunciation_trace import BlockDictionaries, Trace
from app.documentary.reading.words import key_of
from app.documentary.voice_performance import scrub_forbidden
from app.providers.voice import VoiceProvider, VoiceRequest, get_voice_provider

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
BREATH_KINDS = ('paragraph', 'breath')     # the pauses a language's `breath_gap_scale` shortens; chapter breaks and music moments keep theirs


def _rel(path: str | Path) -> str:
    """Repo-relative path for manifests (portable across machines)."""
    p = Path(path).resolve()
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def tag_mask(chars: list[str]) -> list[bool]:
    """True for characters inside an audio tag ("[whispers]"): the voice
    does not speak them, so they never count as words or speech time."""
    mask, inside = [], False
    for c in chars:
        if c == "[":
            inside = True
        mask.append(inside)
        if c == "]":
            inside = False
    return mask


def words_from_alignment(
    chars: list[str], starts: list[float], ends: list[float]
) -> list[dict]:
    """Group the provider's character timing into word timing (audio tags
    and pure punctuation such as "..." are not words)."""
    words: list[dict] = []
    cur, ws, we = "", 0.0, 0.0

    def flush():
        nonlocal cur
        if cur and any(ch.isalnum() for ch in cur):
            words.append({"word": cur, "start": round(ws, 3), "end": round(we, 3)})
        cur = ""

    for c, s, e, tag in zip(chars, starts, ends, tag_mask(chars)):
        if c.isspace() or tag:
            flush()
            continue
        if not cur:
            ws = s
        cur += c
        we = e
    flush()
    return words


def _spoken_span(chars: list[str], mask: list[bool], starts: list[float],
                 ends: list[float], s: int, e: int) -> tuple[float, float] | None:
    idx = [i for i in range(max(s, 0), min(e, len(chars)))
           if not chars[i].isspace() and not mask[i]]
    if not idx:
        return None
    return starts[idx[0]], ends[idx[-1]]


def beat_times(beats: list[dict], text: str, alignment: dict, t0: float) -> list[dict]:
    """Start/end time (block-local, after trimming) of each beat part in
    a block, from the provider's per-character timing. Falls back to a
    proportional estimate if the alignment does not match the text."""
    chars, starts, ends = (alignment["characters"], alignment["starts"],
                           alignment["ends"])
    exact = "".join(chars) == text
    mask = tag_mask(chars)
    out = []
    for bt in beats:
        s, e = bt["start_char"], bt["end_char"]
        if exact:
            idx = [i for i in range(s, min(e, len(chars)))
                   if not chars[i].isspace() and not mask[i]]
            if not idx:
                continue
            start, end = starts[idx[0]], ends[idx[-1]]
        else:
            total = ends[-1] if ends else 0.0
            start = total * s / max(len(text), 1)
            end = total * e / max(len(text), 1)
        out.append({"beat_id": bt["beat_id"],
                    "start": round(max(start - t0, 0.0), 3),
                    "end": round(max(end - t0, 0.0), 3)})
    return out


def sentence_times(sentences: list[dict], text: str, alignment: dict, t0: float
                   ) -> list[dict]:
    """Block-local start/end of every sentence (its tts form is found in
    the request text in order). Sentences that cannot be located get a
    proportional estimate between their neighbours."""
    chars, starts, ends = (alignment["characters"], alignment["starts"],
                           alignment["ends"])
    exact = "".join(chars) == text
    mask = tag_mask(chars)
    total = ends[-1] if ends else 0.0
    out, cursor = [], 0
    for k, sn in enumerate(sentences):
        form = sn.get("tts") or sn["speech"]
        pos = text.find(form, cursor)
        span = None
        if pos >= 0:
            cursor = pos + len(form)
            if exact:
                span = _spoken_span(chars, mask, starts, ends, pos, pos + len(form))
            else:
                span = (total * pos / max(len(text), 1),
                        total * (pos + len(form)) / max(len(text), 1))
        if span is None:
            span = (total * k / max(len(sentences), 1),
                    total * (k + 1) / max(len(sentences), 1))
        out.append({"speech": sn["speech"], "display": sn.get("display") or sn["speech"],
                    "level": sn.get("level"),
                    "start": round(max(span[0] - t0, 0.0), 3),
                    "end": round(max(span[1] - t0, 0.0), 3)})
    return out


def select_blocks(blocks: list[dict], max_seconds: float | None) -> list[dict]:
    """Leading blocks up to roughly max_seconds of estimated speech
    (whole blocks only — a block is never cut)."""
    if not max_seconds:
        return list(blocks)
    out, total = [], 0.0
    for b in blocks:
        if out and total >= max_seconds:
            break
        out.append(b)
        total += b["est_seconds"]
    return out


class VoiceRenderer:
    def __init__(self, provider: VoiceProvider | None = None, asr=None,
                 use_asr: bool = True, listener=None, use_listener: bool = True,
                 db=None, budget_case_id: int | None = None,
                 budget_stage: str | None = None, arbiter=None,
                 documentary_job_id: int | None = None,
                 budget_pilot_seconds: float | None = None):
        self.cfg = ai_config.voice
        self.loud = ai_config.loudness
        self.asr_cfg = ai_config.asr_check
        self.pron_cfg = ai_config.pronunciation
        self.provider = provider or get_voice_provider()
        self.db = db
        self.budget_case_id = budget_case_id
        self.budget_stage = budget_stage
        self.documentary_job_id = documentary_job_id
        self.budget_pilot_seconds = budget_pilot_seconds
        self._budget_usage_missing = False
        self.dictionaries: list[dict] = []        # the film's pronunciation dictionary locators (a prepared language)
        self.rules: list[dict] = []               # the rules of that dictionary (the plan carries them): each block's text is checked against them
        self.block_dictionaries: BlockDictionaries | None = None
        self.trace = Trace(None)                  # what every take was sent with
        self.reviewer = None                      # the semantic audio reviewer (semantic_review.Reviewer) of a reviewed language
        self.seed_bumps: dict[str, int] = {}      # block -> retakes already made by the reading loop (each is a new seed)
        self.voicing = None                       # the reading loop's Voicing of a language it covers: the fixed words go to the voice as IPA
        self.planner = None                       # the risk engine and the intervention planner of that language: what each word is given, and why
        self.narration = False                    # set while the film's narration is rendered: only it gets the language's post speed
        self.asr_error: str | None = None
        # phoneme listener for the pronunciation loop (loaded when needed)
        self._listener = listener
        self._use_listener = use_listener
        self.listener_error: str | None = None
        # decides, for a word that stayed wrong, whether the voice or the key was wrong
        self.arbiter = arbiter
        if asr is not None or not use_asr:
            self.asr = asr
        else:
            try:
                self.asr = get_asr()
            except ASRUnavailable as e:
                self.asr, self.asr_error = None, str(e)

    # ------------------------------------------------------------------
    # paths / cache
    # ------------------------------------------------------------------

    @staticmethod
    def out_dir_for(case_id: int, language: str, story_version_id: int) -> Path:
        base = Path(ai_config.voice.work_dir)
        if not base.is_absolute():
            base = ROOT / base
        if ai_config.ppie.fresh(language):           # a PPIE narration has a folder of its own: no earlier narration of the version is touched
            return base / str(case_id) / "audio" / language / f"v{story_version_id}{ai_config.ppie.folder_suffix}"
        prepared = "_prepared" if language in ai_config.voice.prepared_languages else ""      # the earlier narration of the version stays where it is
        reviewed = ai_config.semantic_review.folder_suffix if language in ai_config.semantic_review.languages else ""
        return base / str(case_id) / "audio" / language / f"v{story_version_id}{prepared}{reviewed}"

    def save_plan(self, out: Path) -> str | None:
        """The planner's decisions of this render, next to the narration (the risk of every changed or escalated word, with its evidence)."""
        if self.planner is None or not self.planner.plans:
            return None
        path = out / "pronunciation_plan.json"
        storage.write_atomic(path, json.dumps(self.planner.report(), ensure_ascii=False, indent=1))
        return str(path)

    def prepared(self, language: str) -> bool:
        """Whether the language's requests carry the film's pronunciation dictionary (never for a PPIE language: only the planner prepares its text)."""
        return language in self.cfg.prepared_languages and not ai_config.ppie.fresh(language)

    def out_dir(self, case_id: int, language: str, story_version_id: int) -> Path:
        return self.out_dir_for(case_id, language, story_version_id)

    @staticmethod
    def cache_key(req: VoiceRequest, output_format: str) -> str:
        payload = json.dumps(
            {
                "text": req.text, "prev": req.previous_text, "next": req.next_text,
                "voice": req.voice_id, "model": req.model_id,
                **({"lang": req.language_code} if req.language_code else {}),
                **({"dictionaries": req.pronunciation_dictionaries} if req.pronunciation_dictionaries else {}),
                "settings": req.settings, "seed": req.seed, "format": output_format,
            },
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------
    # one take
    # ------------------------------------------------------------------

    async def _take(self, block: dict, req: VoiceRequest, blocks_dir: Path,
                    language: str) -> dict:
        fmt = getattr(getattr(self.provider, "cfg", None), "output_format",
                      ai_config.voice.elevenlabs.output_format)
        key = self.cache_key(req, fmt)
        # The cache key is part of the file NAME (not a suffix), so a
        # changed text/voice/seed can never reuse a stale take.
        stem = f"{block['block_id']}__{key}"
        raw, meta_path, wav = (
            blocks_dir / f"{stem}.mp3", blocks_dir / f"{stem}.json",
            blocks_dir / f"{stem}.wav",
        )
        if not (raw.exists() and meta_path.exists()):
            # Same take under another block id (e.g. after re-segmenting):
            # the content key decides, so it is reused, not paid again.
            other = next(iter(sorted(blocks_dir.glob(f"*__{key}.json"))), None)
            if other is not None and other.with_suffix(".mp3").exists():
                raw, meta_path = other.with_suffix(".mp3"), other
        meta = None
        if raw.exists() and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if not isinstance((meta.get("alignment") or {}).get("characters"), list):
                    raise ValueError("sidecar without alignment")
            except (ValueError, AttributeError, OSError):
                log.warning("voice take %s: unreadable sidecar, paying the take again",
                            meta_path.name)
                meta = None
                raw, meta_path = blocks_dir / f"{stem}.mp3", blocks_dir / f"{stem}.json"
        cache_hit = meta is not None
        if not cache_hit:
            if self.db is not None and self.budget_case_id is not None:
                from app.services.budget import BudgetService

                BudgetService(self.db).check(self.budget_case_id, self.budget_stage or f"tts:{language}")
            res = await self.provider.synthesize(req)
            if res.character_cost is None:
                self._budget_usage_missing = True
            if self.db is not None and self.budget_case_id is not None and res.character_cost is not None:
                from app.services.budget import BudgetService
                from app.services.cost_model import load_pricing

                price = float(load_pricing()["tts"]["eleven_v4"]["eur_per_1m_characters"])
                BudgetService(self.db).record(
                    case_id=self.budget_case_id,
                    stage=self.budget_stage or f"tts:{language}",
                    model=res.model_id,
                    units=res.character_cost,
                    cost_eur=float(res.character_cost) / 1_000_000 * price,
                    estimated=False,
                    documentary_job_id=self.documentary_job_id,
                )
            # the audio first, the sidecar last: a take counts as saved
            # only when both exist, and neither is ever half written
            storage.write_atomic(raw, res.audio)
            meta = {
                "block_id": block["block_id"], "cache_key": key,
                "text": req.text, "voice_id": res.voice_id,
                "model_id": res.model_id, "settings": req.settings,
                "seed": req.seed, "provider": res.provider,
                "request_id": res.request_id,
                "language_code": req.language_code, "previous_text": req.previous_text, "next_text": req.next_text,
                "dictionaries": req.pronunciation_dictionaries, "rules_sent": req.pronunciation_rules,
                "character_cost": res.character_cost,
                "alignment": {
                    "characters": res.characters,
                    "starts": res.char_starts, "ends": res.char_ends,
                },
            }
            storage.write_atomic(meta_path, json.dumps(meta, ensure_ascii=False))

        al = meta["alignment"]
        tempo = self.cfg.post_speed.get(language, 1.0) if self.narration else 1.0
        if tempo != 1.0:         # every time of the take is a time of the sped-up audio
            al = {"characters": al["characters"], "starts": [t / tempo for t in al["starts"]], "ends": [t / tempo for t in al["ends"]]}
        words = words_from_alignment(al["characters"], al["starts"], al["ends"])
        raw_duration = await asyncio.to_thread(A.probe_duration, raw) / tempo
        speech_start = words[0]["start"] if words else 0.0
        speech_end = words[-1]["end"] if words else raw_duration
        t0 = max(0.0, speech_start - self.cfg.lead_pad_ms / 1000)
        t1 = min(raw_duration, speech_end + self.cfg.tail_pad_ms / 1000)
        edge = self.cfg.edges.get(language) if self.narration else None
        fades, ends = (0.0, 0.0, 0.0, 0.0), None
        if edge is not None:               # a language cut by its sound: where the voice really begins and ends, with a fade at each cut
            on, off = await asyncio.to_thread(A.audible_span, raw, edge.floor_db)
            on, off = on / tempo, off / tempo
            if off > on:
                lead, tail = edge.lead_keep_ms / 1000, edge.tail_keep_ms / 1000
                t0, t1 = max(0.0, on - lead), min(raw_duration, off + tail)
                fades = (edge.fade_in_ms / 1000, edge.fade_out_ms / 1000, lead - (on - t0), tail - (t1 - off))     # silence is put in where the file has none around its sound
                ends = {"lead_ms": round(lead * 1000), "tail_ms": round(tail * 1000), "raw_lead_ms": round((on - t0) * 1000), "raw_tail_ms": round((t1 - off) * 1000)}
        await asyncio.to_thread(A.to_wav, raw, wav, self.loud.sample_rate, t0, t1, tempo, *fades)
        duration = await asyncio.to_thread(A.probe_duration, wav)
        local_words = [
            {**w, "start": round(w["start"] - t0, 3), "end": round(w["end"] - t0, 3)}
            for w in words
        ]
        local_beats = beat_times(block.get("beats") or [], req.text, al, t0)
        local_sentences = (sentence_times(block["sentences"], req.text, al, t0)
                           if block.get("sentences") else [])

        asr = None
        if self.asr is not None and ai_config.listens_after_voice(language):
            # The check compares what people should hear (no tags, no
            # harakat added for pronunciation) with what Whisper heard.
            heard_text = await self._heard(wav, meta, meta_path, language)
            expected = block.get("display_text") or block["text"]
            asr = compare_transcript(expected, heard_text, language, self.asr_cfg)
            asr["heard_text"] = heard_text
        return {
            "cache_key": key, "cache_hit": cache_hit, "seed": req.seed,
            "raw_path": str(raw), "wav_path": str(wav),
            "raw_duration": round(raw_duration, 3),
            "trimmed": {"start": round(t0, 3), "end": round(t1, 3)},
            "duration": round(duration, 3), "words": local_words,
            "beats": local_beats, "sentences": local_sentences,
            "character_cost": None if cache_hit else meta.get("character_cost"),
            "request_id": meta.get("request_id"), "asr": asr,
            "text": req.text, "alignment": al, "edges": ends,
        }

    async def _heard(self, wav: Path, meta: dict, meta_path: Path, language: str) -> str:
        """What the speech-to-text model heard in this take, remembered with the take: the same audio, language and
        model are never transcribed twice (the comparison with the script is cheap and redone with today's rules)."""
        cfg = self.asr_cfg.for_language(language)
        key = hashlib.sha256(wav.read_bytes() + f"|{language}|{cfg.model}|{cfg.compute_type}".encode()).hexdigest()
        if (meta.get("heard") or {}).get("key") == key:
            return meta["heard"]["text"]
        async with slot("asr"):
            heard = await asyncio.to_thread(self.asr.transcribe, str(wav), language)
        meta["heard"] = {"key": key, "text": heard["text"]}
        storage.write_atomic(meta_path, json.dumps(meta, ensure_ascii=False))
        return heard["text"]

    @staticmethod
    def _take_rank(take: dict) -> tuple:
        wrong = sum(1 for r in take.get("pronunciation") or [] if r.get("ok") is False)
        asr = take.get("asr")
        if not asr:
            return (wrong, 0, 0.0)
        return (wrong, 0 if asr["passed"] else 1, asr["word_error_rate"])

    def listener(self):
        """The phoneme listener, or None (unavailable: recorded once)."""
        if self._listener is None and self._use_listener and self.listener_error is None:
            from app.documentary.pronunciation import ListenerUnavailable, get_listener
            try:
                self._listener = get_listener()
            except (ListenerUnavailable, OSError) as e:
                self.listener_error = str(e)[:300]
        return self._listener

    async def _listen(self, take: dict, sentences: list[dict], occurrences: list[dict]
                      ) -> list[dict]:
        from app.documentary.pronunciation import listen

        lst = self.listener()
        if lst is None:
            return []
        async with slot("asr"):
            frames = await asyncio.to_thread(lst.frames, take["wav_path"])
        return listen(frames, take["text"], sentences, occurrences, take["alignment"],
                      take["trimmed"]["start"], self.pron_cfg)

    async def speak_block(self, b: dict, language: str, lang_cfg, settings: dict, seed: int,
                          blocks_dir: Path, synonyms: bool = True) -> dict:
        """Takes of one block: a wrong-sounding word gets its next pronunciation fix and
        the block is spoken again (pronunciation loop); a failed Whisper check gets a take
        with another seed. The story's blocks and the host's appearances both go through here."""
        from app.documentary.pronunciation import apply_fixes, risky_occurrences

        takes: list[dict] = []
        prepared = self.prepared(language)                       # edited before the voice: its dictionary goes with every request
        listened = ai_config.listens_after_voice(language)       # one take per block when it is not: nothing is listened to afterwards
        sentences = [{**x, "tts": scrub_forbidden(x["tts"])} if x.get("tts") else dict(x)
                     for x in b.get("sentences") or []]
        # `synonyms=False`: the words are frozen (the host's text); only what the voice is
        # given may change
        occurrences = (risky_occurrences(sentences, synonyms)
                       if self.pron_cfg.enabled and language in self.pron_cfg.languages
                       and sentences and listened else [])
        if occurrences and self.listener() is None:
            occurrences = []
        text = scrub_forbidden(b.get("tts_text") or b["text"])
        planned_rules: list[dict] = []
        if self.voicing is not None and self.planner is not None:
            plan = self.planner.plan(b["block_id"], text, self.voicing, allow_dictionary=prepared and bool(self.dictionaries),
                                     covered=frozenset(key_of(r.get("string_to_replace") or "") for r in self.rules))
            self.planner.register(plan)
            text, planned_rules = plan.text, plan.rules
        elif self.voicing is not None:
            text = self.voicing.text(b["block_id"], text)
        asr_cfg = self.asr_cfg.for_language(language)
        asr_retakes = asr_cfg.auto_retakes if asr_cfg.mode == "strict" and listened else 0
        rounds = self.pron_cfg.max_rounds if occurrences else 0
        fixes: list[dict] = []
        for attempt in range(1 + asr_retakes + rounds):
            blk = b
            if sentences:
                # what people read follows a synonym fix (never harakat)
                blk = {**b, "sentences": sentences, "display_text": " ".join(
                    x.get("display") or x["speech"] for x in sentences)}
            locators = list(self.dictionaries) if prepared and not b.get("no_dictionary") else []
            resolution = (await self.block_dictionaries.resolve(b["block_id"], text, b.get("tts_text") or b["text"], planned_rules)
                          if locators and self.block_dictionaries is not None else None)
            req = VoiceRequest(
                text=text, voice_id=lang_cfg.voice_id,
                model_id=lang_cfg.model_id, settings=settings,
                previous_text=b.get("previous_text") or "",
                next_text=b.get("next_text") or "",
                seed=seed, language=language,
                language_code=lang_cfg.language_code,
                pronunciation_dictionaries=resolution.locators if resolution else locators,
                pronunciation_rules=resolution.rules_sent if resolution else [],
            )
            take = await self._take(blk, req, blocks_dir, language)
            if resolution is not None:
                self.trace.record(block_id=b["block_id"], attempt=attempt, text=text, request=req, resolution=resolution, take=take)
            if occurrences:
                take["pronunciation"] = await self._listen(take, sentences, occurrences)
            take["round"] = attempt
            takes.append(take)
            wrong = [r for r in take.get("pronunciation") or [] if r.get("ok") is False]
            if wrong and rounds > 0:
                sentences, applied = apply_fixes(sentences, occurrences, wrong)
                if applied:
                    rounds -= 1
                    fixes.append({"after_round": attempt, "fixes": applied})
                    text = " ".join(x.get("tts") or x["speech"] for x in sentences)
                    continue
            if take["asr"] and not take["asr"]["passed"] and asr_retakes > 0:
                asr_retakes -= 1
                seed += 1
                continue
            break
        best = min(takes, key=self._take_rank)
        await self._referee(best, sentences, language)
        return {"block": b, "take": best, "attempts": len(takes), "takes": takes,
                "fixes": fixes}

    async def _referee(self, take: dict, sentences: list[dict], language: str) -> None:
        """Words still wrong after the whole ladder go to the referee: when the key was the
        mistake (the heard sounds are a right reading), the word is judged again against the
        corrected reading and passes if it matches."""
        from app.documentary.pronunciation import reading_vowels, vowel_distance

        wrong = [r for r in take.get("pronunciation") or [] if r.get("ok") is False]
        if not wrong or self.arbiter is None or self.budget_case_id is None:
            return
        items = [{"i": k, "sentence": sentences[r["sentence"]]["speech"], "word": r["word"],
                  "meaning": next((w.get("meaning") for w in sentences[r["sentence"]].get("risky") or []
                                   if w["w"] == r["word"]), None),
                  "key_read": r["read"], "heard_phonemes": r.get("heard_ipa", ""),
                  "form_sent": r.get("form") or r["word"]}
                 for k, r in enumerate(wrong)]
        verdicts = await self.arbiter.arbitrate(self.db, self.budget_case_id, language, items)
        for k, r in enumerate(wrong):
            v = verdicts.get(k)
            r["referee"] = v["verdict"] if v else "unanswered"
            if v and v["verdict"] == "key_wrong":
                # the vowels the listener already cut out for this word, against the new reading
                dist, hard = vowel_distance(reading_vowels(v["read"]), r.get("heard") or [])
                r["referee_read"] = v["read"]
                if r.get("heard") and hard == 0 and dist <= self.pron_cfg.max_vowel_distance:
                    r["ok"], r["read"] = True, v["read"]

    # ------------------------------------------------------------------
    # whole plan
    # ------------------------------------------------------------------

    def check_joins(self, language: str, results: list[dict], timeline: list[dict]) -> dict:
        """Every join of the assembled narration measured and checked (config voice.joins.check): the silence between the last sound of a block and the
        first sound of the next, a block that begins or ends hard (no silence around its sound), a join with more silence than allowed. A language that is
        not cut by its sound is not checked."""
        join = self.cfg.joins.get(language)
        if join is None or not self.cfg.edges.get(language):
            return {"checked": 0, "flagged": [], "silence_ms": {}}
        silences, flagged = [], []
        padded = sum(1 for r in results if (r["take"].get("edges") or {}).get("raw_lead_ms", 10**6) < join.min_lead_ms)           # files that began with their sound: silence was put in front
        for i in range(len(results) - 1):
            a, b = results[i]["take"].get("edges"), results[i + 1]["take"].get("edges")
            if not a or not b:
                continue
            silence = a["tail_ms"] + timeline[i]["pause_after_ms"] + b["lead_ms"]
            silences.append(silence)
            flags = []
            if b["lead_ms"] < join.min_lead_ms:
                flags.append("join_hard_start")
            if a["tail_ms"] < join.min_tail_ms:
                flags.append("join_hard_end")
            kind = timeline[i].get("pause_after_kind")
            if (kind in join.silence_ms or kind in (None, "paragraph")) and silence > join.max_silence_ms:
                flags.append("join_too_long")
            if flags:
                flagged.append({"after": results[i]["block"]["block_id"], "silence_ms": silence, "flags": flags})
        ordered = sorted(silences)
        return {"checked": len(silences), "flagged": flagged, "files_given_a_lead_in": padded,
                "silence_ms": {"median": ordered[len(ordered) // 2], "max": ordered[-1], "min": ordered[0]} if ordered else {}}

    def join_gap_ms(self, language: str, block: dict, following: dict, planned_ms: int) -> int:
        """The silence put between two blocks so that the silence between the last sound of one and the first sound of the next is the configured one
        (config voice.joins): the next block goes on in the same beat of the story, or the planned kind of the pause; a kind not listed keeps the planned pause."""
        join, edge = self.cfg.joins[language], self.cfg.edges[language]
        kind = block.get("pause_after_kind")
        same_beat = following["section_id"].split(".")[0] == block["section_id"].split(".")[0]
        target = join.silence_ms.get("same_beat" if kind in (None, "paragraph") and same_beat else kind)
        return planned_ms if target is None else max(0, target - edge.tail_keep_ms - edge.lead_keep_ms)

    async def render(
        self, plan: dict, *, case_id: int, story_version_id: int,
        max_seconds: float | None = None, style: str | None = None,
        force_block_ids: list[str] | None = None,
        fixed_requests: dict[str, VoiceRequest] | None = None, output_dir: Path | None = None,
    ) -> dict:
        """`fixed_requests`: block -> the exact request of the take the narration is assembled from (a take already made is found by its content and
        never paid again). `output_dir`: where the narration and its manifest are written (the takes stay in the narration's own `blocks` folder)."""
        language = plan["language"]
        self.narration = True
        lang_cfg = self.cfg.for_language(language)
        if self.prepared(language):
            self.dictionaries = list(plan.get("pronunciation_dictionary") or [])
            self.rules = list(plan.get("pronunciation_rules") or [])
        # An explicit style overrides the performance script (A/B tests);
        # otherwise each block uses its own style, else the default.
        for name in {style} | {b.get("style") for b in plan["blocks"]}:
            if name and name not in self.cfg.styles:
                raise ValueError(f"Unknown voice style {name!r}")
        force = set(force_block_ids or [])

        def block_style(b: dict) -> str:
            return style or b.get("style") or self.cfg.default_style

        out = self.out_dir(case_id, language, story_version_id)
        blocks_dir = out / "blocks"
        blocks_dir.mkdir(parents=True, exist_ok=True)
        dest = output_dir or out
        dest.mkdir(parents=True, exist_ok=True)
        self.block_dictionaries = BlockDictionaries(self.provider, self.rules, self.dictionaries, language=language, case_id=case_id,
                                                    version_id=story_version_id, folder=out)
        self.trace = Trace(self.block_dictionaries)
        chosen = select_blocks(plan["blocks"], max_seconds)

        async def one_block(b: dict) -> dict:
            if fixed_requests and b["block_id"] in fixed_requests:
                take = await self._take(b, fixed_requests[b["block_id"]], blocks_dir, language)
                return {"block": b, "take": take, "attempts": 1, "takes": [take], "fixes": []}
            # A forced re-render is a NEW take (different seed), still
            # reproducible; normal renders reuse the cached take.
            seed = self.cfg.seed + (10 if b["block_id"] in force else 0) + self.seed_bumps.get(b["block_id"], 0)
            settings = self.cfg.settings_for(block_style(b), language)

            async def speak(block: dict, take_seed: int) -> dict:
                return await self.speak_block(block, language, lang_cfg, settings, take_seed, blocks_dir)

            result = await speak(b, seed)
            if self.reviewer is not None:          # after the voice-repair loop: a model listens to the block
                result = await self.reviewer.run(result, speak, seed, language)
            return result

        # All blocks at once: the provider's limit (concurrency
        # .elevenlabs_tts) and the speech-to-text limit pace the requests.
        held: list[dict] = []
        results: list[dict] = list(await asyncio.gather(*(one_block(b) for b in chosen)))
        if self.reviewer is not None:
            from app.documentary import semantic_review as SR

            SR.save_records(out, self.reviewer.records)
            held = SR.failed_blocks(self.reviewer.records)
            if held and not ai_config.semantic_review.failed_blocks_wait_for_owner:      # a block that does not pass is not published: the film is not assembled
                raise SR.SemanticReviewFailed(held)

        # --- per-block loudness -------------------------------------
        for r in results:
            r["loudness"] = await asyncio.to_thread(
                A.measure_loudness, r["take"]["wav_path"],
                self.loud.narration_target_lufs, self.loud.true_peak_db,
                self.loud.lra,
            )
        levels = [r["loudness"]["integrated_lufs"] for r in results
                  if r["loudness"]["integrated_lufs"] is not None]
        median = statistics.median(levels) if levels else None

        # --- assemble with app-level pauses ---------------------------
        pieces: list[str] = []
        timeline_blocks: list[dict] = []
        words_global: list[dict] = []
        sentences_global: list[dict] = []
        beat_spans: dict[str, dict] = {}
        t = 0.0
        sr = self.loud.sample_rate
        for i, r in enumerate(results):
            b, take = r["block"], r["take"]
            pieces.append(take["wav_path"])
            start = t
            t += take["duration"]
            gap_ms = 0
            if i + 1 < len(results):
                if b.get("pause_after_ms") is not None:
                    gap_ms = int(b["pause_after_ms"])  # performance script
                    if b.get("pause_after_kind") in BREATH_KINDS:
                        gap_ms = round(gap_ms * self.cfg.breath_gap_scale.get(language, 1.0))
                else:
                    same = results[i + 1]["block"]["section_id"] == b["section_id"]
                    gap_ms = (self.cfg.between_blocks_ms if same
                              else self.cfg.between_sections_ms)
            if i + 1 < len(results) and self.cfg.joins.get(language) and self.cfg.edges.get(language):
                gap_ms = self.join_gap_ms(language, b, results[i + 1]["block"], gap_ms)
            timeline_blocks.append({
                "block_id": b["block_id"], "section_id": b["section_id"],
                "style": block_style(b),
                "start": round(start, 3), "end": round(t, 3),
                "pause_after_ms": gap_ms,
                "pause_after_kind": b.get("pause_after_kind"),
                "wav": _rel(take["wav_path"]),
            })
            for w in take["words"]:
                words_global.append({
                    **w, "start": round(w["start"] + start, 3),
                    "end": round(w["end"] + start, 3), "block_id": b["block_id"],
                })
            for sn in take.get("sentences") or []:
                sentences_global.append({
                    **sn, "start": round(sn["start"] + start, 3),
                    "end": round(sn["end"] + start, 3), "block_id": b["block_id"],
                })
            for bt in take.get("beats") or []:
                span = beat_spans.setdefault(
                    bt["beat_id"], {"beat_id": bt["beat_id"],
                                    "start": bt["start"] + start, "end": 0.0})
                span["end"] = round(bt["end"] + start, 3)
                span["start"] = round(min(span["start"], bt["start"] + start), 3)
            if i + 1 < len(results):
                if gap_ms:
                    gap = blocks_dir / f"silence_{gap_ms}ms.wav"
                    if not gap.exists():
                        await asyncio.to_thread(A.silence_wav, gap, gap_ms / 1000, sr)
                    pieces.append(str(gap))
                    t += gap_ms / 1000

        raw_mix = dest / "narration_unnormalized.wav"
        final_wav = dest / "narration.wav"
        final_mp3 = dest / "narration.mp3"
        await asyncio.to_thread(A.concat_wavs, pieces, raw_mix)
        before = await asyncio.to_thread(
            A.normalize_loudness, raw_mix, final_wav,
            self.loud.narration_target_lufs, self.loud.true_peak_db,
            self.loud.lra, sr,
        )
        after = await asyncio.to_thread(
            A.measure_loudness, final_wav, self.loud.narration_target_lufs,
            self.loud.true_peak_db, self.loud.lra,
        )

        # --- dynamic EQ / de-esser (post-TTS polish) --------------------
        # Always fed the ORIGINAL normalized narration; the enhanced
        # version is a separate file and only becomes the active track
        # when processing genuinely applied. A failure ships the
        # original — narration is never lost to a processing error.
        from app.documentary import dynamic_eq as DEQ

        active_wav = final_wav
        try:
            eq_report = await asyncio.to_thread(
                DEQ.process_into, final_wav, dest, language,
                ai_config.dynamic_eq.resolved(language), sr,
            )
        except Exception as e:  # noqa: BLE001 — error recovery, not a crash
            eq_report = {"enabled": ai_config.dynamic_eq.enabled,
                         "applied": False, "active": "original",
                         "error": f"{type(e).__name__}: {e}"[:300]}
        if eq_report.get("applied"):
            active_wav = dest / "narration_enhanced.wav"
        else:
            (dest / "narration_original.mp3").unlink(missing_ok=True)

        await asyncio.to_thread(A.encode_mp3, active_wav, final_mp3)
        if eq_report.get("applied"):
            await asyncio.to_thread(
                A.encode_mp3, final_wav, dest / "narration_original.mp3")
        raw_mix.unlink(missing_ok=True)
        total = await asyncio.to_thread(A.probe_duration, final_wav)

        # --- QA ------------------------------------------------------
        qa_blocks, flags = [], []
        chars_paid = 0
        for r in results:
            b, take, loud = r["block"], r["take"], r["loudness"]
            dev = (None if median is None or loud["integrated_lufs"] is None
                   else round(loud["integrated_lufs"] - median, 2))
            asr = take["asr"]
            block_flags = []
            if asr and not asr["passed"]:
                block_flags.append("asr_check_failed" if asr.get("mode", "strict") == "strict"
                                   else "asr_name_or_number_warning")
            if dev is not None and abs(dev) > self.loud.max_block_deviation_lu:
                block_flags.append("loudness_outlier")
            if b.get("oversize"):
                block_flags.append("oversize_sentence")
            if b["block_id"] in {h["block_id"] for h in held}:
                block_flags.append("semantic_review_failed")
            pron = take.get("pronunciation") or []
            unresolved = [x for x in pron if x.get("ok") is False]
            if unresolved:
                block_flags.append("pronunciation_unresolved")
            flags.extend(f"{b['block_id']}:{f}" for f in block_flags)
            paid = sum(tk["character_cost"] or 0 for tk in r["takes"])
            chars_paid += paid
            words_n = b["word_count"]
            qa_blocks.append({
                "block_id": b["block_id"], "section_id": b["section_id"],
                "words": words_n, "est_seconds": b["est_seconds"],
                "actual_seconds": take["duration"],
                "words_per_minute": round(words_n / take["duration"] * 60, 1)
                if take["duration"] else None,
                "attempts": r["attempts"], "seed": take["seed"],
                "cache_hit": take["cache_hit"], "characters_paid": paid,
                "style": block_style(b), "level": b.get("level"),
                "tts_text": take.get("text") if take.get("text") != b["text"] else None,
                "pronunciation": None if not pron else {
                    "words": [{k: x.get(k) for k in ("word", "read", "form", "level", "ok",
                                                     "expected", "heard", "heard_ipa",
                                                     "distance", "reason", "at")}
                              for x in pron],
                    "rounds": len(r["takes"]),
                    "fixes": r.get("fixes") or [],
                    "unresolved": [x["word"] for x in unresolved],
                    "per_round": [
                        {"round": tk.get("round"),
                         "wrong": [x["word"] for x in tk.get("pronunciation") or []
                                   if x.get("ok") is False]}
                        for tk in r["takes"]],
                },
                "beat_ids": [bt["beat_id"] for bt in b.get("beats") or []],
                "loudness_lufs": loud["integrated_lufs"],
                "loudness_deviation_lu": dev,
                "asr": None if not asr else {
                    k: asr.get(k) for k in ("passed", "failures",
                                            "word_error_rate",
                                            "longest_missing_run",
                                            "longest_extra_run", "diffs",
                                            "spelling_variants", "heard_text")
                },
                "flags": block_flags,
            })

        joins = self.check_joins(language, results, timeline_blocks)
        flags += [f"{j['after']}:{flag}" for j in joins["flagged"] for flag in j["flags"]]
        trace_info = self.trace.save(dest) if self.trace.entries else None
        plan_info = self.save_plan(dest)
        manifest = {
            "case_id": case_id, "story_version_id": story_version_id,
            "language": language, "voice_id": lang_cfg.voice_id,
            "model_id": lang_cfg.model_id,
            "style_override": style,
            "styles_used": sorted({block_style(r["block"]) for r in results}),
            "provider": self.provider.name,
            "asr": (self.asr.name if self.asr else None),
            "asr_error": self.asr_error,
            "pronunciation_listener": (getattr(self._listener, "name", None)
                                       if self._listener else None),
            "pronunciation_error": self.listener_error,
            "blocks_rendered": len(results),
            "blocks_in_plan": len(plan["blocks"]),
            "duration_seconds": round(total, 3),
            "estimated_seconds": round(sum(b["est_seconds"] for b in chosen), 1),
            "characters_paid": chars_paid,
            "loudness": {
                "target_lufs": self.loud.narration_target_lufs,
                "before_lufs": before["integrated_lufs"],
                "after_lufs": after["integrated_lufs"],
                "after_true_peak_db": after["true_peak_db"],
                "block_median_lufs": median,
            },
            "flags": flags,
            "pronunciation_trace": trace_info,
            "pronunciation_plan": plan_info,
            "joins": joins,
            "semantic_review_failed": held,
            "dynamic_eq": eq_report,
            "files": {
                "narration_wav": _rel(final_wav), "narration_mp3": _rel(final_mp3),
                "manifest": _rel(dest / "manifest.json"),
                **({"narration_enhanced_wav": _rel(dest / "narration_enhanced.wav"),
                    "narration_original_mp3": _rel(dest / "narration_original.mp3"),
                    "dynamics_json": _rel(dest / "dynamics.json")}
                   if eq_report.get("applied") else {}),
            },
            "blocks": qa_blocks,
            "timeline": {
                "blocks": timeline_blocks,
                "beats": sorted(beat_spans.values(), key=lambda x: x["start"]),
                "words": words_global,
                "sentences": sentences_global,
            },
        }
        storage.write_atomic(dest / "manifest.json",
                             json.dumps(manifest, ensure_ascii=False, indent=2))
        if self.db is not None and self.budget_case_id is not None and self._budget_usage_missing:
            from app.services.budget import BudgetService
            from app.services.cost_model import estimate_stage_eur

            BudgetService(self.db).record(
                case_id=self.budget_case_id, stage=self.budget_stage or f"tts:{language}",
                model=lang_cfg.model_id, units=None,
                cost_eur=estimate_stage_eur(self.db, self.budget_case_id,
                                             self.budget_stage or f"tts:{language}",
                                             self.budget_pilot_seconds),
                estimated=True, documentary_job_id=self.documentary_job_id,
            )
        return manifest
