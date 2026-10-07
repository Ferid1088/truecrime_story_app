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
import statistics
from pathlib import Path

from app.core.ai_config import ai_config
from app.core.concurrency import slot
from app.documentary import audio as A
from app.documentary.asr import ASRUnavailable, compare_transcript, get_asr
from app.providers.voice import VoiceProvider, VoiceRequest, get_voice_provider

ROOT = Path(__file__).resolve().parents[2]


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
                 use_asr: bool = True, listener=None, use_listener: bool = True):
        self.cfg = ai_config.voice
        self.loud = ai_config.loudness
        self.asr_cfg = ai_config.asr_check
        self.pron_cfg = ai_config.pronunciation
        self.provider = provider or get_voice_provider()
        self.asr_error: str | None = None
        # phoneme listener for the pronunciation loop (loaded when needed)
        self._listener = listener
        self._use_listener = use_listener
        self.listener_error: str | None = None
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
        return base / str(case_id) / "audio" / language / f"v{story_version_id}"

    def out_dir(self, case_id: int, language: str, story_version_id: int) -> Path:
        return self.out_dir_for(case_id, language, story_version_id)

    @staticmethod
    def cache_key(req: VoiceRequest, output_format: str) -> str:
        payload = json.dumps(
            {
                "text": req.text, "prev": req.previous_text, "next": req.next_text,
                "voice": req.voice_id, "model": req.model_id,
                **({"lang": req.language_code} if req.language_code else {}),
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
        cache_hit = raw.exists() and meta_path.exists()
        if cache_hit:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            res = await self.provider.synthesize(req)
            raw.write_bytes(res.audio)
            meta = {
                "block_id": block["block_id"], "cache_key": key,
                "text": req.text, "voice_id": res.voice_id,
                "model_id": res.model_id, "settings": req.settings,
                "seed": req.seed, "provider": res.provider,
                "request_id": res.request_id,
                "character_cost": res.character_cost,
                "alignment": {
                    "characters": res.characters,
                    "starts": res.char_starts, "ends": res.char_ends,
                },
            }
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8"
            )

        al = meta["alignment"]
        words = words_from_alignment(al["characters"], al["starts"], al["ends"])
        raw_duration = await asyncio.to_thread(A.probe_duration, raw)
        speech_start = words[0]["start"] if words else 0.0
        speech_end = words[-1]["end"] if words else raw_duration
        t0 = max(0.0, speech_start - self.cfg.lead_pad_ms / 1000)
        t1 = min(raw_duration, speech_end + self.cfg.tail_pad_ms / 1000)
        await asyncio.to_thread(A.to_wav, raw, wav, self.loud.sample_rate, t0, t1)
        duration = await asyncio.to_thread(A.probe_duration, wav)
        local_words = [
            {**w, "start": round(w["start"] - t0, 3), "end": round(w["end"] - t0, 3)}
            for w in words
        ]
        local_beats = beat_times(block.get("beats") or [], req.text, al, t0)
        local_sentences = (sentence_times(block["sentences"], req.text, al, t0)
                           if block.get("sentences") else [])

        asr = None
        if self.asr is not None:
            # The check compares what people should hear (no tags, no
            # harakat added for pronunciation) with what Whisper heard.
            async with slot("asr"):
                heard = await asyncio.to_thread(self.asr.transcribe, str(wav), language)
            expected = block.get("display_text") or block["text"]
            asr = compare_transcript(expected, heard["text"], language, self.asr_cfg)
            asr["heard_text"] = heard["text"]
        return {
            "cache_key": key, "cache_hit": cache_hit, "seed": req.seed,
            "raw_path": str(raw), "wav_path": str(wav),
            "raw_duration": round(raw_duration, 3),
            "trimmed": {"start": round(t0, 3), "end": round(t1, 3)},
            "duration": round(duration, 3), "words": local_words,
            "beats": local_beats, "sentences": local_sentences,
            "character_cost": None if cache_hit else meta.get("character_cost"),
            "request_id": meta.get("request_id"), "asr": asr,
            "text": req.text, "alignment": al,
        }

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

    # ------------------------------------------------------------------
    # whole plan
    # ------------------------------------------------------------------

    async def render(
        self, plan: dict, *, case_id: int, story_version_id: int,
        max_seconds: float | None = None, style: str | None = None,
        force_block_ids: list[str] | None = None,
    ) -> dict:
        language = plan["language"]
        lang_cfg = self.cfg.for_language(language)
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
        chosen = select_blocks(plan["blocks"], max_seconds)

        async def one_block(b: dict) -> dict:
            """Takes of one block: a wrong-sounding word gets its next
            pronunciation fix and the block is spoken again (pronunciation
            loop); a failed Whisper check gets a take with another seed."""
            from app.documentary.pronunciation import apply_fixes, risky_occurrences

            # A forced re-render is a NEW take (different seed), still
            # reproducible; normal renders reuse the cached take.
            seed = self.cfg.seed + (10 if b["block_id"] in force else 0)
            takes: list[dict] = []
            settings = self.cfg.styles[block_style(b)].model_dump()
            sentences = [dict(x) for x in b.get("sentences") or []]
            occurrences = (risky_occurrences(sentences)
                           if self.pron_cfg.enabled and language in self.pron_cfg.languages
                           and sentences else [])
            if occurrences and self.listener() is None:
                occurrences = []
            text = b.get("tts_text") or b["text"]
            asr_retakes = self.asr_cfg.auto_retakes
            rounds = self.pron_cfg.max_rounds if occurrences else 0
            fixes: list[dict] = []
            for attempt in range(1 + asr_retakes + rounds):
                blk = b
                if sentences:
                    # what people read follows a synonym fix (never harakat)
                    blk = {**b, "sentences": sentences, "display_text": " ".join(
                        x.get("display") or x["speech"] for x in sentences)}
                req = VoiceRequest(
                    text=text, voice_id=lang_cfg.voice_id,
                    model_id=lang_cfg.model_id, settings=settings,
                    previous_text=b.get("previous_text") or "",
                    next_text=b.get("next_text") or "",
                    seed=seed, language=language,
                    language_code=lang_cfg.language_code,
                )
                take = await self._take(blk, req, blocks_dir, language)
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
            return {"block": b, "take": best, "attempts": len(takes), "takes": takes,
                    "fixes": fixes}

        # All blocks at once: the provider's limit (concurrency
        # .elevenlabs_tts) and the speech-to-text limit pace the requests.
        results: list[dict] = list(await asyncio.gather(*(one_block(b) for b in chosen)))

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
                else:
                    same = results[i + 1]["block"]["section_id"] == b["section_id"]
                    gap_ms = (self.cfg.between_blocks_ms if same
                              else self.cfg.between_sections_ms)
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

        raw_mix = out / "narration_unnormalized.wav"
        final_wav = out / "narration.wav"
        final_mp3 = out / "narration.mp3"
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
        await asyncio.to_thread(A.encode_mp3, final_wav, final_mp3)
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
                block_flags.append("asr_check_failed")
            if dev is not None and abs(dev) > self.loud.max_block_deviation_lu:
                block_flags.append("loudness_outlier")
            if b.get("oversize"):
                block_flags.append("oversize_sentence")
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
            "files": {
                "narration_wav": _rel(final_wav), "narration_mp3": _rel(final_mp3),
                "manifest": _rel(out / "manifest.json"),
            },
            "blocks": qa_blocks,
            "timeline": {
                "blocks": timeline_blocks,
                "beats": sorted(beat_spans.values(), key=lambda x: x["start"]),
                "words": words_global,
                "sentences": sentences_global,
            },
        }
        (out / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return manifest
