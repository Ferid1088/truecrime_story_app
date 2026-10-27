"""Renders one closing clip: the host's voice (ElevenLabs, the host voice style, the same take
checks as every host appearance) for the fixed sentences, then the HeyGen avatar video of an
approved look speaking that audio. Costs go into the case ledger; the estimate is printed before
anything is paid."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.documentary import audio as A
from app.documentary import studio as ST
from app.documentary.heygen_cost import MODEL, STAGE
from app.providers.avatar import get_avatar_provider
from app.providers.avatar.base import AvatarProviderError
from app.services.budget import BudgetService
from app.services.cost_model import load_pricing
from app.shortform.closing_library import CostEstimate, LookSpec, RenderedClip
from app.utils import utc_now


class ClosingRenderError(RuntimeError):
    pass


class HeyGenClosingRenderer:
    """voice -> upload -> HeyGen job -> poll -> download, for one look."""

    def __init__(self, sleep=asyncio.sleep):
        self.sleep = sleep

    # -- the estimate, before any paid call -----------------------------------------------------
    def estimate(self, language: str, sentences: list[str]) -> CostEstimate:
        pricing = load_pricing()
        characters = sum(len(s) for s in sentences)
        words = sum(len(s.split()) for s in sentences)
        seconds = round(words / ai_config.shorts_host_led.closing_words_per_minute[language] * 60, 1)
        voice = characters * float(pricing["tts"]["eleven_v4"]["eur_per_1m_characters"]) / 1e6
        avatar = seconds / 60 * float(pricing["heygen"]["eur_per_minute"])
        return CostEstimate(characters=characters, voice_eur=round(voice, 4), seconds=seconds,
                            avatar_eur=round(avatar, 4))

    # -- the render -----------------------------------------------------------------------------
    async def render(self, db: Session, language: str, sentences: list[str], look: LookSpec,
                     dest: Path, case_id: int) -> RenderedClip:
        budget = BudgetService(db)
        budget.check(case_id, STAGE)               # stops cleanly before a paid call at the cap
        audio, tts_eur = await self._voice(db, language, sentences, case_id)
        budget.check(case_id, STAGE)
        cp = ST.channel_profile(language)
        prov = get_avatar_provider(key_env=cp["heygen_key_env"])
        resolved = ST.resolve(language)
        composited = resolved["profile"].background_mode == "provider_composited"
        audio_id = await prov.upload_asset(audio, "audio/mpeg")
        background_id = None
        if composited:
            _preset, asset = self._preset(resolved)
            reg = ST.load_registry()
            background_id = await prov.upload_asset(ST.asset_path(reg, asset), "image/png")
        key = f"tc-closing-{hashlib.sha256(audio.read_bytes()).hexdigest()[:16]}-{look.heygen_look_id}"
        job = await prov.create_video(
            look.heygen_look_id, audio_id, key,
            output_format="mp4" if composited else ai_config.avatar.output_format,
            resolution=ai_config.avatar.resolution, background_asset_id=background_id,
            title=f"{ST.channel_profile(language)['channel_name']} closing {look.position}",
            **({"engine": ai_config.avatar.engine} if ai_config.avatar.engine else {}),
            **({"fit": ai_config.avatar.fit} if composited else {}))
        done = await self._wait(prov, job.job_id)
        await prov.download(done.video_url, dest)
        seconds = done.duration or A.probe_duration(dest)
        price = float(load_pricing()["heygen"]["eur_per_minute"])
        avatar_eur = round(seconds / 60 * price, 6)
        budget.record(case_id=case_id, stage=STAGE, model=MODEL, units=round(seconds, 3),
                      cost_eur=avatar_eur, estimated=True, request_id=f"heygen:{job.job_id}",
                      cost_source="estimate")
        return RenderedClip(duration_seconds=round(float(seconds), 3),
                            cost_eur=round(tts_eur + avatar_eur, 6))

    @staticmethod
    def _preset(resolved: dict):
        name = ai_config.studio.framing_by_position.get("final", "HOST_MEDIUM")
        return resolved["presets"][name if name in resolved["presets"] else "HOST_MEDIUM"]

    async def _voice(self, db: Session, language: str, sentences: list[str],
                     case_id: int) -> tuple[Path, float]:
        from app.documentary.pronunciation import PronunciationEditor
        from app.documentary.voice_render import VoiceRenderer

        vcfg = ai_config.voice.languages[language]
        settings = ai_config.voice.styles[ai_config.host.voice_style].model_dump()
        renderer = VoiceRenderer(db=db, budget_case_id=case_id, budget_stage=f"tts:closing:{language}",
                                 arbiter=PronunciationEditor())
        block = {"block_id": f"closing-{language}", "text": " ".join(sentences), "beats": [],
                 "sentences": [{"speech": s, "display": s, "tts": s, "level": None, "risky": []}
                               for s in sentences]}
        takes = Path(tempfile.mkdtemp(prefix="closing_takes_"))
        result = await renderer.speak_block(block, language, vcfg, settings, ai_config.voice.seed,
                                            takes, synonyms=False)
        take = result["take"]
        out = takes / "closing.mp3"
        shutil.copyfile(take["raw_path"], out)
        cost = float(take.get("character_cost") or 0) * float(
            load_pricing()["tts"]["eleven_v4"]["eur_per_1m_characters"]) / 1e6
        return out, cost

    async def _wait(self, prov, job_id: str):
        deadline = utc_now().timestamp() + ai_config.avatar.max_poll_minutes * 60
        while True:
            job = await prov.get_video(job_id)
            if job.status == "completed" and job.video_url:
                return job
            if job.status == "failed":
                raise AvatarProviderError("job_failed", job.failure_message or "avatar job failed")
            if utc_now().timestamp() >= deadline:
                raise AvatarProviderError("timeout", f"job {job_id} still {job.status}")
            await self.sleep(ai_config.avatar.poll_interval_s)
