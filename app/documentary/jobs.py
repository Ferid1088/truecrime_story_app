"""One-button documentary pipeline (DocumentaryJob).

Stages (each idempotent — a re-run resumes and reuses what exists):
  blueprint          editorial blueprint for the master story
  audio_plan         breaths, music beds/moments, silences
  spoken:<lang>      storyteller version per language
  film_length        every finished film must run 45–120 min
  visual_needs       visual requirements per beat
  visual_research    real photos/documents for those needs
  visual_check       vision verification of the best candidates
  visual_plan        shots per beat + motion; maps/document cards
  voice:<lang>       narration + music mix (pilot: first N seconds)
  production:<lang>  language-specific timeline
  critique:<lang>    critics + targeted fixes
  render:<lang>      MP4 (+ subtitles)

mode "pilot" renders the opening `pilot_seconds` of every language;
mode "full" renders the whole film and refuses stories that would run
shorter than documentary.min_film_minutes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback

from sqlalchemy.orm import Session

from app.agents.story import stored_sections
from app.core.ai_config import ai_config
from app.db.models import (
    Case, DocumentaryJob, Fact, ProductionScript, StoryVersion, VisualAsset, VisualPlan,
)
from app.documentary import storage
from app.documentary.audio_director import AudioDirector, latest_audio_plan
from app.documentary.blueprint import NarrativeDirector, latest_blueprint
from app.documentary.performance import performance_for_version
from app.utils import utc_now

log = logging.getLogger(__name__)
_running: dict[int, asyncio.Task] = {}


class JobCancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# helpers shared with the API
# ---------------------------------------------------------------------------


def latest_spoken(db: Session, master: StoryVersion, language: str,
                  blueprint_id: int | None = None) -> StoryVersion | None:
    rows = (db.query(StoryVersion)
            .filter(StoryVersion.case_id == master.case_id, StoryVersion.kind == "spoken",
                    StoryVersion.language == language,
                    StoryVersion.master_version_id == master.id)
            .order_by(StoryVersion.id.desc()).all())
    for v in rows:
        struct = json.loads(v.narrative_structure or "{}")
        if blueprint_id is None or struct.get("blueprint_id") == blueprint_id:
            return v
    return None


def estimate_film_minutes(version: StoryVersion, audio_plan: dict | None = None) -> float:
    """Speech at the measured narration speed of the language plus the
    planned breaths and music moments."""
    words = len((version.story_text or "").split())
    speech = words / ai_config.documentary.wpm(version.language or "en")
    pauses = 0.0
    if audio_plan:
        pauses = sum(float((pb.get("after") or {}).get("seconds") or 0)
                     for pb in audio_plan.get("beats") or []) / 60
        paras = sum(len([p for p in s["text"].split("\n\n") if p.strip()])
                    for s in stored_sections(version))
        pauses += paras * ai_config.audio_direction.paragraph_breath_ms["normal"] / 60000
    return round(speech + pauses, 1)


def pilot_beats(blueprint: dict, seconds: float) -> list[str]:
    """Beats that fall into the first `seconds` (plus one beat margin)."""
    out, t = [], 0.0
    for b in blueprint.get("beats") or []:
        out.append(b["id"])
        t += (b.get("words") or 0) / ai_config.documentary.wpm("en") * 60
        if t > seconds:
            break
    beats = blueprint.get("beats") or []
    if len(out) < len(beats):
        out.append(beats[len(out)]["id"])
    return out


def latest_visual_plan(db: Session, blueprint_id: int) -> VisualPlan | None:
    return (db.query(VisualPlan).filter(VisualPlan.blueprint_id == blueprint_id,
                                        VisualPlan.status != "invalid")
            .order_by(VisualPlan.version.desc()).first())


def latest_production(db: Session, version_id: int) -> ProductionScript | None:
    return (db.query(ProductionScript).filter(ProductionScript.story_version_id == version_id)
            .order_by(ProductionScript.version.desc()).first())


def job_dict(job: DocumentaryJob) -> dict:
    return {
        "id": job.id, "case_id": job.case_id, "master_version_id": job.master_version_id,
        "mode": job.mode, "languages": json.loads(job.languages_json or "[]"),
        "pilot_seconds": job.pilot_seconds, "render_profile": job.render_profile,
        "refresh_visuals": bool(job.refresh_visuals),
        "status": job.status, "stage": job.stage, "progress": round(job.progress or 0, 3),
        "stages": json.loads(job.stages_json or "[]"),
        "result": json.loads(job.result_json or "{}"), "error": job.error,
        "created_at": job.created_at, "updated_at": job.updated_at,
        "completed_at": job.completed_at,
    }


def plan_stages(languages: list[str]) -> list[dict]:
    names = ["blueprint", "audio_plan"] + [f"spoken:{l}" for l in languages] + [
        "film_length", "visual_needs", "visual_research", "visual_check", "visual_plan"]
    for l in languages:
        names += [f"voice:{l}", f"production:{l}", f"critique:{l}", f"render:{l}"]
    return [{"name": n, "status": "pending", "detail": None} for n in names]


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------


class DocumentaryPipeline:
    def __init__(self, db: Session, job: DocumentaryJob):
        self.db = db
        self.job = job
        self.stages = json.loads(job.stages_json or "[]")
        self.result = json.loads(job.result_json or "{}")

    # -- bookkeeping ---------------------------------------------------------
    def _save(self):
        self.job.stages_json = json.dumps(self.stages, ensure_ascii=False, default=str)
        self.job.result_json = json.dumps(self.result, ensure_ascii=False, default=str)
        done = sum(1 for s in self.stages if s["status"] in ("done", "skipped"))
        self.job.progress = done / max(len(self.stages), 1)
        self.job.updated_at = utc_now()
        self.db.commit()

    def _check_cancel(self):
        self.db.refresh(self.job)
        if self.job.status == "cancelling":
            raise JobCancelled()

    async def _stage(self, name: str, fn):
        st = next(s for s in self.stages if s["name"] == name)
        if st["status"] in ("done", "skipped"):
            return st.get("detail")
        self._check_cancel()
        st["status"] = "running"
        self.job.stage = name
        self._save()
        try:
            detail = await fn()
        except Exception as e:
            st["status"] = "failed"
            st["detail"] = str(e)[:500]
            self._save()
            raise
        st["status"] = "skipped" if isinstance(detail, dict) and detail.get("skipped") else "done"
        st["detail"] = detail
        self._save()
        return detail

    # -- run -----------------------------------------------------------------
    async def run(self):
        db, job = self.db, self.job
        master = db.get(StoryVersion, job.master_version_id)
        case = db.get(Case, job.case_id)
        langs = json.loads(job.languages_json)
        pilot = job.mode == "pilot"
        pilot_seconds = job.pilot_seconds or ai_config.documentary.pilot_seconds
        state: dict = {}

        async def blueprint():
            row = latest_blueprint(db, master.id)
            if row and row.status != "invalid" and (
                    not row.story_text_hash or row.story_text_hash == master.text_hash):
                state["bp"] = row
                return {"blueprint_id": row.id, "reused": True}
            row = await NarrativeDirector().create(db, case, master)
            if row.status == "invalid":
                raise RuntimeError("The editorial blueprint is invalid; see the Blueprint tab.")
            state["bp"] = row
            return {"blueprint_id": row.id, "status": row.status}

        await self._stage("blueprint", blueprint)
        state.setdefault("bp", latest_blueprint(db, master.id))
        bp_row = state["bp"]
        bp = json.loads(bp_row.blueprint_json or "{}")

        async def audio_plan():
            row = latest_audio_plan(db, bp_row.id)
            if row:
                return {"audio_plan_id": row.id, "reused": True}
            row = await AudioDirector().create(db, case, bp_row)
            return {"audio_plan_id": row.id, "status": row.status}

        await self._stage("audio_plan", audio_plan)
        ap_row = latest_audio_plan(db, bp_row.id)
        ap = json.loads(ap_row.plan_json) if ap_row else None

        spoken: dict[str, StoryVersion] = {}
        for lang in langs:
            async def make_spoken(lang=lang):
                from app.documentary.spoken import SpokenNarrator

                v = latest_spoken(db, master, lang, bp_row.id)
                if v is None:
                    v = await SpokenNarrator().create(db, case, master, lang)
                gates = json.loads(v.critic_notes or "{}").get("quality_gates") or {}
                return {"version_id": v.id, "status": v.status,
                        "failures": gates.get("failures", [])}

            d = await self._stage(f"spoken:{lang}", make_spoken)
            spoken[lang] = db.get(StoryVersion, d["version_id"]) if d else latest_spoken(
                db, master, lang, bp_row.id)

        async def film_length():
            est = {l: estimate_film_minutes(v, ap) for l, v in spoken.items()}
            short = {l: m for l, m in est.items() if m < ai_config.documentary.min_film_minutes}
            long_ = {l: m for l, m in est.items() if m > ai_config.documentary.max_film_minutes}
            self.result["film_minutes"] = est
            if not pilot and (short or long_):
                raise RuntimeError(
                    f"Film length outside {ai_config.documentary.min_film_minutes:g}–"
                    f"{ai_config.documentary.max_film_minutes:g} min: "
                    + ", ".join(f"{l} {m} min" for l, m in {**short, **long_}.items())
                    + ". Generate a longer (or shorter) master story first.")
            return {"estimated_minutes": est,
                    "warning": (f"Story too short for a {ai_config.documentary.min_film_minutes:g}-"
                                "minute film (pilot only)") if short else None}

        await self._stage("film_length", film_length)

        focus_beats = pilot_beats(bp, pilot_seconds) if pilot else None

        async def visual_needs():
            from app.documentary.visuals.planner import VisualPlanner

            row = latest_visual_plan(db, bp_row.id)
            if row is None:
                row = await VisualPlanner().create(db, case, bp_row)
            state["vp"] = row
            return {"visual_plan_id": row.id, "status": row.status}

        await self._stage("visual_needs", visual_needs)
        vp_row = state.get("vp") or latest_visual_plan(db, bp_row.id)
        if vp_row is None:
            raise RuntimeError("No visual requirements could be planned.")
        requirements = json.loads(vp_row.requirements_json or "{}")

        planned = vp_row.status == "planned" and not job.refresh_visuals

        async def visual_research():
            if planned:
                return {"skipped": True, "reason": "visual plan exists (refresh_visuals to redo)"}
            from app.documentary.visuals.planner import research_queries
            from app.documentary.visuals.research import VisualResearchAgent

            queries = research_queries(requirements, focus_beats)
            stats = await VisualResearchAgent().run(db, case, queries)
            return {"queries": len(queries), **stats}

        await self._stage("visual_research", visual_research)

        async def visual_check():
            if planned:
                return {"skipped": True}
            return await verify_candidates(db, case, bp, requirements, focus_beats,
                                           job.render_profile)

        await self._stage("visual_check", visual_check)

        async def visual_plan():
            from app.documentary.visuals.director import VisualDirector
            from app.documentary.visuals.generated import materialize

            row = db.get(VisualPlan, vp_row.id)
            if row.status != "planned" or job.refresh_visuals:
                await VisualDirector().create(db, case, row, bp_row, ap_row,
                                              profile=job.render_profile)
            stats = await materialize(db, case, row)
            report = json.loads(row.validation_json or "{}").get("plan", {})
            return {"visual_plan_id": row.id, "shots": report.get("shots"),
                    "fallbacks": report.get("fallbacks"), **stats}

        await self._stage("visual_plan", visual_plan)
        vp_row = db.get(VisualPlan, vp_row.id)

        renders = self.result.setdefault("renders", {})
        for lang in langs:
            version = spoken[lang]
            manifest_holder: dict = {}

            async def voice(version=version):
                from app.documentary.production.audio import render_documentary_audio

                plan = performance_for_version(db, version)
                m = await render_documentary_audio(
                    plan, case_id=case.id, story_version_id=version.id,
                    max_seconds=pilot_seconds if pilot else None)
                manifest_holder["m"] = m
                return {"seconds": m.get("duration_seconds"),
                        "characters_paid": m.get("characters_paid"),
                        "music_paid": (m.get("mix") or {}).get("music_characters_paid"),
                        "flags": m.get("flags")}

            await self._stage(f"voice:{lang}", voice)
            manifest = manifest_holder.get("m") or _load_manifest(case.id, version)

            async def production(version=version, manifest=manifest):
                from app.documentary.production.script import build_production_script

                row = await build_production_script(db, version, vp_row, manifest,
                                                    mode=job.mode)
                return {"production_script_id": row.id, "duration": row.duration_seconds}

            d = await self._stage(f"production:{lang}", production)
            ps = db.get(ProductionScript, d["production_script_id"]) if d else latest_production(
                db, version.id)

            async def critique(ps=ps, manifest=manifest):
                from app.documentary.production.critics import DocumentaryCritics

                rep = await DocumentaryCritics().review(
                    db, ps, manifest["timeline"].get("words") or [])
                return {"score": rep.get("score"), "fixes": len(rep.get("fixes") or []),
                        "issues": len(rep["deterministic"]["issues"])}

            await self._stage(f"critique:{lang}", critique)

            async def render(ps=ps, lang=lang, version=version):
                from app.documentary.render.engine import VideoRenderer

                script = json.loads(ps.script_json)
                out = storage.renders_dir(case.id, lang) / f"{job.mode}_v{version.id}_{ps.version}.mp4"
                info = await asyncio.to_thread(VideoRenderer().render, script, out,
                                               pilot_seconds if pilot else None)
                ps.render_json = json.dumps(info)
                ps.status = "rendered"
                db.commit()
                renders[lang] = {"production_script_id": ps.id, **info}
                return info

            await self._stage(f"render:{lang}", render)
        return self.result


def _load_manifest(case_id: int, version: StoryVersion) -> dict:
    from app.documentary.voice_render import VoiceRenderer

    out = VoiceRenderer().out_dir(case_id, version.language or "en", version.id)
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


async def verify_candidates(db: Session, case: Case, blueprint: dict, requirements: dict,
                            beat_ids: list[str] | None, profile: str | None,
                            per_requirement: int = 3) -> dict:
    """Vision-check the best unverified candidates of each need (a pilot
    only checks the beats it shows)."""
    from app.agents.story import build_evidence_pack
    from app.db.models import Contradiction, Source
    from app.documentary.visuals.director import blocked_at, rank_candidates
    from app.documentary.visuals.verification import VisualVerificationAgent

    assets = db.query(VisualAsset).filter(VisualAsset.case_id == case.id).all()
    ents = {e["key"]: e for e in requirements.get("entities") or []}
    beats = {b["id"]: b for b in blueprint.get("beats") or []}
    todo: list[VisualAsset] = []
    for rb in requirements.get("beats") or []:
        if beat_ids and rb["beat_id"] not in beat_ids:
            continue
        blocked = blocked_at(blueprint, rb["beat_id"])
        for r in rb["requirements"]:
            ent = ents.get(r["entity"])
            if not ent:
                continue
            loose = dict(r, acceptable_roles=["evidence", "context", "illustration"])
            for _, a in rank_candidates(loose, ent, assets, blocked, profile)[:per_requirement]:
                if a.verification_status == "unverified" and a not in todo:
                    todo.append(a)
    pack = build_evidence_pack(
        db.query(Fact).filter(Fact.case_id == case.id).all(),
        db.query(Contradiction).filter(Contradiction.case_id == case.id).all(),
        db.query(Source).filter(Source.case_id == case.id).all())
    agent = VisualVerificationAgent()
    counts: dict[str, int] = {}
    for a in todo:
        await agent.verify(db, case, a, list(ents.values()), pack["facts"])
        counts[a.verification_status] = counts.get(a.verification_status, 0) + 1
    return {"checked": len(todo), **counts}


# ---------------------------------------------------------------------------
# launching
# ---------------------------------------------------------------------------


def create_job(db: Session, case: Case, master: StoryVersion, languages: list[str],
               mode: str, pilot_seconds: float | None, profile: str,
               refresh_visuals: bool = False) -> DocumentaryJob:
    job = DocumentaryJob(
        case_id=case.id, master_version_id=master.id, mode=mode,
        refresh_visuals=refresh_visuals,
        languages_json=json.dumps(languages), pilot_seconds=pilot_seconds,
        render_profile=profile, status="queued",
        stages_json=json.dumps(plan_stages(languages)),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


async def run_job(job_id: int, session_factory=None):
    from app.db.base import SessionLocal

    db = (session_factory or SessionLocal)()
    try:
        job = db.get(DocumentaryJob, job_id)
        job.status = "running"
        job.error = None
        db.commit()
        try:
            await DocumentaryPipeline(db, job).run()
            job.status = "completed"
        except JobCancelled:
            job.status = "cancelled"
        except Exception as e:  # recorded on the job, visible in the UI
            log.error("documentary job %s failed: %s", job_id, traceback.format_exc())
            db.rollback()
            job = db.get(DocumentaryJob, job_id)
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"[:1000]
        job.completed_at = utc_now()
        job.updated_at = utc_now()
        db.commit()
    finally:
        db.close()
        _running.pop(job_id, None)


def launch(job_id: int) -> None:
    _running[job_id] = asyncio.create_task(run_job(job_id))
