from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from appointments import URGENCIES
from db import get_db
from models import Appointment, Technician

router = APIRouter(prefix="/availability", tags=["availability"])

# Fixed 1hr-block business window (see models.py: time_slot is a fixed 1hr
# block). Urgent calls are restricted to on-call technicians only; non-urgent
# calls can go to any technician with the matching specialty.
BUSINESS_HOURS = range(8, 18)  # slot start hours 08:00-17:00
LOOKAHEAD_DAYS = 7  # matches seed.py's ~1 week of seeded schedule


@router.get("")
def get_available_slots(
    specialty: str,
    urgency: str = "non_urgent",
    limit: int = 2,
    db: Session = Depends(get_db),
):
    if urgency not in URGENCIES:
        raise HTTPException(status_code=422, detail=f"Invalid urgency: {urgency}")

    technicians = db.query(Technician).filter(Technician.specialty == specialty).all()
    if urgency == "urgent":
        technicians = [t for t in technicians if t.on_call]
    if not technicians:
        return []

    tech_ids = [t.id for t in technicians]
    booked = {
        (a.technician_id, a.time_slot)
        for a in db.query(Appointment)
        .filter(Appointment.technician_id.in_(tech_ids))
        .filter(Appointment.status == "booked")
        .all()
    }

    now = datetime.now()
    day_zero = now.replace(minute=0, second=0, microsecond=0)
    slots = []
    for day_offset in range(LOOKAHEAD_DAYS):
        day = day_zero + timedelta(days=day_offset)
        for hour in BUSINESS_HOURS:
            slot_time = day.replace(hour=hour)
            if slot_time <= now:
                continue
            for tech in technicians:
                if (tech.id, slot_time) in booked:
                    continue
                slots.append(
                    {
                        "technician_id": tech.id,
                        "technician_name": tech.name,
                        "time_slot": slot_time.isoformat(),
                    }
                )

    slots.sort(key=lambda s: s["time_slot"])
    return slots[:limit]
