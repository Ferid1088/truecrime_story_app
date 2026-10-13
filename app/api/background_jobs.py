from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import BackgroundJob
from app.services import background_jobs

router = APIRouter(tags=["jobs"])


@router.get("/api/jobs/{job_id}")
def get_background_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(BackgroundJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Background job not found")
    return background_jobs.job_dict(job)


@router.post("/api/jobs/{job_id}/retry")
async def retry_background_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(BackgroundJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Background job not found")
    try:
        job = background_jobs.retry(db, job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return background_jobs.job_dict(job)
