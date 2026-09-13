# Scaling past one replica of each service reuses the Postgres already provisioned - no new paid infrastructure

This demo runs one replica of each service. Written down here because "how
would this handle more traffic" is a fair question for a role evaluating
CTO-track technical judgment, and the honest answer shouldn't be improvised
in the room.

**What's already fine at N replicas, no changes needed**: `dms`'s REST
endpoints are stateless - every request reads/writes Postgres and nothing
else, and the booking race is now closed by a DB-level partial unique index
on `(technician_id, time_slot) WHERE status='booked'` (models.py), not just
an app-level check-then-insert. `agent`'s voice pipeline is one Pipecat
worker per phone call; Twilio opens a fresh Media Stream connection per
call, so any replica can take any call - there's no session affinity to
design for. The dashboard is a stateless Next.js app; N replicas of it need
nothing beyond what a load balancer already gives for free.

**What needed a real fix, not just a note** - `dms/rate_limit.py`'s booking
limiter was an in-process dict: correct at one replica, silently wrong at
two (each replica would allow its own separate quota). Rewritten to a
Postgres table (`booking_attempts`) instead, so the limit holds regardless
of replica count - same table this project already runs, not a new store.

**What would still need work past one replica, and how, without adding a
paid service**:

- `dms/events.py`'s SSE broadcaster is in-process pub/sub - a write on
  replica A never reaches a dashboard tab connected to replica B. Fix:
  Postgres `LISTEN`/`NOTIFY` on the same instance instead of an in-memory
  `set` of queues, so every replica hears every write.
- `agent/worker.py` has no claim step - two worker replicas polling
  `GET /jobs?status=pending` at once could both pick up the same row and
  double-send an SMS. Fix: `SELECT ... FOR UPDATE SKIP LOCKED` (or an
  atomic `UPDATE ... WHERE status='pending' RETURNING`) when claiming a
  job, not a plain `SELECT` then a separate `PATCH`.
- More replicas means more direct Postgres connections; past some count
  that's a connection-limit problem before it's a compute problem. Fix:
  PgBouncer in front of Postgres (Railway can provision this on the same
  database), not a bigger Postgres plan.

None of this needs Redis, a message broker, or a second database - the
same reasoning as ADR-0002: this scale doesn't need the operational surface
a dedicated broker adds, and neither does scaling it out further.
