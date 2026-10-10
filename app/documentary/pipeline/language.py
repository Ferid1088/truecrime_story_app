"""One language's chain after the shared stages: host, performance, voice,
production, critique, visual audit, render. Runs next to the visual chain and
waits for it before building the production script."""

from __future__ import annotations

import asyncio
import hashlib
import json

from app.core.ai_config import ai_config
from app.core.concurrency import slot
from app.db.models import ProductionScript, VisualPlan
from app.documentary import storage
from app.documentary.performance import performance_for_version
from app.documentary.pipeline.errors import LanguageFailed


class LanguageChain:
    def __init__(self, pipe, lang: str, version, shared):
        self.pipe = pipe  # the DocumentaryPipeline: db, job, stage bookkeeping
        self.lang = lang
        self.version = version
        self.shared = shared  # PipelineShared: what the shared stages produced

    async def run(self) -> None:
        from app.agents.stages import get_stage_agent
        from app.documentary.jobs import _load_manifest, latest_production, tts_budget_left
        from app.documentary.spoken import spoken_blueprint

        pipe, lang, version, sh = self.pipe, self.lang, self.version, self.shared
        db, job, case = pipe.db, pipe.job, sh.case
        bp, focus_beats, state = sh.bp, sh.focus_beats, sh.state
        pilot, pilot_seconds = sh.pilot, sh.pilot_seconds
        vp_row, visual_ready, visual_state, renders = (
            sh.vp_row, sh.visual_ready, sh.visual_state, sh.renders)

        async def performance():

            lang_cfg = ai_config.voice.languages.get(lang)
            if not ai_config.voice_performance.enabled or not lang_cfg or (
                    lang_cfg.model_id not in ai_config.voice.elevenlabs.audio_tag_models):
                return {"skipped": True, "reason": "voice model without audio tags"}
            row = await get_stage_agent("voice_performance").create(
                db, case, version, spoken_blueprint(db, version) or bp,
                beat_ids=focus_beats)
            data = json.loads(row.performance_json or "{}")
            out = {"voice_performance_id": row.id, "status": row.status,
                   "incident_beat": data.get("incident_beat"),
                   "stats": data.get("stats")}
            # "partial" alone is normal for a pilot (only its beats are
            # directed); direction ERRORS mean lines were left plain
            errs = json.loads(row.validation_json or "{}").get("errors") or []
            if errs:
                out["degraded"] = (f"voice performance: {len(errs)} direction errors — "
                                   "those lines are read plainly")
            return out

        async def host():
            from app.documentary.host import latest_host_segments

            plan_row = state.get("host_plan")
            if plan_row is None or plan_row.status == "invalid":
                return {"skipped": True, "reason": "no usable host plan"}
            row = latest_host_segments(db, version.id)
            if row is None or row.host_plan_id != plan_row.id:
                row = await get_stage_agent("host").write(db, case, version, plan_row)
            rep = json.loads(row.validation_json or "{}")
            # the scenes (channel studio + framing, text frozen) are
            # planned now; voice and avatar run per scene on request
            from app.documentary.host_scenes import plan_host_scenes
            from app.documentary.studio import StudioError

            try:
                scenes = [s.id for s in plan_host_scenes(db, row)]
                scene_error = None
            except StudioError as e:
                scenes, scene_error = [], str(e)
            # segments the host critic did not approve (after its
            # rewrites) get no scene: left out of the film
            left_out = rep.get("failed_segments") or []
            return {"host_segments_id": row.id, "status": row.status,
                    "seconds": rep.get("host_seconds"),
                    "left_out_segments": left_out,
                    "host_scene_ids": scenes,
                    **({"degraded": f"host segment(s) not approved, left out: "
                                    f"{', '.join(left_out)}"} if left_out else {}),
                    **({"studio_error": scene_error} if scene_error else {})}

        await pipe._optional(f"host:{lang}", host)
        await pipe._stage(f"performance:{lang}", performance)
        holder: dict = {}

        async def voice():
            from app.documentary.production.audio import render_documentary_audio

            paid = pipe.result.setdefault("tts_characters", {})
            if not tts_budget_left(paid, ai_config.documentary.max_tts_characters_per_job):
                raise LanguageFailed(
                    "narration budget reached "
                    f"({ai_config.documentary.max_tts_characters_per_job} characters)")
            plan = performance_for_version(db, version)
            m = await render_documentary_audio(
                plan, case_id=case.id, story_version_id=version.id,
                max_seconds=pilot_seconds if pilot else None)
            holder["m"] = m
            paid[lang] = int(m.get("characters_paid") or 0)
            out = {"seconds": m.get("duration_seconds"),
                   "characters_paid": m.get("characters_paid"),
                   "music_paid": (m.get("mix") or {}).get("music_characters_paid"),
                   "audio_tags": plan.get("audio_tags"),
                   "flags": m.get("flags")}
            # the listening check (speech-to-text, pronunciation) is the
            # narration's auditor: takes it still rejects after the
            # retakes are reported, never silently kept
            failed = [f for f in m.get("flags") or []
                      if f.endswith((":asr_check_failed", ":pronunciation_unresolved"))]
            if failed:
                out["degraded"] = (f"{len(failed)} narration block(s) failed the listening "
                                   "check after retakes: " + ", ".join(failed[:6]))
            return out

        await pipe._stage(f"voice:{lang}", voice)
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

        d = await pipe._stage(f"production:{lang}", production)
        ps = db.get(ProductionScript, d["production_script_id"]) if d else latest_production(
            db, version.id)

        async def critique():
            from app.documentary.production.critics import DocumentaryCritics
            from app.documentary.production.script import display_words

            rep = await DocumentaryCritics().review(db, ps, display_words(manifest))
            return {"score": rep.get("score"), "fixes": len(rep.get("fixes") or []),
                    "issues": len(rep["deterministic"]["issues"])}

        await pipe._stage(f"critique:{lang}", critique)

        async def visual_audit():
            # the gate before render: every picture and clip on screen
            # verified and approved for the words spoken over it
            # (rejected ones replaced and audited again, else left out)
            from app.documentary.visuals.auditor import audit_script, audit_summary

            if not ai_config.visual_audit.enabled:
                return {"skipped": True, "reason": "visual_audit disabled"}
            db.refresh(ps)
            return audit_summary(await audit_script(db, ps))

        await pipe._stage(f"visual_audit:{lang}", visual_audit)

        async def render():
            from app.documentary.render.engine import VideoRenderer

            script = json.loads(ps.script_json)
            script_sha = hashlib.sha256(ps.script_json.encode("utf-8")).hexdigest()
            out = storage.renders_dir(case.id, lang) / f"{job.mode}_v{version.id}_{ps.version}.mp4"
            prev = json.loads(ps.render_json or "null") if ps.status == "rendered" else None
            if (prev and prev.get("path") == storage.rel(out)
                    and prev.get("script_sha256") == script_sha
                    and out.exists() and out.stat().st_size > 0):
                # rendered before the process stopped: the film is
                # complete (it is only renamed into place when ffmpeg
                # finished), so only the registration is redone
                info = {**prev, "reused": True}
            else:
                async with slot("render"):
                    info = await asyncio.to_thread(VideoRenderer().render, script, out,
                                                   pilot_seconds if pilot else None)
                info["script_sha256"] = script_sha
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

        await pipe._stage(f"render:{lang}", render)
