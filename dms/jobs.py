from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from models import Job

router = APIRouter(prefix="/jobs", tags=["jobs"])

STATUSES = {"pending", "sent", "failed"}


def _serialize(j: Job) -> dict:
    return {
        "id": j.id,
        "job_type": j.job_type,
        "status": j.status,
        "payload": j.payload,
        "attempt_count": j.attempt_count,
    }


@router.get("")
def list_jobs(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Job)
    if status is not None:
        q = q.filter(Job.status == status)
    return [_serialize(j) for j in q.order_by(Job.created_at).all()]


class JobUpdate(BaseModel):
    status: str
    attempt_count: int | None = None


@router.patch("/{job_id}")
def update_job(job_id: int, payload: JobUpdate, db: Session = Depends(get_db)):
    if payload.status not in STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status: {payload.status}")
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Not found")
    job.status = payload.status
    if payload.attempt_count is not None:
        job.attempt_count = payload.attempt_count
    db.commit()
    return _serialize(job)
