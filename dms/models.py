from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import relationship

from db import Base


class Technician(Base):
    __tablename__ = "technicians"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    specialty = Column(String, nullable=False)  # quick_service/master_technician
    on_call = Column(Boolean, nullable=False, default=False)

    appointments = relationship(
        "Appointment", backref="technician", cascade="all, delete-orphan"
    )


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True)
    technician_id = Column(Integer, ForeignKey("technicians.id"), nullable=False)
    time_slot = Column(DateTime, nullable=False)  # appointment start, fixed 1hr block
    customer_name = Column(String, nullable=False)
    customer_phone = Column(String, nullable=False)
    vehicle_make = Column(String, nullable=False)
    vehicle_model = Column(String, nullable=False)
    vehicle_year = Column(Integer, nullable=False)
    urgency = Column(String, nullable=False, default="non_urgent")  # urgent/non_urgent
    status = Column(String, nullable=False, default="booked")  # booked/completed/cancelled
    created_at = Column(DateTime, server_default=func.now())


class Handoff(Base):
    """A Handoff simulation event (CONTEXT.md) - logged so the Ops Dashboard
    (Day 4) has something to show for a safety transfer, since it has no
    Appointment of its own to point to.
    """

    __tablename__ = "handoffs"

    id = Column(Integer, primary_key=True)
    customer_phone = Column(String, nullable=False)
    reason = Column(String, nullable=False)  # matches call_transfer.py's TRANSFER_MESSAGES keys
    created_at = Column(DateTime, server_default=func.now())


class Job(Base):
    """Follow-up job queue - a row enqueued (same transaction as the
    Appointment/Handoff write it follows from) when a call ends, drained by
    the worker loop (agent/worker.py, added Day 3) to send a confirmation or
    handoff SMS via Twilio. See ADR-0002 for why this is a Postgres table
    rather than a dedicated broker.
    """

    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    job_type = Column(String, nullable=False, default="followup_sms")
    status = Column(String, nullable=False, default="pending")  # pending/sent/failed
    payload = Column(JSON, nullable=False, default=dict)
    attempt_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
