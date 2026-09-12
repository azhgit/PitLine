from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from appointments import URGENCIES
from db import get_db
from models import Appointment, Technician

router = APIRouter(prefix="/availability", tags=["availability"])

# Fixed 1hr-block business window (see models.py: time_slot is a fixed 1hr
# block).
BUSINESS_HOURS = range(8, 18)  # slot start hours 08:00-17:00
LOOKAHEAD_DAYS = 7  # matches seed.py's ~1 week of seeded schedule
# Callback promise (CONTEXT.md) only: how far past LOOKAHEAD_DAYS to look for
# the "nearest available time outside that window" when nothing's open in
# the normal window. Not used for urgent - an urgent case with no Master
# Technician slot in the normal window goes to the Handoff simulation
# instead of a wider search.
EXTENDED_LOOKAHEAD_DAYS = 30


def _find_slots(
    db: Session, technician_ids: list[int], day_start: int, day_end: int, limit: int
) -> list[dict]:
    if not technician_ids:
        return []
    technicians = {t.id: t for t in db.query(Technician).filter(Technician.id.in_(technician_ids))}
    booked = {
        (a.technician_id, a.time_slot)
        for a in db.query(Appointment)
        .filter(Appointment.technician_id.in_(technician_ids))
        .filter(Appointment.status == "booked")
        .all()
    }

    now = datetime.now()
    day_zero = now.replace(minute=0, second=0, microsecond=0)
    slots = []
    for day_offset in range(day_start, day_end):
        day = day_zero + timedelta(days=day_offset)
        for hour in BUSINESS_HOURS:
            slot_time = day.replace(hour=hour)
            if slot_time <= now:
                continue
            for tech_id, tech in technicians.items():
                if (tech_id, slot_time) in booked:
                    continue
                slots.append(
                    {
                        "technician_id": tech_id,
                        "technician_name": tech.name,
                        "time_slot": slot_time.isoformat(),
                    }
                )
            if len(slots) >= limit:
                slots.sort(key=lambda s: s["time_slot"])
                return slots[:limit]

    slots.sort(key=lambda s: s["time_slot"])
    return slots[:limit]


@router.get("")
def get_available_slots(
    urgency: str = "non_urgent",
    limit: int = 2,
    db: Session = Depends(get_db),
):
    """Urgent case: only on-call Master Technicians, normal window only - no
    Master Technician slot there means the Handoff simulation, not a wider
    search (CONTEXT.md).

    Non-urgent case: Quick Service technicians (any on_call status) first,
    since Master Technician capacity is meant as overflow, not a first-class
    pool - "why a Quick Service booking has a real fallback path instead of
    a hard no-slot dead end" (docs/adr/...). Falls back to Master Technicians
    only if Quick Service has nothing in the normal window, then - if still
    nothing - widens the search past LOOKAHEAD_DAYS for the Callback promise
    ("offers the nearest available time outside that window"), flagged via
    outside_window so the agent can phrase that differently from a normal
    read-back.
    """
    if urgency not in URGENCIES:
        raise HTTPException(status_code=422, detail=f"Invalid urgency: {urgency}")

    if urgency == "urgent":
        master_ids = [
            t.id
            for t in db.query(Technician).filter(
                Technician.specialty == "master_technician", Technician.on_call == True  # noqa: E712
            )
        ]
        slots = _find_slots(db, master_ids, 0, LOOKAHEAD_DAYS, limit)
        return {"slots": slots, "outside_window": False}

    quick_service_ids = [
        t.id for t in db.query(Technician).filter(Technician.specialty == "quick_service")
    ]
    slots = _find_slots(db, quick_service_ids, 0, LOOKAHEAD_DAYS, limit)
    if slots:
        return {"slots": slots, "outside_window": False}

    master_ids = [
        t.id for t in db.query(Technician).filter(Technician.specialty == "master_technician")
    ]
    slots = _find_slots(db, master_ids, 0, LOOKAHEAD_DAYS, limit)
    if slots:
        return {"slots": slots, "outside_window": False}

    all_ids = quick_service_ids + master_ids
    slots = _find_slots(db, all_ids, LOOKAHEAD_DAYS, EXTENDED_LOOKAHEAD_DAYS, limit=1)
    return {"slots": slots, "outside_window": bool(slots)}
