"""The VoicePerformanceDirector agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.voice_performance."""

from __future__ import annotations

from app.agents.runner import run_agent
import json
from sqlalchemy.orm import Session
from app.core.ai_config import ai_config
from app.core.concurrency import gather_limited
from app.db.models import Case, StoryVersion, VoicePerformance
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.documentary.voice_performance import (
    ARC_SYSTEM,
    LEVEL_NAMES,
    TAG,
    _beat_sources,
    _tag_counts,
    director_system,
    enforce_levels,
    latest_performance,
    performance_records,
    speech_structure,
    thin_tags,
    validate_arc,
    validate_line,
)


class VoicePerformanceDirector:
    def __init__(self):
        self.gen = get_generation_provider()
        self.cfg = ai_config.voice_performance

    async def _arc(self, db, case_id, beats: list[dict], structure: dict[str, dict],
                   language: str) -> tuple[dict, object | None]:
        payload = {"language": language, "beats": []}
        for b in beats:
            recs = [r for p in structure.get(b["id"], {}).get("paragraphs", []) for r in p]
            opening = " ".join((r.get("display") or r["speech"]) for r in recs[:2])
            payload["beats"].append({
                "beat_id": b["id"], "purpose": b.get("purpose"),
                "summary": b.get("summary"), "emotional_load": b.get("emotional_load"),
                "mystery_intensity": b.get("mystery_intensity"),
                "audio_intent": b.get("audio_intent"), "sentences": len(recs),
                "opening": opening[:300],
            })
        with track_run(db, case_id, f"Voice Arc ({language})",
                       input_summary=f"{len(beats)} beats") as run:
            data, res = await run_agent("documentary.voice_arc", self.gen, json.dumps(payload, ensure_ascii=False), system=ARC_SYSTEM)
            stamp_run(run, res, "voice_performance_director")
        return (data if isinstance(data, dict) else {}), res

    async def _direct_chunk(self, db, case_id, language, chunk: list[dict],
                            beats_info: dict[str, dict]) -> dict[int, dict]:
        payload = {"language": language, "beats": []}
        for r in chunk:
            if not payload["beats"] or payload["beats"][-1]["beat_id"] != r["beat_id"]:
                info = beats_info[r["beat_id"]]
                payload["beats"].append({
                    "beat_id": r["beat_id"], "purpose": info.get("purpose"),
                    "summary": info.get("summary"), "arc_level": info["level"],
                    "arc_peak": info["peak"], "sentences": []})
            payload["beats"][-1]["sentences"].append({"i": r["i"], "text": r["speech"]})
        with track_run(db, case_id, f"Voice Performance ({language})",
                       input_summary=f"{len(chunk)} sentences") as run:
            data, res = await run_agent("documentary.voice_directing", self.gen, "TASK: direct every sentence below; the words never change. "
                "Return JSON only.\n\nINPUT:\n" + json.dumps(payload, ensure_ascii=False), system=director_system(language, self.cfg))
            stamp_run(run, res, "voice_performance_director")
        out: dict[int, dict] = {}
        if isinstance(data, dict):
            for s in data.get("sentences") or []:
                if isinstance(s, dict) and isinstance(s.get("i"), int):
                    out[s["i"]] = s
        return out

    async def create(self, db: Session, case: Case, version: StoryVersion,
                     blueprint: dict, beat_ids: list[str] | None = None
                     ) -> VoicePerformance:
        """Direct the whole version, or only `beat_ids` (a pilot). Beats
        directed before for the same text are reused."""
        language = version.language or "en"
        structure = {s["beat_id"]: s for s in speech_structure(version)}
        bp_beats = [b for b in blueprint.get("beats") or [] if b["id"] in structure]
        if not bp_beats:  # no beat mapping: one pseudo-beat per section
            bp_beats = [{"id": bid, "purpose": "timeline"} for bid in structure]
        prev = latest_performance(db, version.id)
        prev_data = json.loads(prev.performance_json or "{}") if prev else {}
        prev_recs = performance_records(prev)
        model = None

        # 1. arc (whole film, so a pilot already sits in the right place)
        if prev_data.get("arc_raw"):
            arc_raw = prev_data["arc_raw"]
        else:
            try:
                arc_raw, res = await self._arc(db, case.id, bp_beats, structure, language)
                model = getattr(res, "model", None)
            except Exception as e:  # deterministic arc from the blueprint
                arc_raw = {"error": f"{type(e).__name__}: {e}"[:300]}
        arc, incident, arc_log = validate_arc(bp_beats, arc_raw, self.cfg)
        info = {b["id"]: {**b, **arc[b["id"]]} for b in bp_beats}

        # 2. sentences (records numbered over the whole film)
        wanted = set(beat_ids) if beat_ids else set(structure)
        records: list[dict] = []
        for b in bp_beats:
            for pi, para in enumerate(structure[b["id"]]["paragraphs"]):
                for k, rec in enumerate(para):
                    old = None
                    old_paras = prev_recs.get(b["id"])
                    if old_paras and pi < len(old_paras) and k < len(old_paras[pi]):
                        cand = old_paras[pi][k]
                        if cand.get("speech") == rec["speech"] and cand.get("directed"):
                            old = cand
                    records.append({
                        "i": len(records), "beat_id": b["id"], "p": pi, "k": k,
                        "speech": rec["speech"], "display": rec.get("display"),
                        "level": (old or {}).get("level", arc[b["id"]]["level"]),
                        "tts": (old or {}).get("tts"), "directed": bool(old),
                        "raw": (old or {}).get("raw"),
                        "risky": (old or {}).get("risky"),
                    })
        todo = [r for r in records if r["beat_id"] in wanted and not r["directed"]]
        pcfg = ai_config.pronunciation
        needs_key = pcfg.enabled and language in pcfg.languages and any(
            r["directed"] and r.get("risky") is None for r in records)
        if prev is not None and not todo and not needs_key and prev.status != "failed":
            return prev  # everything asked for is directed already
        size = self.cfg.sentences_per_call
        chunks: list[list[dict]] = []
        cur: list[dict] = []
        for r in todo:
            if len(cur) >= size and cur[-1]["beat_id"] != r["beat_id"]:
                chunks.append(cur)
                cur = []
            cur.append(r)
            if len(cur) >= size * 1.5:
                chunks.append(cur)
                cur = []
        if cur:
            chunks.append(cur)
        results = await gather_limited(
            None, [self._direct_chunk(db, case.id, language, c, info) for c in chunks],
            return_exceptions=True)
        errors = []
        for chunk, res in zip(chunks, results):
            if isinstance(res, Exception):
                errors.append(f"{type(res).__name__}: {str(res)[:200]}")
                continue
            for r in chunk:
                got = res.get(r["i"])
                if got:
                    r["level"] = got.get("level", r["level"])
                    r["raw"] = got.get("tts")
                    r["directed"] = True

        # 3. validation
        level_log = enforce_levels(records, arc, self.cfg)
        issues: dict[str, int] = {}
        examples: list[dict] = []
        for r in records:
            if not r["directed"]:
                r["tts"] = None
                continue
            tts, probs = validate_line(r["speech"], r.get("raw"), r["level"], language, self.cfg)
            r["tts"] = tts
            for pr in probs:
                key = pr.split(":")[0]
                issues[key] = issues.get(key, 0) + 1
                if len(examples) < 25:
                    examples.append({"i": r["i"], "issue": pr, "raw": r.get("raw")})
        thinned = thin_tags([r for r in records if r["directed"]], self.cfg)

        # 4. pronunciation key: the words a voice could misread, with the
        #    reading the meaning needs (used by the listening loop)
        pron_report = None
        if pcfg.enabled and language in pcfg.languages:
            from app.documentary.pronunciation import PronunciationEditor

            need = [r for r in records if r["directed"] and r.get("risky") is None]
            if need:
                sources = _beat_sources(db, version, blueprint)
                keyed, pron_report = await PronunciationEditor().annotate(
                    db, case.id, language,
                    [{"i": r["i"], "beat_id": r["beat_id"], "text": r["speech"],
                      "source": sources.get(r["beat_id"], "")} for r in need])
                for r in need:
                    if r["i"] in keyed:
                        r["risky"] = keyed[r["i"]]

        beats_out = []
        for b in bp_beats:
            paras: list[list[dict]] = []
            for r in (x for x in records if x["beat_id"] == b["id"]):
                while len(paras) <= r["p"]:
                    paras.append([])
                paras[r["p"]].append({k: r[k] for k in (
                    "speech", "display", "tts", "level", "directed", "raw", "risky")})
            beats_out.append({"beat_id": b["id"], "arc_level": arc[b["id"]]["level"],
                              "arc_peak": arc[b["id"]]["peak"], "paragraphs": paras})
        directed = [r for r in records if r["directed"]]
        incident_i = next((r["i"] for r in records if r["beat_id"] == incident), None)
        stats = {
            "sentences": len(records), "directed": len(directed),
            "levels": {LEVEL_NAMES[lv]: sum(1 for r in directed if r["level"] == lv)
                       for lv in range(4)},
            "tagged": sum(1 for r in directed if TAG.search(r["tts"] or "")),
            "tags": _tag_counts(directed),
            "issues": issues, "thinned": thinned,
        }
        data = {"language": language, "arc_raw": arc_raw,
                "incident_beat": incident, "incident_sentence": incident_i,
                "beats": beats_out, "stats": stats}
        validation = {"arc_log": arc_log, "level_log": level_log, "errors": errors,
                      "examples": examples, "pronunciation_key": pron_report}
        row = VoicePerformance(
            case_id=case.id, story_version_id=version.id, language=language,
            version=(prev.version + 1) if prev else 1,
            status="failed" if errors and not directed else (
                "partial" if errors or len(directed) < len(records) else "ready"),
            performance_json=json.dumps(data, ensure_ascii=False),
            validation_json=json.dumps(validation, ensure_ascii=False),
            generation_model=model or (prev.generation_model if prev else None),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
