"""The SpokenNarrator agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.spoken."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.agents.story import (
    _join_sections,
    _mark_sections,
    build_evidence_pack,
    evidence_fingerprint,
    realign_sections,
    save_story_version,
    strip_section_markers,
    structure_without_text,
)
from app.core.ai_config import ai_config
from app.core.concurrency import gather_limited
from app.core.prompts import prompt
from app.db.models import Case, Contradiction, Fact, Source, StoryVersion
from app.documentary.blueprint import latest_blueprint, version_sections
from app.documentary.spoken import (
    MEANING_SYSTEM,
    _meaning_ok,
    _task_line,
    _words_minutes,
    beat_sections,
    clean_writer_output,
    critic_system_prompt,
    has_meta_text,
    missing_anchors,
    read_aloud_metrics,
    writer_system_prompt,
)
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationResult
from app.services.tracking import stamp_run, track_run
from app.utils import language_quality


class SpokenNarrator:
    def __init__(self, resume_dir: Path | None = None):
        """resume_dir: where a finished model answer is kept (by a hash of its role, system prompt and input), so a run that stops on a
        provider error continues without paying for the answers it already has. None = every call goes to the provider."""
        self.gen = get_generation_provider()
        self.cfg = ai_config.spoken
        self.resume_dir = resume_dir

    def _kept(self, role: str, system: str, user: str) -> tuple[Path | None, dict | None]:
        if self.resume_dir is None:
            return None, None
        path = self.resume_dir / (hashlib.sha256(json.dumps([role, system, user], ensure_ascii=False).encode()).hexdigest() + ".json")
        return path, (json.loads(path.read_text(encoding="utf-8")) if path.exists() else None)

    @staticmethod
    def _keep(path: Path | None, res: GenerationResult, data: dict | None = None) -> None:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"text": res.text, "model": res.model, "provider": res.provider, "data": data}, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------
    # model calls
    # ------------------------------------------------------------------

    async def _text(self, db, case_id, label, role, system, payload: dict,
                    task: str = ""):
        user = json.dumps(payload, ensure_ascii=False)
        if task:
            user = f"{task}\n\nINPUT:\n{user}"
        path, kept = self._kept(role, system, user)
        if kept:
            return GenerationResult(text=kept["text"], model=kept["model"], provider=kept["provider"])
        with track_run(db, case_id, label, input_summary=label) as run:
            res = await run_agent(role, self.gen, user, system=system)
            stamp_run(run, res, role)
            run.output_summary = f"words={len(res.text.split())}"
        self._keep(path, res)
        return res

    async def _json(self, db, case_id, label, role, system, payload: dict):
        user = json.dumps(payload, ensure_ascii=False)
        path, kept = self._kept(role, system, user)
        if kept:
            return kept["data"] or {}, GenerationResult(text=kept["text"], model=kept["model"], provider=kept["provider"])
        with track_run(db, case_id, label, input_summary=label) as run:
            data, res = await run_agent(role, self.gen, user, system=system)
            stamp_run(run, res, role)
        data = data if isinstance(data, dict) else {}
        self._keep(path, res, data)
        return data, res

    async def _write(self, db, case_id, language, source: list[dict],
                     uncertain: list[str]) -> tuple[list[dict], GenerationResult, list[str]]:
        """Whole story in one call; on lost markers chunks, then beats."""
        system = writer_system_prompt(language)
        log: list[str] = []

        async def call(beats: list[dict], before: str = ""):
            payload = {"language": language, "script": _mark_sections(beats),
                       "if_mentioned_keep_uncertain": uncertain}
            if before:
                payload["story_so_far_ends_with"] = before
            task = _task_line(language, len(beats))
            res = await self._text(
                db, case_id, f"Spoken Writer ({language})", "spoken_writer", system,
                payload, task,
            )
            text, issues = clean_writer_output(res.text)
            if issues:
                # The model talked to us instead of telling the story:
                # ask once more, plainly.
                log.append(f"meta_output_retry_{beats[0]['id']}")
                payload["previous_answer_rejected"] = (
                    "Your previous answer contained comments, notes or a list of "
                    "changes. Output only the spoken narration this time.")
                res = await self._text(
                    db, case_id, f"Spoken Writer ({language})", "spoken_writer",
                    system, payload, task,
                )
                text, _ = clean_writer_output(res.text)
            res.text = text
            return res

        size = self.cfg.write_beats_per_call or self.cfg.beats_per_call
        if not size:
            res = await call(source)
            sections, ok = realign_sections(source, res.text,
                                            allow_paragraph_fallback=False)
            if ok and [s["id"] for s in sections] == [s["id"] for s in source]:
                return sections, res, log
            log.append("markers_lost_whole_story")
            size = 6
        out: list[dict] = []
        for i in range(0, len(source), size):
            chunk = source[i:i + size]
            # Continuity: the next chunk hears how the last one ended.
            tail = " ".join(out[-1]["text"].split()[-60:]) if out else ""
            res = await call(chunk, tail)
            got, ok = realign_sections(chunk, res.text, allow_paragraph_fallback=False)
            if ok and [s["id"] for s in got] == [s["id"] for s in chunk]:
                out.extend(got)
                continue
            log.append(f"markers_lost_chunk_{chunk[0]['id']}")
            for beat in chunk:
                tail = " ".join(out[-1]["text"].split()[-60:]) if out else ""
                res = await call([beat], tail)
                got, ok = realign_sections([beat], res.text,
                                           allow_paragraph_fallback=False)
                text = got[0]["text"] if ok else strip_section_markers(res.text)
                out.append({"id": beat["id"], "text": text})
        return out, res, log

    async def _check(self, db, case_id, language, source: list[dict],
                     spoken: list[dict], beat_ids: set[str] | None = None,
                     style_ids: set[str] | None = None, previous: dict | None = None):
        """Meaning check + native style critic per chunk of beats (all chunks and both critics in
        parallel). The meaning check (the fact guard) covers every beat asked for; the style critic
        only `style_ids` (default: the same beats), the others keep the verdict they had in `previous`."""
        src = {s["id"]: s["text"] for s in source}
        subset = [s for s in spoken if beat_ids is None or s["id"] in beat_ids]
        if not self.cfg.llm_checks:
            backed = self._backed(language, src, subset)
            if self.cfg.addition_inspector:
                await self._inspect_additions(db, case_id, language, src, subset, backed)
            return backed, None, ""
        size = self.cfg.beats_per_call or len(subset) or 1
        chunks = [subset[i:i + size] for i in range(0, len(subset), size)]
        styled = [s for s in subset if style_ids is None or s["id"] in style_ids]
        style_chunks = [styled[i:i + size] for i in range(0, len(styled), size)]

        def meaning_payload(chunk):
            return {"source_language": language, "spoken_language": language,
                    "beats": [{"beat_id": s["id"], "source": src[s["id"]],
                               "spoken": s["text"]} for s in chunk]}

        def style_payload(chunk):
            return {"language": language, "narration": _mark_sections(chunk)}

        calls = [self._json(db, case_id, f"Spoken Meaning Check ({language})",
                            "spoken_meaning_checker", MEANING_SYSTEM, meaning_payload(chunk))
                 for chunk in chunks]
        calls += [self._json(db, case_id, f"Spoken Style Critic ({language})",
                             "spoken_style_critic", critic_system_prompt(language), style_payload(chunk))
                  for chunk in style_chunks]
        results = await gather_limited(None, calls)
        by_id_m: dict[str, dict] = {}
        by_id_s: dict[str, dict] = {}
        overall, notes = [], []
        for k, (data, _) in enumerate(results):
            for b in data.get("beats") or []:
                if isinstance(b, dict):
                    (by_id_m if k < len(chunks) else by_id_s)[str(b.get("beat_id"))] = b
            if k >= len(chunks):
                if data.get("overall"):
                    overall.append(data["overall"])
                if data.get("notes"):
                    notes.append(str(data["notes"]))
        result = {}
        for s in subset:
            m = by_id_m.get(s["id"])
            kept = (previous or {}).get(s["id"]) if s["id"] not in {x["id"] for x in styled} else None
            st = by_id_s.get(s["id"]) or {}
            verdict = kept["verdict"] if kept else (st.get("verdict") if st.get("verdict") in (
                "storyteller", "mixed", "newsreader") else "unrated")
            result[s["id"]] = {
                # A beat the checker did not return is NOT assumed fine.
                "meaning": m if m is not None else {"unchecked": True},
                "meaning_ok": m is not None and _meaning_ok(m),
                "verdict": verdict,
                "problems": kept["problems"] if kept else
                [p for p in st.get("problems") or [] if isinstance(p, dict)][:self.cfg.max_style_problems],
                "ear": read_aloud_metrics(s["text"], language),
                "length": self._length(language, src[s["id"]], s["text"]),
            }
        worst = next((v for v in ("newsreader", "mixed", "storyteller") if v in overall), None)
        return result, worst, " ".join(notes)[:800]

    async def _inspect_additions(self, db, case_id, language: str, src: dict[str, str], subset: list[dict], checks: dict) -> None:
        """The small inspector: one cheap call per chunk of beats (all chunks at once), answering only with the beats that give the listener something
        the script does not give. A flagged beat is not backed, and its quoted additions go back to the writer as the problem to fix. A beat it does not
        mention is fine."""
        size = self.cfg.beats_per_call or len(subset) or 1
        chunks = [subset[i:i + size] for i in range(0, len(subset), size)]
        calls = [self._json(db, case_id, f"Spoken Addition Inspector ({language})", "spoken_addition_inspector",
                            prompt("documentary/spoken/addition_inspector"),
                            {"beats": [{"beat_id": s["id"], "source": src[s["id"]], "spoken": s["text"]} for s in chunk]})
                 for chunk in chunks]
        for data, _ in await gather_limited(None, calls):
            for item in data.get("beats") or []:
                entry = checks.get(str(item.get("beat_id"))) if isinstance(item, dict) else None
                added = [str(a) for a in (item.get("added") or []) if str(a).strip()] if entry is not None else []
                if added:
                    entry["meaning"]["added"] = added
                    entry["meaning_ok"] = False

    def _backed(self, language: str, src: dict[str, str], subset: list[dict]) -> dict:
        """The check without a model: a beat is backed while the narration still holds the names (people, places, organisations) of its
        source, up to `max_missing_name_share`. The beat is not rated for style (verdict "unrated"); its length, digits and long sentences are
        measured as before."""
        result = {}
        for s in subset:
            missing, names = missing_anchors(src[s["id"]], s["text"], language)
            result[s["id"]] = {
                "meaning": {"missing": missing, "added": [], "changed": [], "certainty": []},
                "meaning_ok": not names or len(missing) / names <= self.cfg.max_missing_name_share,
                "verdict": "unrated", "problems": [],
                "ear": read_aloud_metrics(s["text"], language),
                "length": self._length(language, src[s["id"]], s["text"]),
            }
        return result

    def _length(self, language: str, source_text: str, spoken_text: str) -> dict:
        """A beat told much longer than its script: how long it is against what the whole film may
        grow to (every beat within the ratio keeps the film within it)."""
        source_words = len(source_text.split())
        spoken_words = len(spoken_text.split())
        ratio = _words_minutes(spoken_text, language) / max(_words_minutes(source_text, language), 1e-6)
        return {"source_words": source_words, "spoken_words": spoken_words,
                "max_words": int(spoken_words / ratio * self.cfg.max_duration_ratio)
                if ratio else spoken_words,
                "too_long": ratio > self.cfg.max_duration_ratio}

    def _repair_targets(self, spoken: list[dict], checks: dict, failures: list[str],
                        repaired: dict[str, int]) -> list[dict]:
        """The beats worth another rewrite: a fact problem, a newsreader beat or a digit left in a beat (each fails the version
        by itself), and, only while the gate they belong to fails, a beat too long, a "mixed" beat with problems, or a beat with
        long sentences. A beat already rewritten `max_repairs_per_beat` times is not sent back again."""
        targets = []
        for s in spoken:
            if repaired.get(s["id"], 0) >= self.cfg.max_repairs_per_beat:
                continue
            c = checks[s["id"]]
            ear = c.get("ear") or {}
            if (not c["meaning_ok"] or c["verdict"] == "newsreader" or ear.get("digits")
                    or ("duration_out_of_range" in failures and (c.get("length") or {}).get("too_long"))
                    or ("not_enough_storytelling" in failures and c["verdict"] == "mixed" and c["problems"])
                    or ("sentences_too_long" in failures and ear.get("long_sentences"))):
                targets.append(s)
        return targets

    async def _repair(self, db, case_id, language, source, spoken, checks,
                      uncertain, targets: list[dict]) -> list[dict]:
        size = self.cfg.beats_per_call or len(targets)
        # chunks repair different beats: all at once
        results = await gather_limited(None, [
            self._repair_chunk(db, case_id, language, source, spoken, checks, uncertain,
                               targets[i:i + size])
            for i in range(0, len(targets), size)])
        new: dict[str, str] = {}
        for fixed in results:
            new.update(fixed)
        return [{"id": s["id"], "text": new.get(s["id"], s["text"])} for s in spoken]

    async def _repair_chunk(self, db, case_id, language, source, spoken, checks,
                            uncertain, targets) -> dict[str, str]:
        src = {s["id"]: s["text"] for s in source}
        system = writer_system_prompt(language) + prompt("documentary/spoken/repair_chunk")
        payload = {
            "language": language,
            "if_mentioned_keep_uncertain": uncertain,
            "beats": [
                {"beat_id": s["id"], "source": src[s["id"]], "current": s["text"],
                 "fact_problems": {k: v for k, v in checks[s["id"]]["meaning"].items()
                                   if k in ("missing", "added", "changed", "certainty")},
                 "style_problems": checks[s["id"]]["problems"],
                 "too_long_for_the_ear": (checks[s["id"]].get("ear") or {}).get(
                     "long_sentences", []),
                 "stiff_phrases": (checks[s["id"]].get("ear") or {}).get(
                     "stiff_phrases", []),
                 "numbers_left_as_digits": (checks[s["id"]].get("ear") or {}).get(
                     "digits", []),
                 "too_long_for_the_length": (
                     checks[s["id"]]["length"] if checks[s["id"]]["length"]["too_long"] else None)}
                for s in targets
            ],
            "output_template": _mark_sections(
                [{"id": s["id"], "text": "…"} for s in targets]),
        }
        res = await self._text(db, case_id, f"Spoken Repair ({language})",
                               "spoken_writer", system, payload,
                               _task_line(language, len(targets), repair=True))
        text, _ = clean_writer_output(res.text)
        fixed, ok = realign_sections(
            [{"id": s["id"], "text": s["text"]} for s in targets], text,
            allow_paragraph_fallback=False)
        if not ok:
            return {}  # keep current text; checks stay honest
        return {s["id"]: s["text"] for s in fixed}

    def _gates(self, language: str, source: list[dict], spoken: list[dict], checks: dict):
        """The gates of the whole narration: the names of the ones that fail, with the numbers they were judged on."""
        text = _join_sections(spoken)
        failures = []
        changed = sum(1 for c in checks.values() if not c["meaning_ok"])
        if changed / max(len(checks), 1) > self.cfg.max_meaning_changed_share:
            failures.append("meaning_changed")
        n = max(len(checks), 1)
        storyteller = sum(1 for c in checks.values() if c["verdict"] == "storyteller")
        if self.cfg.llm_checks:             # the verdicts come from the style critic
            if any(c["verdict"] == "newsreader" for c in checks.values()):
                failures.append("newsreader_tone")
            if storyteller / n < self.cfg.min_storyteller_share:
                failures.append("not_enough_storytelling")
        ear = read_aloud_metrics(text, language)
        if ear["long_sentence_share"] > self.cfg.max_long_sentence_share:
            failures.append("sentences_too_long")
        lq = language_quality(text, language, ai_config.language_quality_for(language))
        if not lq["pass"]:
            failures.append("language_quality")
        if ear["digits"]:
            failures.append("digits_left")
        if re.search(r"https?://|www\.", text) or has_meta_text(text):
            failures.append("output_purity")
        ratio = _words_minutes(text, language) / max(
            _words_minutes(_join_sections(source), language), 1e-6)
        if not (self.cfg.min_duration_ratio <= ratio <= self.cfg.max_duration_ratio):
            failures.append("duration_out_of_range")
        return failures, text, ear, lq, ratio, storyteller, n

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    async def create(self, db: Session, case: Case, master: StoryVersion,
                     language: str, beat_ids: set[str] | None = None,
                     pilot_seconds: float | None = None) -> StoryVersion:
        if language not in self.cfg.languages:
            raise ValueError(f"Language {language!r} is not configured for spoken narration.")
        row = latest_blueprint(db, master.id)
        if not row or row.status == "invalid" or (
            row.story_text_hash and row.story_text_hash != master.text_hash
        ):
            raise RuntimeError(
                "This story needs a valid editorial blueprint for its current "
                "text first (POST …/blueprint)."
            )
        blueprint = json.loads(row.blueprint_json or "{}")
        source = beat_sections(version_sections(master), blueprint)
        if beat_ids:
            source = [section for section in source if section["id"] in beat_ids]
        if not source:
            raise RuntimeError("The selected pilot has no story beats to narrate.")
        pack = build_evidence_pack(
            db.query(Fact).filter(Fact.case_id == case.id).all(),
            db.query(Contradiction).filter(Contradiction.case_id == case.id).all(),
            db.query(Source).filter(Source.case_id == case.id).all(),
        )
        if row.evidence_fingerprint and row.evidence_fingerprint != evidence_fingerprint(pack):
            raise RuntimeError(
                "Research changed after the blueprint was made; regenerate the "
                "story and its blueprint first."
            )
        uncertain = [f["claim"] for f in pack["disputed_facts"]]

        spoken, writer_res, write_log = await self._write(
            db, case.id, language, source, uncertain)
        checks, overall, notes = await self._check(db, case.id, language, source, spoken)
        repairs = 0
        repaired: dict[str, int] = {}
        while repairs < self.cfg.max_repair_iterations:
            failures, *_ = self._gates(language, source, spoken, checks)
            targets = self._repair_targets(spoken, checks, failures, repaired)
            if not targets:
                break                       # the version passes, or what is left is not worth another rewrite
            repairs += 1
            # the style critic judges again only the beats that were sent back for how they sound; the meaning check
            # (the fact guard) judges every beat the writer touched
            by_style = {s["id"] for s in targets if checks[s["id"]]["verdict"] != "storyteller" or any(
                (checks[s["id"]].get("ear") or {}).get(k) for k in ("digits", "long_sentences"))}
            before = {s["id"]: s["text"] for s in spoken}
            spoken = await self._repair(db, case.id, language, source, spoken,
                                        checks, uncertain, targets)
            for s in targets:
                repaired[s["id"]] = repaired.get(s["id"], 0) + 1
            changed = {s["id"] for s in spoken if s["text"] != before[s["id"]]}
            if not changed:
                continue            # the writer answered with the same words: the next round asks again
            rechecked, new_overall, new_notes = await self._check(
                db, case.id, language, source, spoken, changed, style_ids=changed & by_style,
                previous=checks)
            checks.update(rechecked)
            if new_overall:
                overall, notes = new_overall, new_notes

        failures, text, ear, lq, ratio, storyteller, n = self._gates(language, source, spoken, checks)
        meaning_ok = sum(1 for c in checks.values() if c["meaning_ok"])
        critique = {
            "quality_gates": {"pass": not failures, "failures": failures},
            "spoken": {
                "language": language, "overall": overall, "notes": notes,
                "storyteller_beats": storyteller, "beats": n,
                "meaning_ok_beats": meaning_ok, "duration_ratio": round(ratio, 3),
                "repair_iterations": repairs, "write_log": write_log,
                "language_quality": lq,
                "ear": {k: v for k, v in ear.items() if k != "long_sentences"}
                | {"long_sentences": ear["long_sentences"][:20]},
                "per_beat": checks,
            },
        }
        try:
            plan = structure_without_text(json.loads(master.narrative_structure or "{}"))
        except (ValueError, TypeError):
            plan = {}
        version = save_story_version(
            db, case, plan, spoken, text, 0.0, None, "not_evaluated", critique,
            language, writer_res, kind="spoken",
            writer_prompt="documentary/spoken/writer_system_prompt",
            master_version_id=master.id,
            native_quality_score=round(100 * storyteller / n, 1) if self.cfg.llm_checks else None,
            semantic_consistency_score=round(100 * meaning_ok / n, 1) if self.cfg.llm_checks else None,
            factual_consistency_score=round(meaning_ok / n, 3),
            extra_structure={
                "blueprint_id": row.id, "source_version_id": master.id,
                "beat_sections": True,
                "pilot_seconds": pilot_seconds,
                "pilot_beat_ids": sorted(beat_ids) if beat_ids else None,
                "evidence_fingerprint": row.evidence_fingerprint,
            },
        )
        return version
