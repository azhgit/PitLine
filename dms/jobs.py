from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import engine, get_db
from models import Job

router = APIRouter(prefix="/jobs", tags=["jobs"])

STATUSES = {"pending", "claimed", "sent", "failed"}
# SQLite (test suite only - see conftest.py) has no row-level locking at
# all, so SKIP LOCKED isn't a dialect it supports; harmless to skip there
# since SQLite is single-writer anyway and can't hit the race this guards.
_SUPPORTS_SKIP_LOCKED = engine.dialect.name != "sqlite"


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


@router.post("/claim")
def claim_job(db: Session = Depends(get_db)):
    """Atomically claims and returns one pending job, or null if none are
    pending. FOR UPDATE SKIP LOCKED (not a plain SELECT then a separate
    PATCH) is what actually prevents two worker replicas from both
    claiming the same row when they poll at the same instant - see
    ADR-0003. The worker (agent/worker.py) calls this in a loop instead of
    GET /jobs?status=pending.
    """
    q = db.query(Job).filter(Job.status == "pending").order_by(Job.created_at)
    if _SUPPORTS_SKIP_LOCKED:
        q = q.with_for_update(skip_locked=True)
    job = q.first()
    if job is None:
        return None
    job.status = "claimed"
    db.commit()
    return _serialize(job)


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
