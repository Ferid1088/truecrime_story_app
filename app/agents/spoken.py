"""The SpokenNarrator agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.spoken."""

from __future__ import annotations

from app.agents.runner import run_agent
from app.core.prompts import prompt
import json
import re
from sqlalchemy.orm import Session
from app.agents.story import StoryPipeline, _join_sections, _mark_sections, build_evidence_pack, evidence_fingerprint, realign_sections, strip_section_markers, structure_without_text
from app.core.ai_config import ai_config
from app.db.models import Case, Contradiction, Fact, Source, StoryVersion
from app.core.concurrency import gather_limited
from app.documentary.blueprint import latest_blueprint, version_sections
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.utils import language_quality
from app.documentary.spoken import (
    MEANING_SYSTEM,
    _meaning_ok,
    _task_line,
    _words_minutes,
    beat_sections,
    clean_writer_output,
    critic_system_prompt,
    has_meta_text,
    read_aloud_metrics,
    writer_system_prompt,
)


class SpokenNarrator:
    def __init__(self):
        self.gen = get_generation_provider()
        self.cfg = ai_config.spoken

    # ------------------------------------------------------------------
    # model calls
    # ------------------------------------------------------------------

    async def _text(self, db, case_id, label, role, system, payload: dict,
                    task: str = ""):
        user = json.dumps(payload, ensure_ascii=False)
        if task:
            user = f"{task}\n\nINPUT:\n{user}"
        with track_run(db, case_id, label, input_summary=label) as run:
            res = await run_agent(role, self.gen, user, system=system)
            stamp_run(run, res, role)
            run.output_summary = f"words={len(res.text.split())}"
        return res

    async def _json(self, db, case_id, label, role, system, payload: dict):
        with track_run(db, case_id, label, input_summary=label) as run:
            data, res = await run_agent(role, self.gen, json.dumps(payload, ensure_ascii=False), system=system)
            stamp_run(run, res, role)
        return data if isinstance(data, dict) else {}, res

    async def _write(self, db, case_id, language, source: list[dict],
                     uncertain: list[str]) -> tuple[list[dict], object, list[str]]:
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

        size = self.cfg.beats_per_call
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
                     spoken: list[dict], beat_ids: set[str] | None = None):
        """Meaning check + native style critic per chunk of beats (all
        chunks and both critics in parallel)."""
        src = {s["id"]: s["text"] for s in source}
        subset = [s for s in spoken if beat_ids is None or s["id"] in beat_ids]
        size = self.cfg.beats_per_call or len(subset) or 1
        chunks = [subset[i:i + size] for i in range(0, len(subset), size)]

        def meaning_payload(chunk):
            return {"source_language": "en", "spoken_language": language,
                    "beats": [{"beat_id": s["id"], "source": src[s["id"]],
                               "spoken": s["text"]} for s in chunk]}

        def style_payload(chunk):
            return {"language": language, "narration": _mark_sections(chunk)}

        calls = []
        for chunk in chunks:
            calls.append(self._json(db, case_id, f"Spoken Meaning Check ({language})",
                                    "spoken_meaning_checker", MEANING_SYSTEM,
                                    meaning_payload(chunk)))
            calls.append(self._json(db, case_id, f"Spoken Style Critic ({language})",
                                    "spoken_style_critic", critic_system_prompt(language),
                                    style_payload(chunk)))
        results = await gather_limited(None, calls)
        by_id_m: dict[str, dict] = {}
        by_id_s: dict[str, dict] = {}
        overall, notes = [], []
        for k in range(0, len(results), 2):
            meaning, _ = results[k]
            style, _ = results[k + 1]
            for b in meaning.get("beats") or []:
                if isinstance(b, dict):
                    by_id_m[str(b.get("beat_id"))] = b
            for b in style.get("beats") or []:
                if isinstance(b, dict):
                    by_id_s[str(b.get("beat_id"))] = b
            if style.get("overall"):
                overall.append(style["overall"])
            if style.get("notes"):
                notes.append(str(style["notes"]))
        result = {}
        for s in subset:
            m = by_id_m.get(s["id"])
            st = by_id_s.get(s["id"]) or {}
            verdict = st.get("verdict") if st.get("verdict") in (
                "storyteller", "mixed", "newsreader") else "unrated"
            result[s["id"]] = {
                # A beat the checker did not return is NOT assumed fine.
                "meaning": m if m is not None else {"unchecked": True},
                "meaning_ok": m is not None and _meaning_ok(m),
                "verdict": verdict,
                "problems": [p for p in st.get("problems") or [] if isinstance(p, dict)][:4],
                "ear": read_aloud_metrics(s["text"], language),
            }
        worst = next((v for v in ("newsreader", "mixed", "storyteller") if v in overall), None)
        return result, worst, " ".join(notes)[:800]

    @staticmethod
    def _needs_repair(entry: dict) -> bool:
        ear = entry.get("ear") or {}
        return (not entry["meaning_ok"]) or (
            entry["verdict"] in ("mixed", "newsreader") and entry["problems"]
        ) or bool(ear.get("long_sentences") or ear.get("stiff_phrases"))

    async def _repair(self, db, case_id, language, source, spoken, checks,
                      uncertain) -> list[dict]:
        targets = [s for s in spoken if self._needs_repair(checks[s["id"]])]
        if not targets:
            return spoken
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
                     "stiff_phrases", [])}
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

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    async def create(self, db: Session, case: Case, master: StoryVersion,
                     language: str) -> StoryVersion:
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
        while (repairs < self.cfg.max_repair_iterations
               and any(self._needs_repair(c) for c in checks.values())):
            repairs += 1
            before = {s["id"]: s["text"] for s in spoken}
            spoken = await self._repair(db, case.id, language, source, spoken,
                                        checks, uncertain)
            changed = {s["id"] for s in spoken if s["text"] != before[s["id"]]}
            if not changed:
                break
            rechecked, overall, notes = await self._check(
                db, case.id, language, source, spoken, changed)
            checks.update(rechecked)

        text = _join_sections(spoken)
        failures = []
        if any(not c["meaning_ok"] for c in checks.values()):
            failures.append("meaning_changed")
        if any(c["verdict"] == "newsreader" for c in checks.values()):
            failures.append("newsreader_tone")
        n = max(len(checks), 1)
        storyteller = sum(1 for c in checks.values() if c["verdict"] == "storyteller")
        if storyteller / n < self.cfg.min_storyteller_share:
            failures.append("not_enough_storytelling")
        ear = read_aloud_metrics(text, language)
        if ear["long_sentence_share"] > self.cfg.max_long_sentence_share:
            failures.append("sentences_too_long")
        lq = language_quality(text, language, ai_config.language_quality_for(language))
        if not lq["pass"]:
            failures.append("language_quality")
        if re.search(r"https?://|www\.", text) or has_meta_text(text):
            failures.append("output_purity")
        ratio = _words_minutes(text, language) / max(
            _words_minutes(_join_sections(source), "en"), 1e-6)
        if not (self.cfg.min_duration_ratio <= ratio <= self.cfg.max_duration_ratio):
            failures.append("duration_out_of_range")

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
        version = StoryPipeline()._new_version(
            db, case, plan, spoken, text, 0.0, None, "not_evaluated", critique,
            language, writer_res, kind="spoken",
            master_version_id=master.id,
            derived_from_master_version=master.version,
            native_quality_score=round(100 * storyteller / n, 1),
            semantic_consistency_score=round(100 * meaning_ok / n, 1),
            factual_consistency_score=round(meaning_ok / n, 3),
            extra_structure={
                "blueprint_id": row.id, "source_version_id": master.id,
                "beat_sections": True,
                "evidence_fingerprint": row.evidence_fingerprint,
            },
        )
        return version
