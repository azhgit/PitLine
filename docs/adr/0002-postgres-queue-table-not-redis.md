# Postgres-backed queue table instead of Redis/Celery for the follow-up job

The Follow-up job (send a confirmation/handoff SMS after a call ends) is
the demo's only background-job use case. We're queuing it as a row in a
Postgres `jobs` table (status, payload, attempt count), drained by a small
worker loop that marks each row succeeded or retries it — not a dedicated
broker like Redis + RQ/Celery.

A reviewer who sees "background job and queue systems" satisfied without
Redis might assume it was skipped or not understood, so this is worth
recording: Redis would be a second stateful service to provision, secure,
and keep alive on Railway for exactly one job type, and it buys nothing
this scale needs — no pub/sub fan-out, no sub-millisecond dequeue latency,
no need to survive the DB itself being unavailable. A queue table in the
same Postgres instance the DMS already uses gets transactional enqueue
(the appointment write and the job enqueue commit together, so a booking
can never exist without its follow-up being queued) for less operational
surface, not more.

This does not generalize past this demo's scale — a second job type with
real throughput or latency requirements would be a real reason to revisit
this and reach for a dedicated broker instead.
