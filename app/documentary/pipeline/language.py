"""One language's chain after the shared stages: host, performance, voice,
production, critique, visual audit, render. Runs next to the visual chain and
waits for it before building the production script."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from app.core.ai_config import ai_config
from app.core.concurrency import slot
from app.db.models import ProductionScript, VisualPlan
from app.documentary import storage
from app.documentary.mvp import coverage as CV
from app.documentary.performance import performance_for_version
from app.documentary.pipeline.base import record_warning
from app.documentary.pipeline.errors import AwaitingPictureReview, LanguageFailed


def saved_render_path(ps) -> Path | None:
    """The film of a production script as its saved render record names it: a job that is picked up again after its render stage
    finished does not run that stage, so the review reads the film from here."""
    return storage.resolve(json.loads(ps.render_json or "{}").get("path"))


class LanguageChain:
    def __init__(self, pipe, lang: str, version, shared):
        self.pipe = pipe  # the DocumentaryPipeline: db, job, stage bookkeeping
        self.lang = lang
        self.version = version
        self.shared = shared  # PipelineShared: what the shared stages produced

    async def run(self) -> None:
        """The language's chain. The host's text and then its voice and avatar videos start the moment the film's version exists, next to
        the narration; the chain waits for them where the film needs them, and a chain that stops first stops them too."""
        self.host_media: asyncio.Task | None = None
        try:
            await self._run()
        finally:
            if self.host_media is not None and not self.host_media.done():
                self.host_media.cancel()
            if self.host_media is not None:
                await asyncio.gather(self.host_media, return_exceptions=True)

    async def _run(self) -> None:
        from app.agents.stages import get_stage_agent
        from app.documentary.jobs import (
            _load_manifest,
            latest_production,
            render_cap_seconds,
            tts_budget_left,
        )
        from app.documentary.spoken import spoken_blueprint

        pipe, lang, version, sh = self.pipe, self.lang, self.version, self.shared
        db, job, case = pipe.db, pipe.job, sh.case
        bp, focus_beats, state = sh.bp, sh.focus_beats, sh.state
        pilot, pilot_seconds = sh.pilot, sh.pilot_seconds
        vp_row, visual_ready, visual_state, renders = (
            sh.vp_row, sh.visual_ready, sh.visual_state, sh.renders)

        planning = job.mode == "plan"       # the pictures only: the narration of an earlier run is used, nothing is made
        from app.documentary import voice_review as VRV

        approved = not planning and VRV.kept(version, lang)         # the narration the owner approved is used as it is, never made again

        async def skipped(reason: str) -> dict:
            return {"skipped": True, "reason": reason}

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
            from app.documentary.outfits import follow_english_outfit

            follow_english_outfit(db, case.id, lang)        # the host wears the English film's outfit, in every other language too
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

        voices_first = lang in ai_config.host.avatar_after_voice_approval_languages          # the owner hears the voices first; the videos follow his approval

        async def mvp_host():
            from app.documentary.mvp import pipeline as MVP

            return await MVP.stage_host(db, case, version, lang)

        async def mvp_host_voice():
            from app.documentary.mvp import pipeline as MVP

            return await MVP.stage_host(db, case, version, lang, until="voice")

        async def host_media():
            await pipe._optional(f"host:{lang}", host)
            if voices_first:
                await pipe._stage(f"host_voice:{lang}", mvp_host_voice)
            else:
                await pipe._stage(f"mvp_host:{lang}", mvp_host)

        if not planning:
            self.host_media = asyncio.create_task(host_media())
            await pipe._stage(f"performance:{lang}", performance)
        else:
            await pipe._stage(f"performance:{lang}", lambda: skipped("planning job: no narration is made"))
        holder: dict = {}

        async def render_voice():
            from app.documentary import semantic_review as SR
            from app.documentary.production.audio import render_documentary_audio
            from app.documentary.voice_render import VoiceRenderer

            paid = pipe.result.setdefault("tts_characters", {})
            if not tts_budget_left(paid, ai_config.documentary.max_tts_characters_per_job):
                raise LanguageFailed(
                    "narration budget reached "
                    f"({ai_config.documentary.max_tts_characters_per_job} characters)")
            plan = performance_for_version(db, version)
            renderer = VoiceRenderer(db=db, budget_case_id=case.id,
                                     budget_stage=f"tts:{lang}",
                                     use_asr=ai_config.listens_after_voice(lang),       # a semantically reviewed language is judged by a model that listens, not by the speech-to-text check
                                     documentary_job_id=job.id,
                                     budget_pilot_seconds=pilot_seconds if pilot else None)
            if ai_config.voice.after_voice_review and SR.required(lang):           # after the voice-repair loop of each block, a model listens to it and judges it against the source
                renderer.reviewer = SR.Reviewer(db, case.id, SR.english_sources(db, version), SR.tones_of(db, version))
            m = await render_documentary_audio(
                plan, case_id=case.id, story_version_id=version.id,
                max_seconds=pilot_seconds if pilot else None, db=db, renderer=renderer)
            holder["m"] = m
            paid[lang] = int(m.get("characters_paid") or 0)
            out = {"seconds": m.get("duration_seconds"),
                   "characters_paid": m.get("characters_paid"),
                   "music_paid": (m.get("mix") or {}).get("music_characters_paid"),
                   "library_misses": (m.get("mix") or {}).get("library_misses", []),
                   "library_notes": (m.get("mix") or {}).get("library_notes", []),
                   "audio_tags": plan.get("audio_tags"),
                   "flags": m.get("flags"),
                   "music_history_snapshot": (m.get("mix") or {}).get(
                       "music_history_snapshot", []),
                   "music_library_identity": (m.get("mix") or {}).get(
                       "music_library_identity", []),
                   "selection_reasons": (m.get("mix") or {}).get(
                       "selection_reasons", [])}
            # the speech-to-text check is the narration's auditor: takes it still
            # rejects after the retakes are reported, never silently kept
            failed = [f for f in m.get("flags") or []
                      if f.endswith(":asr_check_failed")]
            if failed:
                out["degraded"] = (f"{len(failed)} narration block(s) failed the listening "
                                   "check after retakes: " + ", ".join(failed[:6]))
            return out

        def pass_turn():
            index = sh.languages.index(lang)
            if index + 1 < len(sh.music_turns):
                sh.music_turns[index + 1].set()

        async def voice():
            turn = sh.music_turns[sh.languages.index(lang)]
            await turn.wait()
            try:
                async with slot("voice_stage"):          # one narration is voiced at a time, however many languages are in the run
                    return await render_voice()
            finally:
                pass_turn()

        async def mix_approved():
            """The narration the owner approved has no music under it yet (the review makes the voice alone): the music is mixed under that exact audio, once."""
            from app.documentary.production.audio import mix_narration
            from app.documentary.voice_render import VoiceRenderer

            folder = VoiceRenderer.out_dir_for(case.id, lang, version.id)
            manifest = _load_manifest(case.id, version)
            if manifest.get("mix") and (folder / "documentary.mp3").exists():
                return {"skipped": True, "reason": "the narration the owner approved is kept, its music is mixed"}
            plan = performance_for_version(db, version)
            manifest = await mix_narration(manifest, plan, folder, db=db)
            holder["m"] = manifest
            mix = manifest.get("mix") or {}
            return {"skipped": True, "reason": "the narration the owner approved is kept, the music is mixed under it",
                    "music_moments": mix.get("music_moments"), "silences": mix.get("silences"), "beds": mix.get("beds"),
                    "library_misses": mix.get("library_misses", []), "music_paid": mix.get("music_characters_paid")}

        async def reuse_narration():
            if planning:
                pass_turn()
                return await skipped("planning job: the narration of an earlier run is used")
            await sh.music_turns[sh.languages.index(lang)].wait()
            try:
                return await mix_approved()
            finally:
                pass_turn()

        await pipe._stage(f"voice:{lang}", reuse_narration if planning or approved else voice)
        try:
            manifest = holder.get("m") or _load_manifest(case.id, version)
        except FileNotFoundError as e:
            raise LanguageFailed(f"A planning job uses the narration of an earlier run, and the {lang} version has "
                                 "none: run a pilot or a full film first.") from e

        if not planning:
            from app.documentary import voice_review as VRV

            VRV.guard(version, lang)         # a language the owner hears first: the run waits here, before the pictures are planned
        await visual_ready.wait()
        if "error" in visual_state:
            raise LanguageFailed("visual plan failed")
        needs_plan = visual_state.get("row") or db.get(VisualPlan, vp_row.id)

        from app.documentary.mvp import pipeline as MVP
        from app.documentary.mvp import review as RV

        RV.guard_no_progress_past_review(db, case.id, lang)
        if (lang != "en" and ai_config.mvp().review.require_english_first
                and not RV.english_approved(db, case.id)):
            raise LanguageFailed("Full-film production for this language requires the owner to watch and "
                                 "explicitly approve the English film first.")

        if planning:
            await pipe._stage(f"mvp_host:{lang}", lambda: skipped("planning job: the Host is not made"))
        else:
            await self.host_media          # made next to the narration; a failure of it stops the film here, as it always did
            if voices_first:
                await pipe._stage(f"mvp_host:{lang}", mvp_host)          # the narration is approved (the guard above): now the videos are made

        async def mvp_direct():
            from app.documentary.mvp import reference as REF

            try:
                if not REF.is_reference_film(db, needs_plan):      # the English film's pictures were reviewed in the English film
                    MVP.stage_owner_attention(db, needs_plan, lang)          # review 1: everything
                    await MVP.stage_cut(db, case, needs_plan, state["bp"], lang)     # then trim and cut; review 2: only those
            except AwaitingPictureReview:
                MVP.request_review(db, needs_plan)
                raise
            plan, report = await MVP.stage_direct(db, case, needs_plan, state["bp"], manifest, lang)
            holder["plan"] = plan
            out = {"visual_plan_id": plan.id, **{k: report[k] for k in (
                "shots", "gap_seconds", "agent_directed_beats", "assets_used")}}
            if report["gaps"]:
                out["degraded"] = (f"{len(report['gaps'])} moment(s) without an approved visual "
                                   f"({report['gap_seconds']}s)")
            crime = report.get("crime_maps")
            if crime:
                out["crime_maps"] = crime
                if crime.get("degraded"):
                    out["degraded"] = "; ".join(filter(None, [out.get("degraded"), crime["degraded"]]))
            return out

        direct = await pipe._stage(f"mvp_direct:{lang}", mvp_direct)
        plan_row = holder["plan"] if "plan" in holder else db.get(VisualPlan, direct["visual_plan_id"])

        async def mvp_complete():
            return MVP.stage_complete(plan_row, lang)

        await pipe._stage(f"mvp_complete:{lang}", mvp_complete)

        async def mvp_portraits():
            return await MVP.stage_portraits_ready(db, case, plan_row, manifest, lang, wait=not planning)

        await pipe._stage(f"mvp_portraits:{lang}", mvp_portraits)
        if planning:
            for name in ("zoom_maps", "production", "critique", "mvp_coverage", "render", "mvp_review"):
                await pipe._stage(f"{name}:{lang}", lambda: skipped("planning job: it ends with the planned pictures"))
            return

        async def zoom_maps():
            from app.documentary.zoom_map.stage import render_zoom_maps

            return await render_zoom_maps(db, case, plan_row, lang)

        await pipe._stage(f"zoom_maps:{lang}", zoom_maps)

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
            return {"production_script_id": ps.id,
                    "score": rep.get("score"), "fixes": len(rep.get("fixes") or []),
                    "issues": len(rep["deterministic"]["issues"])}

        await pipe._stage(f"critique:{lang}", critique)

        async def mvp_coverage():
            db.refresh(ps)
            script = json.loads(ps.script_json)
            report = MVP.stage_coverage(db, needs_plan, script, manifest)
            ps.audit_json = json.dumps(report, ensure_ascii=False)
            db.commit()
            out = {"production_script_id": ps.id, **{k: report[k] for k in (
                "temporal_coverage", "black_seconds", "longest_black", "weak_sentence_count",
                "review_ready")}, "requirements": report["requirements"]}
            if report["blocking"]:
                out["degraded"] = "; ".join(report["blocking"])
            return out

        await pipe._stage(f"mvp_coverage:{lang}", mvp_coverage)

        async def render():
            from app.documentary.render.engine import VideoRenderer

            script = json.loads(ps.script_json)
            # Owner decision 2026-10-22: a render (pilot or full) is never blocked for lacking an
            # avatar (A2) — only publishing is (app.lifecycle.videos.publish_blockers, A3).
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
                    cap = render_cap_seconds(bp, focus_beats, pilot_seconds, script) if pilot else None
                    info = await asyncio.to_thread(VideoRenderer().render, script, out, cap)
                info["script_sha256"] = script_sha
                ps.render_json = json.dumps(info)
                ps.status = "rendered"
                db.commit()
            # the channel's memory: a Video with status, opening and
            # YouTube metadata (UNSOLVED / follow-up titles)
            from app.lifecycle.videos import register_render

            video = register_render(db, job, ps, info)
            for warning in info.get("warnings") or []:
                record_warning(pipe.result, warning.get("stage", "render"),
                               warning.get("reason", warning.get("message", "render warning")),
                               warning.get("language", lang), warning.get("code", "STAGE_WARNING"))
            renders[lang] = {"production_script_id": ps.id, "video_id": video.id,
                             "youtube_title": video.youtube_title, **info}
            holder["render_path"] = out
            return {**info, "video_id": video.id, "youtube_title": video.youtube_title}

        await pipe._stage(f"render:{lang}", render)

        async def mvp_review():
            db.refresh(ps)
            coverage = json.loads(ps.audit_json or "{}")
            film = holder.get("render_path") or saved_render_path(ps)
            probe = await asyncio.to_thread(CV.probe_file, film)
            out = MVP.stage_review(db, case, lang, ps.id, film, coverage, probe)
            return {**out, "file": probe,
                    **({"degraded": "; ".join(out["blocking"])} if out["blocking"] else {})}

        await pipe._stage(f"mvp_review:{lang}", mvp_review)
