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
import re
import traceback
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.agents.stages import get_stage_agent
from app.agents.story import MASTER_ROLES, stored_sections
from app.core.ai_config import ai_config
from app.core.concurrency import gather_limited, slot
from app.core.policy import PolicyDecision
from app.db.models import (
    Artifact,
    Case,
    DocumentaryJob,
    EditLog,
    Fact,
    ProductionScript,
    StoryVersion,
    VisualAsset,
    VisualPlan,
)
from app.documentary.audio_director import latest_audio_plan
from app.documentary.blueprint import latest_blueprint
from app.utils import ensure_utc, utc_now

log = logging.getLogger(__name__)
_running: dict[int, asyncio.Task] = {}

RESEARCH_POLL_SECONDS = 15.0
RESEARCH_MAX_HOURS = 6.0


class MasterApprovalError(RuntimeError):
    """A failed master gate with safe, UI-ready diagnostics."""

    def __init__(self, message: str, detail: dict):
        super().__init__(message)
        self.detail = detail


from app.documentary.pipeline.base import StageRunner  # noqa: E402
from app.documentary.pipeline.errors import JobCancelled, LanguageFailed, SpokenRejected  # noqa: E402,F401
from app.services.policy_events import record_decision  # noqa: E402
from app.services.retry_policy import ExhaustedAction, RetryAuditPolicy  # noqa: E402
from app.services.budget import BudgetStopped  # noqa: E402


async def approve_master(db: Session, case: Case, v: StoryVersion,
                         max_redos: int | None = None,
                         target_minutes: float | None = None,
                         words_per_minute: int | None = None) -> tuple[StoryVersion, list[dict]]:
    """The master story's approval gate: its critics and quality gates
    must have passed (status "ready"). A master marked needs_revision is
    revised with exactly the reasons it failed — the revision is a new
    version, judged again by the same critics — at most max_redos times.
    Returns (approved version, the rejected attempts). Raises when it is
    still not approved: there is no film without an approved story."""
    from app.agents.story import StoryPipeline

    limit = ai_config.documentary.max_redos if max_redos is None else max_redos
    if (target_minutes is not None
            and target_minutes < ai_config.story.min_target_minutes):
        limit = min(limit, ai_config.master_generation.max_pilot_approval_revisions)
    current = v
    rejected: list[dict] = []

    async def produce(feedback: dict) -> StoryVersion:
        nonlocal current
        if feedback["reasons"]:
            notes = json.loads(current.critic_notes or "{}")
            instruction = (
                "Revise the story so that it passes these failed quality checks, and change "
                "nothing else: " + json.dumps(feedback["reasons"], ensure_ascii=False)
                + (". Critic notes: " + str(notes.get("summary") or notes.get("notes"))[:1500]
                   if notes.get("summary") or notes.get("notes") else ""))
            if target_minutes is None:
                # Preserve the small public helper's historical behavior for
                # callers that are not running inside a documentary job.
                current = await StoryPipeline(roles=MASTER_ROLES).improve(
                    db, case, current, instruction)
            else:
                current = await StoryPipeline(roles=MASTER_ROLES).improve(
                    db, case, current, instruction,
                    target_minutes=target_minutes,
                    words_per_minute=words_per_minute,
                )
        return current

    async def audit(version: StoryVersion) -> dict:
        notes = json.loads(version.critic_notes or "{}")
        failures = (notes.get("quality_gates") or {}).get("failures") or []
        # A human can approve a version "as is" over its own critics'
        # objections (control at every stage). That sets status "ready"
        # directly but deliberately keeps the original critic scores and
        # problems, so `failures` here can be non-empty for an approved
        # version — the manual override, not an empty failures list, is
        # what makes it approved.
        manually_approved = bool(version.approved_by)
        if version.status == "needs_revision":
            rejected.append({
                "version_id": version.id,
                "failures": failures,
                "quality_gates": notes.get("quality_gates") or {},
            })
        # A deterministic length miss is not made better by repeating the
        # same approval rewrite. The caller will surface the measured band.
        return {
            "approved": version.status != "needs_revision" and (not failures or manually_approved),
            "reasons": [] if manually_approved else failures,
            "quality_gates": notes.get("quality_gates") or {},
            "terminal": "length_out_of_range" in failures and not manually_approved,
            "manually_approved": manually_approved,
        }

    # A deterministic length miss cannot be repaired by a quality revision.
    # Check it before the retry policy starts so a pilot never spends another
    # writer/validator/critic graph on a story that is already the wrong
    # size. This reads `current`'s own stored notes directly rather than
    # calling the shared `audit()` closure above: that closure also records
    # the version into `rejected` as a side effect, and the retry policy's
    # own first iteration below audits this same, unchanged version again
    # (its first `produce()` call is a no-op with no revision reasons yet) —
    # calling `audit()` here too would double-count that one version.
    initial_notes = json.loads(current.critic_notes or "{}")
    initial_quality_gates = initial_notes.get("quality_gates") or {}
    initial_failures = initial_quality_gates.get("failures") or []
    if current.status == "needs_revision" and "length_out_of_range" in initial_failures:
        raise MasterApprovalError(
            f"Master story not approved after 0 revision(s): "
            f"{', '.join(map(str, initial_failures)) or 'quality gates failed'}",
            {
                "reasons": list(initial_failures),
                "attempts": [{
                    "version_id": current.id,
                    "failures": initial_failures,
                    "quality_gates": initial_quality_gates,
                }],
                "target_minutes": target_minutes,
                "terminal": True,
            },
        )

    outcome = await RetryAuditPolicy(db, case_id=case.id).run_with_audit(
        produce, audit, limit, ExhaustedAction.STOP, stage="master_story")
    if outcome.status == "stopped":
        reasons = ", ".join(map(str, outcome.reasons)) or "quality gates failed"
        detail = {
            "reasons": list(outcome.reasons),
            "attempts": rejected,
            "target_minutes": target_minutes,
            "terminal": bool(rejected and "length_out_of_range" in rejected[-1].get("failures", [])),
        }
        raise MasterApprovalError(
            f"Master story not approved after {max(0, outcome.attempts - 1)} revision(s): {reasons}",
            detail,
        )
    return current, rejected


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
    result = json.loads(job.result_json or "{}")
    call_progress = result.get("call_progress")
    if isinstance(call_progress, dict) and call_progress.get("active"):
        started_at = call_progress.get("started_at")
        if isinstance(started_at, str):
            try:
                started = ensure_utc(datetime.fromisoformat(started_at))
                call_progress = {
                    **call_progress,
                    "elapsed_seconds": round(
                        max((utc_now() - started).total_seconds(), 0.0), 1
                    ),
                }
                result["call_progress"] = call_progress
            except (TypeError, ValueError):
                pass
    failure = result.get("failure") or {}
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
        "result": result, "error": job.error,
        "next_action": failure.get("next_action"),
        "resume_allowed": failure.get("resume_allowed", job.status in ("cancelled", "partial", "interrupted")),
        "resume_disabled_reason": failure.get("resume_disabled_reason"),
        "created_at": job.created_at, "updated_at": job.updated_at,
        "completed_at": job.completed_at,
    }


def plain_failure(error: BaseException | str) -> str:
    text = str(error)
    return re.sub(r"^(?:[A-Za-z_][A-Za-z0-9_]*Error|RuntimeError):\s*", "", text).strip()


def failure_details(message: str, error_code: str | None = None) -> dict:
    if error_code in {"search_no_results", "search_rate_limited"}:
        rate_limited = error_code == "search_rate_limited"
        return {
            "resume_allowed": False,
            "resume_disabled_reason": (
                "Resume is disabled until the Brave Search response or rate-limit condition changes."
            ),
            "next_action": (
                "Wait for the Brave Search rate-limit window to change, then start a new research run."
                if rate_limited else
                "Check Brave Search availability and query results, then start a new research run."
            ),
        }
    if error_code == "no_relevant_sources" or message.startswith("No relevant sources found"):
        return {
            "resume_allowed": False,
            "resume_disabled_reason": "Resume is disabled until the case title or research details change.",
            "next_action": "Add a more specific title, aliases, location or incident date, then start a new run.",
        }
    return {
        "resume_allowed": True,
        "resume_disabled_reason": None,
        "next_action": "Review the failed stage and its inputs, then resume if the provider or service is available again.",
    }


def tts_budget_left(paid: dict[str, int], cap: int) -> bool:
    """False when the job's paid narration characters reached the ceiling."""
    return not cap or sum(int(v or 0) for v in paid.values()) < cap


def host_active() -> bool:
    """The host stages (director, writer, critic per language) only run when
    the host can appear: its avatar videos are enabled. Planning a host
    nobody renders costs model calls and changes nothing in the film."""
    return bool(ai_config.host.enabled and ai_config.avatar.enabled)


def plan_stages(languages: list[str], from_zero: bool = False) -> list[dict]:
    host = host_active()
    names = (["research", "master_story"] if from_zero else []) + [
        "master_approval", "blueprint", "audio_plan"] + [f"spoken:{l}" for l in languages] + (
        ["host_plan"] if host else []) + (
        ["chapters"] if ai_config.chapters.enabled else []) + (
        ["naming"] if ai_config.case_naming.generate_in_pipeline else []) + [
        "film_length", "visual_needs", "visual_research", "visual_check", "visual_plan",
        "visual_gaps"]
    for l in languages:
        names += ([f"host:{l}"] if host else []) + [
            f"performance:{l}", f"voice:{l}", f"production:{l}", f"critique:{l}",
            f"visual_audit:{l}", f"render:{l}"]
    # Shorts are real post-film stages.  They remain pending while the film
    # runs and are claimed only after the documentary job is finished.
    names += ["short_selection"]
    names += [f"short_script:{l}" for l in languages]
    names += [f"short_render:{l}" for l in languages]
    return [{"name": n, "status": "pending", "detail": None} for n in names]


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------


class DocumentaryPipeline(StageRunner):
    """One job's run: the stage order lives here, the bookkeeping in StageRunner."""

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

        async def master_approval():
            # nothing goes into production unapproved: a master its own
            # critics did not pass is revised with their reasons (each
            # revision judged again), at most max_redos times
            target_minutes = float(job.target_minutes or (
                (job.pilot_seconds or ai_config.documentary.pilot_seconds) / 60
                if pilot else ai_config.story.default_target_minutes
            ))
            v, history = await approve_master(
                db, case, db.get(StoryVersion, job.master_version_id),
                target_minutes=target_minutes,
                words_per_minute=ai_config.words_per_minute_for(
                    ai_config.multilingual.canonical_language
                ),
            )
            if v.id != job.master_version_id:
                job.master_version_id = v.id
                db.commit()
            return {"master_version_id": v.id,
                    "verdict": "approved manually" if v.approved_by else "approved",
                    "manually_approved": bool(v.approved_by),
                    "approved_by": v.approved_by,
                    "redos": len(history), "history": history}

        await self._stage("master_approval", master_approval)
        master = db.get(StoryVersion, job.master_version_id)

        async def blueprint():
            row = latest_blueprint(db, master.id)
            if row and row.status != "invalid" and (
                    not row.story_text_hash or row.story_text_hash == master.text_hash):
                state["bp"] = row
                return {"blueprint_id": row.id, "reused": True}
            # an invalid blueprint is made again (validated each time)
            tries = []
            for _ in range(ai_config.documentary.max_redos + 1):
                row = await get_stage_agent("blueprint").create(db, case, master)
                tries.append(row.status)
                if row.status != "invalid":
                    break
            if row.status == "invalid":
                raise RuntimeError(f"The editorial blueprint stayed invalid after {len(tries)} "
                                   "attempts; see the Blueprint tab.")
            state["bp"] = row
            return {"blueprint_id": row.id, "status": row.status, "attempts": len(tries)}

        await self._stage("blueprint", blueprint)
        state.setdefault("bp", latest_blueprint(db, master.id))
        bp_row = state["bp"]
        bp = json.loads(bp_row.blueprint_json or "{}")

        async def audio_plan():
            row = latest_audio_plan(db, bp_row.id)
            if row:
                return {"audio_plan_id": row.id, "reused": True}
            row = await get_stage_agent("audio_plan").create(db, case, bp_row)
            out = {"audio_plan_id": row.id, "status": row.status}
            if row.status == "invalid":
                out["degraded"] = "audio plan invalid — the film runs without music/sound direction"
            return out

        await self._stage("audio_plan", audio_plan)
        ap_row = latest_audio_plan(db, bp_row.id)
        ap = json.loads(ap_row.plan_json) if ap_row else None
        focus_beats = pilot_beats(bp, pilot_seconds) if pilot else None

        # ---- phase B: spoken versions (all languages) + visual needs ---------
        spoken: dict[str, StoryVersion] = {}

        async def spoken_branch(lang: str):
            async def make_spoken():
                current = latest_spoken(db, master, lang, bp_row.id)
                if pilot and current is not None:
                    saved = json.loads(current.narrative_structure or "{}")
                    same_pilot = (
                        float(saved.get("pilot_seconds") or 0) == float(pilot_seconds)
                        and set(saved.get("pilot_beat_ids") or []) == set(focus_beats or [])
                    )
                    if not same_pilot:
                        current = None
                history = []

                async def produce(feedback):
                    nonlocal current
                    if current is None or feedback["reasons"]:
                        current = await get_stage_agent("spoken").create(
                            db, case, master, lang, beat_ids=set(focus_beats or []),
                            pilot_seconds=pilot_seconds if pilot else None)
                    return current

                async def audit(version):
                    gates = json.loads(version.critic_notes or "{}").get(
                        "quality_gates") or {}
                    failures = gates.get("failures", [])
                    if version.status == "needs_revision":
                        history.append({"version_id": version.id, "failures": failures})
                    return {"approved": version.status != "needs_revision",
                            "reasons": failures, "failed_item_ids": [lang]}

                outcome = await RetryAuditPolicy(db, case_id=case.id, language=lang).run_with_audit(
                    produce, audit, ai_config.documentary.max_redos,
                    ai_config.production_policies.retry_policy.spoken_versions,
                    stage="spoken_versions")
                if outcome.status != "approved":
                    raise SpokenRejected(
                        f"spoken version not approved after {outcome.attempts - 1} redo(s): "
                        + ", ".join(map(str, outcome.reasons)))
                v = current
                gates = json.loads(v.critic_notes or "{}").get("quality_gates") or {}
                return {"version_id": v.id, "status": v.status, "verdict": "approved",
                        "redos": len(history), "rejected_before": history,
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

            row = latest_visual_plan(db, bp_row.id)
            tries = 0
            while row is None or row.status == "invalid":
                if tries > ai_config.documentary.max_redos:
                    raise RuntimeError(f"Visual needs stayed invalid after {tries} attempts.")
                row = await get_stage_agent("visual_planner").create(db, case, bp_row)
                tries += 1
            state["vp"] = row
            return {"visual_plan_id": row.id, "status": row.status, "attempts": tries}

        async def host_plan():
            from app.documentary.host import latest_host_plan

            if not host_active():
                return {"skipped": True, "reason": "host disabled"}
            row = latest_host_plan(db, bp_row.id)
            if row is None:
                row = await get_stage_agent("host").create_plan(db, case, bp_row, master)
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
        if "host_plan" not in state and host_active():
            from app.documentary.host import latest_host_plan

            state["host_plan"] = latest_host_plan(db, bp_row.id)
        ok_langs = [l for l in langs if l in spoken]
        if not ok_langs:
            raise RuntimeError("No language could be told: " + "; ".join(
                f"{l}: {e}" for l, e in self.lang_errors.items()))

        async def chapters():
            # chapter titles, the film title and the timeline labels, in
            # every language, each approved by the chapter auditor
            from app.documentary.chapters import latest_chapter_plan, plan_covers

            if not ai_config.chapters.enabled:
                return {"skipped": True, "reason": "chapters disabled"}
            row = latest_chapter_plan(db, bp_row.id)
            reused = plan_covers(row, ok_langs) and row.status != "no_texts"
            if not reused:
                row = await get_stage_agent("chapters").create(db, case, bp_row, master, ok_langs, spoken)
            plan = json.loads(row.plan_json or "{}")
            audit = json.loads(row.audit_json or "{}")
            left = [f"{x['key']} ({', '.join(x.get('languages') or [x.get('language', '?')])}): "
                    f"{x.get('reason') or ''}"[:160]
                    for x in audit.get("left_out") or []]
            out = {"chapter_plan_id": row.id, "status": row.status, "reused": reused,
                   "chapters": len(plan.get("chapters") or []),
                   "timeline_events": len(plan.get("events") or []), "left_out": left}
            if row.status == "no_texts":
                out["degraded"] = ("no card texts (writer/auditor failed: "
                                   f"{audit.get('error')}) — cards show only numbers and dates")
            elif left:
                out["degraded"] = (f"{len(left)} card text(s) still rejected after the redos "
                                   "were left out")
            return out

        await self._optional("chapters", chapters)

        async def naming():
            # title candidates per language (stored data only, no search); a
            # person approves one in the Naming tab
            if not ai_config.case_naming.generate_in_pipeline:
                return {"skipped": True, "reason": "disabled"}
            from app.naming.agent import default_embedder, generate_case_titles
            from app.providers.generation import get_generation_provider

            res = await generate_case_titles(db, case, get_generation_provider(),
                                             default_embedder(), list(ok_langs))
            short = {l: r["short_by"] for l, r in res["languages"].items() if r["short_by"]}
            out = {"title_family_id": res["title_family_id"],
                   "eligible": {l: r["eligible"] for l, r in res["languages"].items()},
                   "approve_in": "Naming tab"}
            if short:
                out["degraded"] = f"fewer than {ai_config.case_naming.candidates_per_language} " \
                                  f"title candidates: {short}"
            return out

        await self._optional("naming", naming)

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

                    queries = research_queries(requirements, focus_beats)
                    stats = await get_stage_agent("visual_research").run(db, case, queries)
                    return {"queries": len(queries), **stats}

                await self._stage("visual_research", visual_research)

                async def visual_check():
                    if planned:
                        return {"skipped": True}
                    out = await verify_candidates(db, case, bp, requirements, focus_beats,
                                                  job.render_profile)
                    if out.get("errors"):
                        out["degraded"] = (f"{out['errors']} vision checks failed — "
                                           "those candidates stay unverified")
                    return out

                await self._stage("visual_check", visual_check)

                async def visual_plan():
                    from app.documentary.visuals.generated import materialize

                    row = db.get(VisualPlan, vp_row.id)
                    if row.status != "planned" or job.refresh_visuals:
                        await get_stage_agent("visual_director").create(db, case, row, bp_row, ap_row,
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

                    out = await fill_visual_gaps(db, case, db.get(VisualPlan, vp_row.id),
                                                 bp_row, ap_row, profile=job.render_profile,
                                                 beat_ids=focus_beats)
                    if out.get("error"):
                        out["degraded"] = "production search failed — " + out["error"]
                    return out

                await self._stage("visual_gaps", visual_gaps)
                visual_state["row"] = db.get(VisualPlan, vp_row.id)
            except BaseException as e:
                visual_state["error"] = e
                raise
            finally:
                visual_ready.set()

        renders = self.result.setdefault("renders", {})

        class _Shared:  # what the shared stages produced, read by every language chain
            pass

        shared = _Shared()
        shared.case, shared.bp, shared.focus_beats, shared.state = case, bp, focus_beats, state
        shared.pilot, shared.pilot_seconds = pilot, pilot_seconds
        shared.vp_row, shared.visual_ready = vp_row, visual_ready
        shared.visual_state, shared.renders = visual_state, renders
        # Music selection reads usage history written by preceding language
        # renders. Preserve the pre-incremental completion order explicitly.
        shared.languages = [lang for lang in ("fa", "ar", "en", "de") if lang in ok_langs]
        shared.music_turns = [asyncio.Event() for _ in shared.languages]
        if shared.music_turns:
            shared.music_turns[0].set()
        async def language_chain(lang: str):
            from app.documentary.pipeline.language import LanguageChain

            await LanguageChain(self, lang, spoken[lang], shared).run()

        async def guarded(lang: str):
            # Do not let the language semaphore be occupied by a task waiting
            # for the ordered music boundary; otherwise an arbitrary pair of
            # later languages can starve the first turn.
            await shared.music_turns[shared.languages.index(lang)].wait()
            async with slot("languages"):
                try:
                    await language_chain(lang)
                except JobCancelled:
                    raise
                except Exception as e:
                    self.lang_errors[lang] = f"{type(e).__name__}: {e}"[:400]
                    self._skip_language(lang, str(e))

        await self._parallel([visual_chain()] + [guarded(l) for l in shared.languages], shared_from=0,
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
            # A cap refusal is an intentional, retryable outcome rather than
            # a provider failure. Keep the persisted reason, but mark this
            # optional stage skipped so a disabled/over-cap host cannot make
            # the otherwise valid film look failed.
            if isinstance(e, BudgetStopped):
                entry = self._entry(name)
                entry["status"] = "skipped"
                entry["detail"] = {"budget_stopped": True, "reason": e.reason}
                entry["error_type"] = type(e).__name__
                self._save()
            from app.documentary.pipeline.base import record_warning

            record_warning(self.result, name, f"{type(e).__name__}: {e}")
            self._save()
            return None


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
        return {"skipped": True, "reason": f"case already has {facts} facts",
                "video_research": await _video_research(db, case)}
    job = (db.query(ResearchJob).filter(ResearchJob.case_id == case.id,
                                        ResearchJob.job_type == "research",
                                        ResearchJob.status.in_(("queued", "running")))
           .order_by(ResearchJob.id.desc()).first())
    if job is None and follow_up:
        from app.lifecycle.followups import start_update_research

        job = await start_update_research(db, case, doc_job.follow_up_id)
    if job is None:
        objective = None
        if doc_job is not None:
            try:
                objective = json.loads(doc_job.result_json or "{}").get("stage_control_prompt")
            except (TypeError, ValueError):
                pass
        job = await research_jobs.start_research_job(db, case, objective=objective)
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
    if not facts:
        raise RuntimeError(f"Research job {job.id} completed but no facts were saved for "
                           "the case — check the research job before writing a story.")
    vr = await _video_research(db, case)
    out = {"research_job_id": job.id, "facts": facts, "sources": job.sources_accepted,
           "video_research": vr}
    if vr.get("error"):
        out["degraded"] = "video research failed — the story uses the web evidence only"
    return out


async def _video_research(db: Session, case: Case) -> dict:
    """The transcript layer (captions -> claims -> promoted facts): part of
    a from-zero film, never fatal — the film goes on with the web evidence."""
    if not ai_config.documentary.video_research:
        return {"skipped": True, "reason": "disabled"}
    from app.db.models import Source
    from app.services import research_jobs
    from app.services.video_research import run_video_research

    have = db.query(Source).filter(Source.case_id == case.id,
                                   Source.source_type == "youtube_video").count()
    if have:
        return {"skipped": True, "reason": f"case already has {have} video sources"}
    try:
        job = research_jobs.create_job(db, job_type="video_research", case_id=case.id,
                                       input_data={"case_title": case.canonical_title})
        out = await run_video_research(db, case, job)
        return {"job_id": job.id, "status": job.status, **{k: v for k, v in (out or {}).items()
                                                           if isinstance(v, (int, float, str))}}
    except Exception as e:  # noqa: BLE001
        log.warning("video research for case %s failed: %s", case.id, e)
        return {"error": f"{type(e).__name__}: {e}"[:300]}


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
    instruction = None
    try:
        instruction = json.loads(job.result_json or "{}").get("stage_control_prompt")
    except (TypeError, ValueError):
        pass
    story = await StoryPipeline(roles=MASTER_ROLES).run(
        db=db, case=case, target_minutes=minutes, language=canonical,
        tone=defaults.tone, iterations=defaults.iterations, kind="master",
        words_per_minute=ai_config.words_per_minute_for(canonical), follow_up=follow_up,
        instruction=instruction)
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
    # a relevant piece of a video brings its sibling pieces: the whole
    # video gets described (each piece by the video auditor), so every
    # part of it can serve the sentence it belongs to
    from app.documentary.visuals.pieces import piece_info

    parents = {piece_info(a).get("parent") for a in todo} - {None}
    if parents:
        for a in assets:
            if (a not in todo and a.verification_status == "unverified"
                    and piece_info(a).get("parent") in parents):
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
    effective_target_minutes = (float(pilot_seconds) / 60.0
                                if mode == "pilot" and pilot_seconds else target_minutes)
    job = DocumentaryJob(
        case_id=case.id, master_version_id=master.id if master else None, mode=mode,
        refresh_visuals=refresh_visuals, from_zero=from_zero,
        target_minutes=effective_target_minutes, batch_id=batch_id,
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


_FIXED_CASE_STATES = ("archived", "rejected", "published")


def status_for_result(result: dict) -> str:
    """Map a finished pipeline result to the persisted job status."""
    if result.get("errors"):
        return "partial"
    if result.get("warnings"):
        return "completed_warnings"
    return "completed"


def _set_case_state(db: Session, case_id: int, state: str) -> None:
    """The case's workflow state follows production: producing while a
    film is made, rendered when one exists, story_ready after a failure."""
    case = db.get(Case, case_id)
    if case is not None and case.status not in _FIXED_CASE_STATES:
        case.status = state
        db.commit()


async def run_job(job_id: int, session_factory=None):
    """Runs one documentary. Waits in "queued" until one of the
    concurrency.jobs slots is free (several documentaries in parallel)."""
    from app.db.base import SessionLocal

    db = (session_factory or SessionLocal)()
    try:
        async with slot("jobs"):
            job = db.get(DocumentaryJob, job_id)
            if job is None:
                raise ValueError(f"documentary job {job_id} not found")
            db.refresh(job)
            if job.status in ("cancelling", "cancelled"):
                job.status = "cancelled"
                job.completed_at = utc_now()
                db.commit()
                return
            job.status = "running"
            job.error = None
            db.commit()
            _set_case_state(db, job.case_id, "producing")
            scope_edit, execution_scope = _stage_control_scope(db, job.id)
            if scope_edit is not None and execution_scope is not None:
                result = json.loads(job.result_json or "{}")
                result["stage_control_prompt"] = scope_edit.instruction_text
                job.result_json = json.dumps(result, ensure_ascii=False)
                db.commit()
            before_snapshot = _artifact_snapshot(db, job.id) if execution_scope else None
            pipeline = DocumentaryPipeline(db, job, execution_scope=execution_scope)
            pipeline_error = None
            result = {}
            try:
                result = await pipeline.run() or {}
                if ai_config.artifacts.shadow_mode:
                    from app.services.artifacts import ArtifactRegistry

                    ArtifactRegistry(db).reconcile(
                        job.case_id, stage_job_id=job.id, repair=True)
                if (scope_edit is not None and execution_scope is not None
                        and scope_edit.status == "applied"):
                    outcome = _finish_stage_control_scope(
                        db, job, scope_edit, execution_scope, before_snapshot or {},
                        pipeline.executed_stage_names, None)
                    if outcome["changed_outside_scope"]:
                        pipeline_error = RuntimeError(
                            "stage-control execution escaped preview scope: "
                            + ", ".join(outcome["changed_outside_scope"]))
                # Equivalent to completed_with_warnings, within the existing
                # DocumentaryJob.status String(20) column.
                if pipeline_error is not None:
                    raise pipeline_error
                job.status = status_for_result(result or {})
                # The long film is complete at this boundary. Shorts are
                # post-film work and must not hold the film's progress at a
                # partial value while their isolated worker runs.
                job.progress = 1.0
                _set_case_state(db, job.case_id,
                                "rendered" if (result or {}).get("renders") else "story_ready")
                if job.status == "partial":
                    job.error = "; ".join(f"{l}: {e}" for l, e in result["errors"].items())[:1000]
            except JobCancelled:
                if (scope_edit is not None and execution_scope is not None
                        and scope_edit.status == "applied"):
                    db.rollback()
                    job = db.get(DocumentaryJob, job_id)
                    assert job is not None
                    _finish_stage_control_scope(
                        db, job, scope_edit, execution_scope, before_snapshot or {},
                        getattr(pipeline, "executed_stage_names", []), "cancelled")
                job.status = "cancelled"
                _set_case_state(db, job.case_id, "story_ready")
            except BudgetStopped as e:
                db.rollback()
                job = db.get(DocumentaryJob, job_id)
                assert job is not None
                if (scope_edit is not None and execution_scope is not None
                        and scope_edit.status == "applied"):
                    _finish_stage_control_scope(
                        db, job, scope_edit, execution_scope, before_snapshot or {},
                        getattr(pipeline, "executed_stage_names", []), e.reason)
                    job = db.get(DocumentaryJob, job_id)
                    assert job is not None
                case = db.get(Case, job.case_id)
                if case is not None:
                    case.status = "budget stopped"
                    case.budget_stop_reason = e.reason[:1000]
                job.status = "failed"
                job.error = plain_failure(e.reason)[:1000]
                result = json.loads(job.result_json or "{}")
                result["budget_stopped"] = {"stage": e.stage, "reason": e.reason}
                result["failure"] = {**failure_details(job.error, "budget_stopped"), "reason": job.error}
                job.result_json = json.dumps(result, ensure_ascii=False)
            except Exception as e:  # recorded on the job, visible in the UI
                log.error("documentary job %s failed: %s", job_id, traceback.format_exc())
                db.rollback()
                job = db.get(DocumentaryJob, job_id)
                assert job is not None
                if (scope_edit is not None and execution_scope is not None
                        and scope_edit.status == "applied"):
                    _finish_stage_control_scope(
                        db, job, scope_edit, execution_scope, before_snapshot or {},
                        getattr(pipeline, "executed_stage_names", []), str(e))
                    job = db.get(DocumentaryJob, job_id)
                    assert job is not None
                _set_case_state(db, job.case_id, "story_ready")
                job.status = "failed"
                job.error = plain_failure(e)[:1000]
                result = json.loads(job.result_json or "{}")
                result["failure"] = {**failure_details(job.error), "reason": job.error}
                job.result_json = json.dumps(result, ensure_ascii=False)
            from app.services.cost_model import estimate_vs_actual_report

            # Persist the per-case estimate-vs-actual ledger for this run
            # before the UI sees the terminal job state.
            result = result or json.loads(job.result_json or "{}")
            result["cost_report"] = estimate_vs_actual_report(db, job)
            job.result_json = json.dumps(result, ensure_ascii=False, default=str)
            job.stage = None
            job.completed_at = utc_now()
            job.updated_at = utc_now()
            db.commit()
            # Post-film Shorts are durable queue work with their own error
            # boundary.  A failure here must never alter the completed film.
            if job.status in ("completed", "completed_warnings"):
                try:
                    from app.shortform.pipeline import enqueue_for_finished_job
                    enqueue_for_finished_job(db, job.id)
                except Exception:  # noqa: BLE001
                    log.exception("could not enqueue isolated Shorts job %s", job.id)
    finally:
        db.close()
        _running.pop(job_id, None)


def _stage_control_scope(db: Session, job_id: int) -> tuple[EditLog | None, dict | None]:
    """Load the one applied preview that authorizes this queued run."""
    for edit in db.query(EditLog).filter(EditLog.status == "applied").order_by(EditLog.id.desc()):
        try:
            payload = json.loads(edit.scope_preview_json or "{}")
            result = json.loads(edit.result_json or "{}")
        except (TypeError, ValueError):
            continue
        if payload.get("job_id") == job_id and result.get("status") == "queued":
            payload["prompt"] = edit.instruction_text
            return edit, payload
    return None, None


def _artifact_snapshot(db: Session, job_id: int) -> dict[str, dict]:
    return {
        row.id: {name: getattr(row, name) for name in (
            "input_hash", "output_hash", "output_ref", "status", "version", "scope_json")}
        for row in db.query(Artifact).filter(Artifact.stage_job_id == job_id).all()
    }


def _finish_stage_control_scope(db: Session, job: DocumentaryJob, edit: EditLog,
                                payload: dict, before: dict, executed_stages: list[str],
                                error: str | None) -> dict:
    after = _artifact_snapshot(db, job.id)
    previewed = set(payload.get("artifact_ids", []))
    changed = {artifact_id for artifact_id in set(before) | set(after)
               if before.get(artifact_id) != after.get(artifact_id)}
    outside = sorted(changed - previewed)
    # "executed" describes the artifacts belonging to stages that actually
    # ran, not only artifacts whose hash happened to change.  This makes the
    # audit useful for redo correctness and keeps it equal to the previewed
    # scope for a fully scoped run.
    executed_stage_names = set(executed_stages)
    executed = set(changed & previewed)
    for artifact_id, snapshot in after.items():
        try:
            detail = json.loads(snapshot.get("scope_json") or "{}")
        except (TypeError, ValueError):
            detail = {}
        stage = detail.get("stage")
        language = detail.get("language")
        name = f"{stage}:{language}" if language else stage
        if name in executed_stage_names and artifact_id in previewed:
            executed.add(artifact_id)
    inside = sorted(executed)
    result = json.loads(edit.result_json or "{}")
    result.update({
        "status": "failed" if error or outside else "completed",
        "previewed_artifact_ids": sorted(previewed),
        "executed_artifact_ids": inside,
        "executed_stage_names": sorted(executed_stage_names),
        "changed_outside_scope": outside,
        **({"error": error} if error else {}),
    })
    edit.result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
    edit.status = "failed" if error or outside else "completed"
    reason = ("changed artifacts outside preview scope" if outside else
              error or "stage-control scope completed")
    record_decision(
        db,
        PolicyDecision(policy="stage_control",
                       decision="blocked" if outside else ("failed" if error else "applied"),
                       reason=reason, rule_id="STAGE_CONTROL_SCOPE_CHECKED",
                       severity="error" if outside or error else "info",
                       recoverable=True, actions=("review",),
                       context={"preview_id": edit.id,
                                "previewed_artifact_ids": sorted(previewed),
                                "executed_artifact_ids": inside,
                                "changed_outside_scope": outside}),
        case_id=job.case_id, production_id=job.id,
    )
    db.commit()
    return result


def launch(job_id: int) -> None:
    """Start a queued job. With an external worker (TRUECRIME_JOB_RUNNER=external) the API only queues it:
    the worker picks it up, so restarting the API never stops a running film."""
    from app.core.config import settings

    if settings.job_runner == "external":
        return
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
