from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from events import broadcaster
from models import Handoff, Job

router = APIRouter(prefix="/handoffs", tags=["handoffs"])


class HandoffCreate(BaseModel):
    customer_phone: str | None = None  # None = caller couldn't be verified
    reason: str
    summary: dict | None = None


def _serialize(h: Handoff) -> dict:
    return {
        "id": h.id,
        "customer_phone": h.customer_phone,
        "reason": h.reason,
        "summary": h.summary,
        "created_at": h.created_at.isoformat(),
    }


@router.get("")
def list_handoffs(db: Session = Depends(get_db)):
    # id, not created_at: two handoffs in the same request can share a
    # timestamp at SQLite's resolution, and id is the reliable tiebreaker.
    return [_serialize(h) for h in db.query(Handoff).order_by(Handoff.id.desc()).all()]


@router.post("")
async def create_handoff(payload: HandoffCreate, db: Session = Depends(get_db)):
    handoff = Handoff(
        customer_phone=payload.customer_phone, reason=payload.reason, summary=payload.summary
    )
    db.add(handoff)
    db.flush()  # assigns handoff.id before the Job payload below needs it indirectly
    # Same transaction as the Handoff write (ADR-0002) - a Handoff can never
    # exist without its follow-up SMS being queued, unless there's no
    # verified number to text.
    if payload.customer_phone:
        db.add(
            Job(
                job_type="handoff_sms",
                payload={
                    "to": payload.customer_phone,
                    "body": (
                        "Thanks for calling - our on-call service advisor will be "
                        "reaching out to you shortly. Reply STOP to opt out."
                    ),
                },
            )
        )
    db.commit()
    db.refresh(handoff)
    serialized = _serialize(handoff)
    await broadcaster.publish("handoff_created", serialized)
    return serialized
