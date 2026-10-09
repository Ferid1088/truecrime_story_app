"""One-button documentary pipeline (DocumentaryJob).

Stages (each idempotent — a re-run resumes and reuses what exists):
  research           (from zero) research the case with the search engine
  master_story       (from zero) write the English master story
  blueprint          editorial blueprint for the master story
  audio_plan         breaths, music beds/moments, silences
  spoken:<lang>      storyteller version per language
  host_plan          when/why the on-screen host appears (persona)
  film_length        every finished film must run 45–120 min
  visual_needs       visual requirements per beat
  visual_research    real photos/documents for those needs
  visual_check       vision verification of the best candidates
  visual_plan        shots per sentence + motion; maps/document cards
  visual_gaps        targeted searches for sentences with weak pictures,
                     then those beats are directed again
  host:<lang>        the host's dialogue, natively per language
  performance:<lang> narrator arc + ElevenLabs v3 audio tags
  voice:<lang>       narration + music mix (pilot: first N seconds)
  production:<lang>  language-specific timeline
  critique:<lang>    critics + targeted fixes
  render:<lang>      MP4 (+ subtitles)

Parallel inside one documentary:
  * the spoken versions of all languages and the visual needs together;
  * then the visual chain (research -> check -> plan) next to every
    language's own chain (performance -> voice), and as soon as the
    visual plan exists each language continues on its own (production
    -> critique -> render);
  * shared limits (config: concurrency) pace model calls, ElevenLabs,
    speech-to-text and renders across everything that runs.
A language that fails does not stop the others (job status "partial").
The host is an addition: a failed host stage is recorded but never stops
the film.
Several documentaries run at once up to concurrency.jobs; more wait in
"queued".

mode "pilot" renders the opening `pilot_seconds` of every language;
mode "full" renders the whole film and refuses stories that would run
shorter than documentary.min_film_minutes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
import uuid

from sqlalchemy.orm import Session

from app.agents.story import stored_sections
from app.core.ai_config import ai_config
from app.core.concurrency import gather_limited, slot
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

RESEARCH_POLL_SECONDS = 15.0
RESEARCH_MAX_HOURS = 6.0


class JobCancelled(Exception):
    pass


class LanguageFailed(Exception):
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
        if blueprint_id is not None and struct.get("blueprint_id") != blueprint_id:
            continue
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
        "from_zero": bool(job.from_zero), "target_minutes": job.target_minutes,
        "batch_id": job.batch_id,
        "production_type": job.production_type or "original",
        "follow_up_id": job.follow_up_id,
        "status": job.status, "stage": job.stage, "progress": round(job.progress or 0, 3),
        "stages": json.loads(job.stages_json or "[]"),
        "result": json.loads(job.result_json or "{}"), "error": job.error,
        "created_at": job.created_at, "updated_at": job.updated_at,
        "completed_at": job.completed_at,
    }


def plan_stages(languages: list[str], from_zero: bool = False) -> list[dict]:
    host = ai_config.host.enabled
    names = (["research", "master_story"] if from_zero else []) + [
        "blueprint", "audio_plan"] + [f"spoken:{l}" for l in languages] + (
        ["host_plan"] if host else []) + [
        "film_length", "visual_needs", "visual_research", "visual_check", "visual_plan",
        "visual_gaps"]
    for l in languages:
        names += ([f"host:{l}"] if host else []) + [
            f"performance:{l}", f"voice:{l}", f"production:{l}", f"critique:{l}",
            f"render:{l}"]
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
        self.running: list[str] = []
        self.lang_errors: dict[str, str] = {}

    # -- bookkeeping ---------------------------------------------------------
    def _save(self):
        self.job.stages_json = json.dumps(self.stages, ensure_ascii=False, default=str)
        self.job.result_json = json.dumps(self.result, ensure_ascii=False, default=str)
        done = sum(1 for s in self.stages if s["status"] in ("done", "skipped"))
        self.job.progress = done / max(len(self.stages), 1)
        self.job.stage = (", ".join(self.running) or None) if self.running else None
        if self.job.stage and len(self.job.stage) > 60:
            self.job.stage = self.job.stage[:57] + "..."
        self.job.updated_at = utc_now()
        self.db.commit()

    def _check_cancel(self):
        self.db.refresh(self.job)
        if self.job.status == "cancelling":
            raise JobCancelled()

    def _entry(self, name: str) -> dict:
        st = next((s for s in self.stages if s["name"] == name), None)
        if st is None:  # jobs created before this stage existed
            st = {"name": name, "status": "pending", "detail": None}
            self.stages.append(st)
        return st

    async def _stage(self, name: str, fn):
        st = self._entry(name)
        if st["status"] in ("done", "skipped"):
            return st.get("detail")
        self._check_cancel()
        st["status"] = "running"
        self.running.append(name)
        self._save()
        try:
            detail = await fn()
        except Exception as e:
            st["status"] = "failed"
            st["detail"] = str(e)[:500]
            if name in self.running:
                self.running.remove(name)
            if not self.db.is_active:
                self.db.rollback()
            self._save()
            raise
        st["status"] = "skipped" if isinstance(detail, dict) and detail.get("skipped") else "done"
        st["detail"] = detail
        if name in self.running:
            self.running.remove(name)
        self._save()
        return detail

    def _skip_language(self, lang: str, reason: str):
        for st in self.stages:
            if st["name"].endswith(f":{lang}") and st["status"] == "pending":
                st["status"] = "blocked"
                st["detail"] = reason[:300]
        self._save()

    # -- run -----------------------------------------------------------------
    async def run(self):
        db, job = self.db, self.job
        case = db.get(Case, job.case_id)
        langs = json.loads(job.languages_json)
        pilot = job.mode == "pilot"
        pilot_seconds = job.pilot_seconds or ai_config.documentary.pilot_seconds
        state: dict = {}

        if job.from_zero:
            await self._stage("research",
                              lambda: research_case(db, case, self._check_cancel, job))
            d = await self._stage("master_story", lambda: write_master(db, case, job))
            if d and d.get("master_version_id"):
                job.master_version_id = d["master_version_id"]
                db.commit()
        if not job.master_version_id:
            raise RuntimeError("No master story for this job.")
        master = db.get(StoryVersion, job.master_version_id)

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
        focus_beats = pilot_beats(bp, pilot_seconds) if pilot else None

        # ---- phase B: spoken versions (all languages) + visual needs ---------
        spoken: dict[str, StoryVersion] = {}

        async def spoken_branch(lang: str):
            async def make_spoken():
                from app.documentary.spoken import SpokenNarrator

                v = latest_spoken(db, master, lang, bp_row.id)
                if v is None:
                    v = await SpokenNarrator().create(db, case, master, lang)
                gates = json.loads(v.critic_notes or "{}").get("quality_gates") or {}
                return {"version_id": v.id, "status": v.status,
                        "failures": gates.get("failures", [])}

            try:
                d = await self._stage(f"spoken:{lang}", make_spoken)
            except JobCancelled:
                raise
            except Exception as e:
                self.lang_errors[lang] = f"spoken: {type(e).__name__}: {e}"[:400]
                self._skip_language(lang, "spoken version failed")
                return
            spoken[lang] = db.get(StoryVersion, d["version_id"]) if d else latest_spoken(
                db, master, lang, bp_row.id)

        async def visual_needs():
            from app.documentary.visuals.planner import VisualPlanner

            row = latest_visual_plan(db, bp_row.id)
            if row is None:
                row = await VisualPlanner().create(db, case, bp_row)
            state["vp"] = row
            return {"visual_plan_id": row.id, "status": row.status}

        async def host_plan():
            from app.documentary.host import HostDirector, latest_host_plan

            if not ai_config.host.enabled:
                return {"skipped": True, "reason": "host disabled"}
            row = latest_host_plan(db, bp_row.id)
            if row is None:
                row = await HostDirector().create_plan(db, case, bp_row, master)
            state["host_plan"] = row
            plan = json.loads(row.plan_json or "{}")
            return {"host_plan_id": row.id, "status": row.status,
                    "segments": [f"{s['id']}:{s['position']}"
                                 + (f"@{s['beat_id']}" if s["position"] == "mid" else "")
                                 for s in plan.get("segments") or []],
                    "memory_updates": len(plan.get("memory_updates") or [])}

        await self._parallel([spoken_branch(l) for l in langs]
                             + [self._stage("visual_needs", visual_needs),
                                self._optional("host_plan", host_plan)],
                             shared_from=len(langs), shared_count=1)
        if "host_plan" not in state and ai_config.host.enabled:
            from app.documentary.host import latest_host_plan

            state["host_plan"] = latest_host_plan(db, bp_row.id)
        ok_langs = [l for l in langs if l in spoken]
        if not ok_langs:
            raise RuntimeError("No language could be told: " + "; ".join(
                f"{l}: {e}" for l, e in self.lang_errors.items()))

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

        vp_row = state.get("vp") or latest_visual_plan(db, bp_row.id)
        if vp_row is None:
            raise RuntimeError("No visual requirements could be planned.")
        requirements = json.loads(vp_row.requirements_json or "{}")
        planned = vp_row.status == "planned" and not job.refresh_visuals

        # ---- phase C: visual chain || each language's chain ----------------
        visual_ready = asyncio.Event()
        visual_state: dict = {}

        async def visual_chain():
            try:
                async def visual_research():
                    if planned:
                        return {"skipped": True,
                                "reason": "visual plan exists (refresh_visuals to redo)"}
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

                async def visual_gaps():
                    # production-time search for weak sentences; a plan made
                    # by an earlier job already had its search
                    if planned:
                        return {"skipped": True,
                                "reason": "visual plan exists (refresh_visuals to redo)"}
                    from app.documentary.visuals.gaps import fill_visual_gaps

                    return await fill_visual_gaps(db, case, db.get(VisualPlan, vp_row.id),
                                                  bp_row, ap_row, profile=job.render_profile,
                                                  beat_ids=focus_beats)

                await self._stage("visual_gaps", visual_gaps)
                visual_state["row"] = db.get(VisualPlan, vp_row.id)
            except BaseException as e:
                visual_state["error"] = e
                raise
            finally:
                visual_ready.set()

        renders = self.result.setdefault("renders", {})

        async def language_chain(lang: str):
            from app.documentary.spoken import spoken_blueprint

            version = spoken[lang]

            async def performance():
                from app.documentary.voice_performance import VoicePerformanceDirector

                lang_cfg = ai_config.voice.languages.get(lang)
                if not ai_config.voice_performance.enabled or not lang_cfg or (
                        lang_cfg.model_id not in ai_config.voice.elevenlabs.audio_tag_models):
                    return {"skipped": True, "reason": "voice model without audio tags"}
                row = await VoicePerformanceDirector().create(
                    db, case, version, spoken_blueprint(db, version) or bp,
                    beat_ids=focus_beats)
                data = json.loads(row.performance_json or "{}")
                return {"voice_performance_id": row.id, "status": row.status,
                        "incident_beat": data.get("incident_beat"),
                        "stats": data.get("stats")}

            async def host():
                from app.documentary.host import HostDirector, latest_host_segments

                plan_row = state.get("host_plan")
                if plan_row is None or plan_row.status == "invalid":
                    return {"skipped": True, "reason": "no usable host plan"}
                row = latest_host_segments(db, version.id)
                if row is None or row.host_plan_id != plan_row.id:
                    row = await HostDirector().write(db, case, version, plan_row)
                rep = json.loads(row.validation_json or "{}")
                return {"host_segments_id": row.id, "status": row.status,
                        "seconds": rep.get("host_seconds"),
                        "needs_review": rep.get("failed_segments")}

            await self._optional(f"host:{lang}", host)
            await self._stage(f"performance:{lang}", performance)
            holder: dict = {}

            async def voice():
                from app.documentary.production.audio import render_documentary_audio

                plan = performance_for_version(db, version)
                m = await render_documentary_audio(
                    plan, case_id=case.id, story_version_id=version.id,
                    max_seconds=pilot_seconds if pilot else None)
                holder["m"] = m
                return {"seconds": m.get("duration_seconds"),
                        "characters_paid": m.get("characters_paid"),
                        "music_paid": (m.get("mix") or {}).get("music_characters_paid"),
                        "audio_tags": plan.get("audio_tags"),
                        "flags": m.get("flags")}

            await self._stage(f"voice:{lang}", voice)
            manifest = holder.get("m") or _load_manifest(case.id, version)

            await visual_ready.wait()
            if "error" in visual_state:
                raise LanguageFailed("visual plan failed")
            plan_row = visual_state.get("row") or db.get(VisualPlan, vp_row.id)

            async def production():
                from app.documentary.production.script import build_production_script

                row = await build_production_script(
                    db, version, plan_row, manifest, mode=job.mode,
                    production_type=getattr(job, "production_type", None) or "original")
                return {"production_script_id": row.id, "duration": row.duration_seconds}

            d = await self._stage(f"production:{lang}", production)
            ps = db.get(ProductionScript, d["production_script_id"]) if d else latest_production(
                db, version.id)

            async def critique():
                from app.documentary.production.critics import DocumentaryCritics
                from app.documentary.production.script import display_words

                rep = await DocumentaryCritics().review(db, ps, display_words(manifest))
                return {"score": rep.get("score"), "fixes": len(rep.get("fixes") or []),
                        "issues": len(rep["deterministic"]["issues"])}

            await self._stage(f"critique:{lang}", critique)

            async def render():
                from app.documentary.render.engine import VideoRenderer

                script = json.loads(ps.script_json)
                out = storage.renders_dir(case.id, lang) / f"{job.mode}_v{version.id}_{ps.version}.mp4"
                async with slot("render"):
                    info = await asyncio.to_thread(VideoRenderer().render, script, out,
                                                   pilot_seconds if pilot else None)
                ps.render_json = json.dumps(info)
                ps.status = "rendered"
                db.commit()
                # the channel's memory: a Video with status, opening and
                # YouTube metadata (UNSOLVED / follow-up titles)
                from app.lifecycle.videos import register_render

                video = register_render(db, job, ps, info)
                renders[lang] = {"production_script_id": ps.id, "video_id": video.id,
                                 "youtube_title": video.youtube_title, **info}
                return {**info, "video_id": video.id, "youtube_title": video.youtube_title}

            await self._stage(f"render:{lang}", render)

        async def guarded(lang: str):
            async with slot("languages"):
                try:
                    await language_chain(lang)
                except JobCancelled:
                    raise
                except Exception as e:
                    self.lang_errors[lang] = f"{type(e).__name__}: {e}"[:400]
                    self._skip_language(lang, str(e))

        await self._parallel([visual_chain()] + [guarded(l) for l in ok_langs], shared_from=0,
                             shared_count=1)
        if self.lang_errors:
            self.result["errors"] = self.lang_errors
            if not renders:
                raise RuntimeError("; ".join(f"{l}: {e}" for l, e in self.lang_errors.items()))
        return self.result

    async def _optional(self, name: str, fn):
        """A stage whose failure is recorded but does not stop the job."""
        try:
            return await self._stage(name, fn)
        except JobCancelled:
            raise
        except Exception as e:
            log.warning("optional stage %s failed: %s", name, e)
            self.result.setdefault("warnings", {})[name] = f"{type(e).__name__}: {e}"[:300]
            self._save()
            return None

    async def _parallel(self, coros: list, shared_from: int, shared_count: int | None = None):
        """Run branches together. Failures of language branches are
        recorded by the branches themselves; a cancelled job or a failed
        SHARED branch (indices shared_from .. +shared_count) ends the job
        after every branch has stopped."""
        results = await gather_limited(None, coros, return_exceptions=True)
        shared = range(shared_from, shared_from + (shared_count if shared_count is not None
                                                   else len(coros) - shared_from))
        for k, r in enumerate(results):
            if isinstance(r, JobCancelled):
                raise r
        for k, r in enumerate(results):
            if isinstance(r, BaseException) and k in shared:
                raise r


async def research_case(db: Session, case: Case, check_cancel,
                        doc_job: DocumentaryJob | None = None) -> dict:
    """From zero, step 1: research with the configured search engine and
    wait for the facts (skipped when the case already has facts). A
    follow-up film always researches the new developments first."""
    from app.db.models import ResearchJob
    from app.services import research_jobs

    follow_up = doc_job is not None and doc_job.production_type == "follow_up"
    facts = db.query(Fact).filter(Fact.case_id == case.id).count()
    if facts and not follow_up:
        return {"skipped": True, "reason": f"case already has {facts} facts"}
    job = (db.query(ResearchJob).filter(ResearchJob.case_id == case.id,
                                        ResearchJob.job_type == "research",
                                        ResearchJob.status.in_(("queued", "running")))
           .order_by(ResearchJob.id.desc()).first())
    if job is None and follow_up:
        from app.lifecycle.followups import start_update_research

        job = await start_update_research(db, case, doc_job.follow_up_id)
    if job is None:
        job = await research_jobs.start_research_job(db, case)
    waited = 0.0
    while job.status in ("queued", "running"):
        check_cancel()
        await asyncio.sleep(RESEARCH_POLL_SECONDS)
        waited += RESEARCH_POLL_SECONDS
        job = await research_jobs.poll_job(db, job)
        if waited > RESEARCH_MAX_HOURS * 3600:
            raise RuntimeError(f"Research job {job.id} did not finish in time.")
    if job.status != "completed":
        raise RuntimeError(f"Research job {job.id} {job.status}: {job.error or ''}"[:400])
    facts = db.query(Fact).filter(Fact.case_id == case.id).count()
    return {"research_job_id": job.id, "facts": facts,
            "sources": job.sources_accepted}


async def write_master(db: Session, case: Case, job: DocumentaryJob) -> dict:
    """From zero, step 2: the English master story at the film's length
    (reuses a master that already exists)."""
    from app.agents.story import StoryPipeline
    from app.services.readiness import build_readiness

    canonical = ai_config.multilingual.canonical_language
    follow_up = None
    if job.production_type == "follow_up" and job.follow_up_id:
        from app.db.models import FollowUpCandidate
        from app.lifecycle.followups import context as follow_up_context

        fu = db.get(FollowUpCandidate, job.follow_up_id)
        follow_up = follow_up_context(db, fu) if fu else None
    existing = (db.query(StoryVersion)
                .filter(StoryVersion.case_id == case.id, StoryVersion.kind == "master",
                        StoryVersion.language == canonical)
                .order_by(StoryVersion.id.desc()).all())
    for row in existing:
        struct = json.loads(row.narrative_structure or "{}")
        same_film = ((struct.get("follow_up") or {}).get("follow_up_id") == job.follow_up_id
                     if follow_up else not struct.get("follow_up"))
        if same_film:
            return {"master_version_id": row.id, "reused": True}
    from app.schemas import GenerateStoryRequest

    defaults = GenerateStoryRequest()
    minutes = int(job.target_minutes or max(ai_config.story.default_target_minutes,
                                            ai_config.documentary.min_film_minutes))
    report = build_readiness(db, case.id, minutes, provider_status={})
    status = report["master_readiness"]["status"]
    if status in ("incomplete_evidence", "failed", "insufficient_research"):
        raise RuntimeError(
            f"Master story blocked ({status}): {report['master_readiness'].get('reason')}")
    roles = {"director": "master_story_director", "writer": "master_writer",
             "rewriter": "master_rewriter", "critic": "master_engagement_critic",
             "final_editor": "master_final_editor"}
    story = await StoryPipeline(roles=roles).run(
        db=db, case=case, target_minutes=minutes, language=canonical,
        tone=defaults.tone, iterations=defaults.iterations, kind="master",
        words_per_minute=ai_config.words_per_minute_for(canonical), follow_up=follow_up)
    struct = json.loads(story.narrative_structure or "{}")
    return {"master_version_id": story.id, "minutes": minutes, "status": story.status,
            "opening_strategy": struct.get("opening_strategy"),
            "production_type": job.production_type or "original"}


def _load_manifest(case_id: int, version: StoryVersion) -> dict:
    from app.documentary.voice_render import VoiceRenderer

    out = VoiceRenderer().out_dir(case_id, version.language or "en", version.id)
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


async def verify_candidates(db: Session, case: Case, blueprint: dict, requirements: dict,
                            beat_ids: list[str] | None, profile: str | None,
                            per_requirement: int | None = None) -> dict:
    """Vision-check the best unverified candidates of each need (a pilot
    only checks the beats it shows)."""
    from app.documentary.visuals.director import blocked_at, rank_candidates

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
            n = per_requirement or ai_config.visual_verification.per_requirement
            for _, a in rank_candidates(loose, ent, assets, blocked, profile)[:n]:
                if a.verification_status == "unverified" and a not in todo:
                    todo.append(a)
    return await verify_assets(db, case, todo, list(ents.values()))


async def verify_assets(db: Session, case: Case, assets: list[VisualAsset],
                        entities: list[dict]) -> dict:
    """Vision-check these assets against the case evidence (shared by the
    visual_check stage and the production-time search)."""
    from app.agents.story import build_evidence_pack
    from app.db.models import Contradiction, Source
    from app.documentary.visuals.verification import VisualVerificationAgent

    if not assets:
        return {"checked": 0}
    pack = build_evidence_pack(
        db.query(Fact).filter(Fact.case_id == case.id).all(),
        db.query(Contradiction).filter(Contradiction.case_id == case.id).all(),
        db.query(Source).filter(Source.case_id == case.id).all())
    agent = VisualVerificationAgent()
    # all candidates at once; the vision limit (concurrency.vision) paces them
    results = await gather_limited(
        None, [agent.verify(db, case, a, entities, pack["facts"]) for a in assets],
        return_exceptions=True)
    counts: dict[str, int] = {}
    errors = 0
    for a, r in zip(assets, results):
        if isinstance(r, Exception):
            errors += 1
            continue
        counts[a.verification_status] = counts.get(a.verification_status, 0) + 1
    return {"checked": len(assets), **counts, **({"errors": errors} if errors else {})}


# ---------------------------------------------------------------------------
# launching
# ---------------------------------------------------------------------------


def create_job(db: Session, case: Case, master: StoryVersion | None, languages: list[str],
               mode: str, pilot_seconds: float | None, profile: str,
               refresh_visuals: bool = False, from_zero: bool = False,
               target_minutes: float | None = None, batch_id: str | None = None
               ) -> DocumentaryJob:
    if master is None and not from_zero:
        raise ValueError("A job needs a master story unless it starts from zero.")
    job = DocumentaryJob(
        case_id=case.id, master_version_id=master.id if master else None, mode=mode,
        refresh_visuals=refresh_visuals, from_zero=from_zero,
        target_minutes=target_minutes, batch_id=batch_id,
        languages_json=json.dumps(languages), pilot_seconds=pilot_seconds,
        render_profile=profile, status="queued",
        stages_json=json.dumps(plan_stages(languages, from_zero)),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def new_batch_id() -> str:
    return "b" + uuid.uuid4().hex[:12]


async def run_job(job_id: int, session_factory=None):
    """Runs one documentary. Waits in "queued" until one of the
    concurrency.jobs slots is free (several documentaries in parallel)."""
    from app.db.base import SessionLocal

    db = (session_factory or SessionLocal)()
    try:
        async with slot("jobs"):
            job = db.get(DocumentaryJob, job_id)
            db.refresh(job)
            if job.status in ("cancelling", "cancelled"):
                job.status = "cancelled"
                job.completed_at = utc_now()
                db.commit()
                return
            job.status = "running"
            job.error = None
            db.commit()
            pipeline = DocumentaryPipeline(db, job)
            try:
                result = await pipeline.run()
                job.status = "partial" if (result or {}).get("errors") else "completed"
                if job.status == "partial":
                    job.error = "; ".join(f"{l}: {e}" for l, e in result["errors"].items())[:1000]
            except JobCancelled:
                job.status = "cancelled"
            except Exception as e:  # recorded on the job, visible in the UI
                log.error("documentary job %s failed: %s", job_id, traceback.format_exc())
                db.rollback()
                job = db.get(DocumentaryJob, job_id)
                job.status = "failed"
                job.error = f"{type(e).__name__}: {e}"[:1000]
            job.stage = None
            job.completed_at = utc_now()
            job.updated_at = utc_now()
            db.commit()
    finally:
        db.close()
        _running.pop(job_id, None)


def launch(job_id: int) -> None:
    _running[job_id] = asyncio.create_task(run_job(job_id))


def scheduler_status(db: Session) -> dict:
    """What runs now and what waits (several documentaries in parallel)."""
    from app.core.concurrency import usage

    active = (db.query(DocumentaryJob)
              .filter(DocumentaryJob.status.in_(("queued", "running", "cancelling")))
              .order_by(DocumentaryJob.id).all())
    return {
        "max_parallel_jobs": ai_config.concurrency.jobs,
        "running": [job_dict(j) for j in active if j.status != "queued"],
        "queued": [job_dict(j) for j in active if j.status == "queued"],
        "limits": usage(),
    }
