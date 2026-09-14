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

**What needed a real fix, and is now actually fixed, not just noted here**:

- `dms/rate_limit.py`'s booking limiter was an in-process dict: correct at
  one replica, silently wrong at two (each replica would allow its own
  separate quota). Rewritten onto a Postgres table (`booking_attempts`)
  instead, so the limit holds regardless of replica count - same database
  this project already runs, not a new store.
- `dms/events.py`'s SSE broadcaster was in-process pub/sub - a write on
  replica A would never have reached a dashboard tab connected to replica
  B. Rewritten onto Postgres `LISTEN`/`NOTIFY`: `publish()` sends a
  `NOTIFY`, and a listener connection (opened in `main.py`'s lifespan)
  forwards every notification - this replica's own and every other
  replica's - into the same local per-tab queues the dashboard reads from.
  Verified for real: a second local DMS process on a different port,
  pointed at the same Postgres, received a live event for a booking made
  through the *other* process - not simulated, an actual second process.
- `agent/worker.py` had no claim step - two worker replicas polling
  `GET /jobs?status=pending` at once could both pick up the same row and
  double-send an SMS. `dms/jobs.py` now has `POST /jobs/claim`, which does
  `SELECT ... FOR UPDATE SKIP LOCKED` then marks the row `claimed` in the
  same transaction - not a plain `SELECT` followed by a separate `PATCH`.
  `loadtest_concurrency.py` fires 20 concurrent claim calls against 8 real
  jobs on the live Railway DMS - while its actual worker was *also*
  concurrently draining the same queue - and no job is ever claimed twice.

**What would still need work past one replica, and how, without adding a
paid service**: more replicas means more direct Postgres connections, and
past some count that's a connection-limit problem before it's a compute
problem. Fix: PgBouncer in front of Postgres (Railway can provision this on
the same database), not a bigger Postgres plan. Not done here - this scale
doesn't need it yet, and adding it now would be solving a problem this demo
doesn't have.

None of this needs Redis, a message broker, or a second database - the
same reasoning as ADR-0002: this scale doesn't need the operational surface
a dedicated broker adds, and neither does scaling it out further.
