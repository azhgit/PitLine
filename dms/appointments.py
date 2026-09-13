import re
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db import get_db
from events import broadcaster
from models import Appointment, Job, Technician
from rate_limit import check_rate_limit

router = APIRouter(prefix="/appointments", tags=["appointments"])

STATUSES = {"booked", "completed", "cancelled"}
URGENCIES = {"urgent", "non_urgent"}


def _normalize_phone(value: str) -> str:
    """Digits only, dropping a leading US/Canada country code - so Twilio's
    E.164 "+15550428871" matches a caller-stated "555-042-8871", not just a
    formatting difference (same fix as eval_edge_cases.py's phone-match
    check) but a genuine country-code mismatch: real callers state 10-digit
    numbers, Twilio's Caller ID always includes the +1.
    """
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


class AppointmentCreate(BaseModel):
    technician_id: int
    time_slot: datetime
    customer_name: str
    customer_phone: str
    vehicle_make: str
    vehicle_model: str
    vehicle_year: int
    urgency: str = "non_urgent"


def _serialize(a: Appointment) -> dict:
    return {
        "id": a.id,
        "technician_id": a.technician_id,
        "time_slot": a.time_slot.isoformat(),
        "customer_name": a.customer_name,
        "customer_phone": a.customer_phone,
        "vehicle_make": a.vehicle_make,
        "vehicle_model": a.vehicle_model,
        "vehicle_year": a.vehicle_year,
        "urgency": a.urgency,
        "status": a.status,
    }


@router.get("")
def list_appointments(
    technician_id: int | None = None,
    status: str | None = None,
    customer_phone: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Appointment)
    if technician_id is not None:
        q = q.filter(Appointment.technician_id == technician_id)
    if status is not None:
        q = q.filter(Appointment.status == status)
    appointments = q.order_by(Appointment.time_slot).all()
    if customer_phone is not None:
        # Digit-normalized comparison in Python, not a SQL filter - stored
        # numbers and the caller's actual Twilio number can differ in
        # formatting (dashes, country code) even when they're the same
        # number. Fine at this table size (demo scale).
        target = _normalize_phone(customer_phone)
        appointments = [a for a in appointments if _normalize_phone(a.customer_phone) == target]
    return [_serialize(a) for a in appointments]


@router.get("/{appointment_id}")
def get_appointment(appointment_id: int, db: Session = Depends(get_db)):
    a = db.get(Appointment, appointment_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _serialize(a)


@router.post("")
async def create_appointment(
    payload: AppointmentCreate,
    db: Session = Depends(get_db),
    x_skip_rate_limit: bool = Header(default=False, alias="X-Skip-Rate-Limit"),
):
    # Only the agent can set this header (it's the sole holder of the DMS
    # API key), and only after code - not the LLM - has confirmed this
    # booking is the rebook half of a Caller-ID-verified reschedule. See
    # agent/bot.py's book_appointment for where it's actually set.
    if not x_skip_rate_limit and not check_rate_limit(db, payload.customer_phone):
        raise HTTPException(
            status_code=429, detail="Too many booking attempts for this phone number"
        )
    if payload.urgency not in URGENCIES:
        raise HTTPException(status_code=422, detail=f"Invalid urgency: {payload.urgency}")
    technician = db.get(Technician, payload.technician_id)
    if technician is None:
        raise HTTPException(status_code=404, detail="Technician not found")
    # App-level check first for a fast, friendly error on the common case -
    # but the actual race window between two concurrent requests both
    # passing this check is closed by the partial unique index in
    # models.py (see ADR-0003), not by this check alone.
    conflict = (
        db.query(Appointment)
        .filter(
            Appointment.technician_id == payload.technician_id,
            Appointment.time_slot == payload.time_slot,
            Appointment.status == "booked",
        )
        .first()
    )
    if conflict is not None:
        raise HTTPException(
            status_code=409, detail="Technician is already booked for that time slot"
        )
    appointment = Appointment(**payload.model_dump())
    db.add(appointment)
    # Same transaction as the Appointment write (ADR-0002) - a booking can
    # never exist without its confirmation SMS being queued.
    db.add(
        Job(
            job_type="confirmation_sms",
            payload={
                "to": appointment.customer_phone,
                "body": (
                    f"Your appointment with {technician.name} is confirmed for "
                    f"{appointment.time_slot.strftime('%A, %B %d at %-I:%M %p')}. See you then!"
                ),
            },
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # The unique index caught a genuine race the app-level check above
        # missed - another request for the same technician/slot committed
        # first, in the gap between that check and this commit.
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Technician is already booked for that time slot"
        )
    serialized = _serialize(appointment)
    await broadcaster.publish("appointment_created", serialized)
    return serialized


@router.patch("/{appointment_id}")
def update_appointment_status(appointment_id: int, status: str, db: Session = Depends(get_db)):
    if status not in STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status: {status}")
    a = db.get(Appointment, appointment_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Not found")
    a.status = status
    db.commit()
    return _serialize(a)
