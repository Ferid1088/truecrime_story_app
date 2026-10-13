"""Deterministic provider worker for the disposable stage-control E2E stack."""

from __future__ import annotations

import asyncio
import time

from app.db.base import SessionLocal
from app.db.models import Artifact, DocumentaryJob
from app.documentary import jobs as documentary_jobs
from app.documentary.pipeline.base import StageRunner


class E2EMockPipeline(StageRunner):
    """Run only the scope authorized by StageControlService with no providers."""

    async def _touch(self, stage: str, language: str):
        row = (self.db.query(Artifact)
               .filter_by(stage_job_id=self.job.id,
                          type="spoken_version" if stage == "spoken" else "voice_takes",
                          language=language).one())
        row.output_hash = f"e2e-{stage}-{language}-updated"
        return {"stage": stage, "language": language, "mock_provider_calls": 1}

    async def run(self):
        for language in ("en", "de", "fa", "ar"):
            await self._stage(f"spoken:{language}",
                              lambda language=language: self._touch("spoken", language))
            await self._stage(f"voice:{language}",
                              lambda language=language: self._touch("voice", language))
        return {"mock_providers": True}


def main() -> None:
    documentary_jobs.DocumentaryPipeline = E2EMockPipeline
    import app.services.artifact_shadow as artifact_shadow
    from app.services.artifacts import ArtifactRegistry

    artifact_shadow.register_stage_outputs = lambda *args, **kwargs: None
    ArtifactRegistry.reconcile = lambda *args, **kwargs: None
    while True:
        with SessionLocal() as db:
            job = (db.query(DocumentaryJob)
                   .filter(DocumentaryJob.status == "queued")
                   .order_by(DocumentaryJob.id)
                   .first())
            job_id = job.id if job else None
        if job_id is not None:
            asyncio.run(documentary_jobs.run_job(job_id))
        else:
            time.sleep(0.1)


if __name__ == "__main__":
    main()
