from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
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
    __table_args__ = (
        # Closes the race window appointments.py's own check-then-insert
        # can't: two concurrent bookings for the same technician/slot both
        # pass the app-level conflict check, then both insert. The DB is
        # the only thing that can actually serialize that. Partial (not a
        # plain unique constraint) so a cancelled-then-rebooked slot still
        # works - see test_cancelled_appointment_frees_slot.
        Index(
            "uq_appointments_technician_slot_booked",
            "technician_id",
            "time_slot",
            unique=True,
            postgresql_where=text("status = 'booked'"),
            sqlite_where=text("status = 'booked'"),
        ),
    )

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


class BookingAttempt(Base):
    """One row per call to check_rate_limit (rate_limit.py), booking success
    or not - deliberately not just successful bookings, so a caller can't
    dodge the limit by intentionally colliding with an already-booked slot.
    Postgres-backed (not an in-process dict) so the limit actually holds if
    this service ever runs more than one replica - see ADR-0003.
    """

    __tablename__ = "booking_attempts"

    id = Column(Integer, primary_key=True)
    phone = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now())


class Handoff(Base):
    """A Handoff simulation event (CONTEXT.md) - logged so the Ops Dashboard
    (Day 4) has something to show for a safety transfer, since it has no
    Appointment of its own to point to.
    """

    __tablename__ = "handoffs"

    id = Column(Integer, primary_key=True)
    # Null when the caller's number couldn't be verified - exactly the
    # handoff the advisor most needs to see, so it's still logged.
    customer_phone = Column(String, nullable=True)
    reason = Column(String, nullable=False)  # matches call_transfer.py's TRANSFER_MESSAGES keys
    # What the advisor picks up with. Facts the agent's code owns
    # (verification, tool_results) are kept apart from what the LLM wrote
    # (summary, caller_stated) - see agent/bot.py's transfer_and_end_call.
    summary = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class Call(Base):
    """One finished phone call, written by the agent on disconnect: the
    basis for real-traffic containment and for running the simulator's
    judge over real transcripts (agent/review_calls.py).

    Outcome is decided by the agent's code from its tool log, never by the
    model. transcript has the booked name and calling number redacted, and
    is readable only with REVIEW_API_KEY - see calls.py.
    """

    __tablename__ = "calls"

    id = Column(Integer, primary_key=True)
    call_sid = Column(String, nullable=True)
    # booked / cancelled / resolved_no_action / transferred / abandoned
    outcome = Column(String, nullable=False)
    transfer_reason = Column(String, nullable=True)
    prompt_version = Column(String, nullable=False)
    caller_verified = Column(Boolean, nullable=False)
    caller_last4 = Column(String, nullable=True)  # never the full number here
    transcript = Column(JSON, nullable=False)  # [{"role": "agent"|"caller", "text": ...}]
    tool_log = Column(JSON, nullable=False)
    review = Column(JSON, nullable=True)  # judge verdict, set by review_calls.py
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
