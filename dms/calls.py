from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import verify_api_key, verify_review_key
from db import get_db
from models import Call

router = APIRouter(prefix="/calls", tags=["calls"])

# No human needed. resolved_no_action counts by rule; the judge can still
# overturn it in a review - see stats().
CONTAINED_OUTCOMES = {"booked", "cancelled", "resolved_no_action"}


def journey(tool_log: list[dict]) -> str:
    """Which journey a call was in, from what its tools did - code decides,
    like outcome. Derived on read, not stored, so it needs no migration and
    every past call gets one. A reschedule or step-up starts with
    find_appointment, so it's existing_appointment; a call that used neither
    tool (a price question, an unintelligible caller) is other.
    """
    tools = {t.get("tool") for t in tool_log}
    if "find_appointment" in tools:
        return "existing_appointment"
    if "check_availability" in tools:
        return "new_booking"
    return "other"


class CallCreate(BaseModel):
    call_sid: str | None = None
    outcome: str
    transfer_reason: str | None = None
    prompt_version: str
    caller_verified: bool
    caller_last4: str | None = None
    transcript: list[dict]
    tool_log: list[dict]


class CallReview(BaseModel):
    resolved: bool
    hallucinated: bool
    reason: str


def _serialize(c: Call) -> dict:
    return {
        "id": c.id,
        "call_sid": c.call_sid,
        "outcome": c.outcome,
        "transfer_reason": c.transfer_reason,
        "prompt_version": c.prompt_version,
        "caller_verified": c.caller_verified,
        "caller_last4": c.caller_last4,
        "transcript": c.transcript,
        "tool_log": c.tool_log,
        "review": c.review,
        "journey": journey(c.tool_log),
        "created_at": c.created_at.isoformat(),
    }


# Writes come from the agent, which holds DMS_API_KEY.
@router.post("", dependencies=[Depends(verify_api_key)])
def create_call(payload: CallCreate, db: Session = Depends(get_db)):
    call = Call(**payload.model_dump())
    db.add(call)
    db.commit()
    db.refresh(call)
    return {"id": call.id}


def _rates(calls: list[Call]) -> dict:
    total = len(calls)
    reviewed = [c for c in calls if c.review]
    contained = sum(c.outcome in CONTAINED_OUTCOMES for c in calls)
    judged_contained = sum(c.outcome in CONTAINED_OUTCOMES and c.review["resolved"] for c in reviewed)
    return {
        "total": total,
        # By code-decided outcome, over every call.
        "containment_by_rule": contained / total if total else None,
        # Contained AND the judge agrees it was resolved, over reviewed calls.
        "containment_judged": judged_contained / len(reviewed) if reviewed else None,
        "hallucination_rate": sum(c.review["hallucinated"] for c in reviewed) / len(reviewed) if reviewed else None,
        "reviewed": len(reviewed),
    }


# Aggregates only - no transcripts or numbers - so the dashboard's public
# key may read it.
@router.get("/stats", dependencies=[Depends(verify_api_key)])
def stats(db: Session = Depends(get_db)):
    calls = db.query(Call).all()
    by_outcome: dict[str, int] = {}
    by_version: dict[str, int] = {}
    by_journey: dict[str, list[Call]] = {}
    for c in calls:
        by_outcome[c.outcome] = by_outcome.get(c.outcome, 0) + 1
        by_version[c.prompt_version] = by_version.get(c.prompt_version, 0) + 1
        by_journey.setdefault(journey(c.tool_log), []).append(c)
    return {
        **_rates(calls),
        "by_outcome": by_outcome,
        "by_prompt_version": by_version,
        "by_journey": {j: _rates(group) for j, group in sorted(by_journey.items())},
    }


# Transcripts: review key only.
@router.get("", dependencies=[Depends(verify_review_key)])
def list_calls(unreviewed: bool = False, db: Session = Depends(get_db)):
    q = db.query(Call)
    if unreviewed:
        q = q.filter(Call.review.is_(None))
    return [_serialize(c) for c in q.order_by(Call.id.desc()).all()]


@router.patch("/{call_id}/review", dependencies=[Depends(verify_review_key)])
def review_call(call_id: int, payload: CallReview, db: Session = Depends(get_db)):
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Not found")
    call.review = payload.model_dump()
    db.commit()
    return _serialize(call)
