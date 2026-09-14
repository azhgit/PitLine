# PitLine

An after-hours service-department line for a car dealership: an AI voice
agent that answers real phone calls, classifies the problem, checks and
books real appointments against a mock DMS, and simulates a handoff for
anything safety-related — plus a live ops dashboard, together playing the
role VaultDrive's own pitch describes: "the operational layer that sits
above the DMS and runs the store instead of it."

Built as a job-application demo. See [CONTEXT.md](CONTEXT.md) for the
scenario and domain vocabulary, [doc/pitline-plan.md](doc/pitline-plan.md)
for the execution plan this was built against, and
[docs/adr/](docs/adr/) for the architecture decisions and why each one was
made instead of the obvious alternative.

## Why this exists

VaultDrive's JD asks for full-stack ownership across a typed web stack,
relational databases, background job/queue systems, and API design, with
hands-on AI/LLM work as the mandatory core of the role — voice/telephony is
a bonus, not the point. This adapts an earlier voice-pipeline-only demo
(plumbing/HVAC dispatch) into something that actually exercises all of
that: a real frontend, a real Postgres-backed queue, and a DMS that's a
genuine service of its own, not a relabeled mock. The engineering bar
throughout is **correctness, not cleverness** — fixed classification rules
instead of model improvisation, decisions verified against the real API and
real concurrent load instead of assumed, and every "why not the obvious
alternative" question answered in writing rather than left implicit.

## Architecture

```
Inbound call (Twilio Media Streams)
  → agent/  (Pipecat pipeline: Deepgram STT → Claude tool-calling → ElevenLabs TTS)
      → reads/writes the DMS through its HTTP API only
  → back to Twilio

agent/worker.py
  → polls dms/ 's job queue (POST /jobs/claim), sends confirmation/handoff SMS via Twilio

dashboard/  (Next.js + TypeScript)
  → subscribes to dms/ 's SSE feed, read-only live view of new appointments and handoffs

dms/  (FastAPI + Postgres)
  → Technician / Appointment / Handoff / Job tables
  → the only thing that touches Postgres directly - agent, worker, and
    dashboard all reach it through its API (see ADR-0001)
```

Five services deployed independently on Railway: `agent`, `dms`, `worker`,
`dashboard`, and a managed Postgres instance.

## How this maps to the JD

| JD requirement | Where | Evidence |
|---|---|---|
| Hands-on AI/LLM work shipped to real users (marked mandatory) | `agent/bot.py` | Real Claude tool-calling agent, deployed, taking real inbound Twilio calls, writing real bookings and sending real SMS |
| Typed web stack, front and back | `dashboard/app/page.tsx`, `dms/*.py` | Next.js + TypeScript dashboard; FastAPI + Pydantic on the backend |
| Relational databases | `dms/models.py` | Postgres with a partial unique index closing a real double-booking race (`uq_appointments_technician_slot_booked`), not just an app-level check |
| Background job and queue systems | `dms/jobs.py`, `agent/worker.py` | Postgres-backed queue with `FOR UPDATE SKIP LOCKED` claim semantics - safe for multiple worker replicas, not a naive poll-and-patch |
| API design | `dms/main.py`, `dms/*.py` | REST + SSE, API-key auth, rate limiting, CORS scoped to what the dashboard actually needs |
| Cloud infrastructure | Railway (5 services) | Independent deploys, env-var-driven config, a real security fix shipped post-launch (see below) |
| Voice, telephony, real-time streaming (strong asset) | `agent/*.py` | Hand-built Pipecat pipeline (not a managed voice platform), barge-in verified against real mid-sentence interruptions, latency measured per pipeline stage |
| Automotive/DMS-adjacent systems (strong asset) | `CONTEXT.md`, `dms/models.py` | Quick Service / Master Technician routing asymmetry, vehicle make/model/year schema, dealership vocabulary enforced throughout |
| Built something that had to be right (strong asset) | `docs/adr/`, `dms/appointments.py`, `agent/twilio_auth.py` | Race condition closed at the DB level and proven under real concurrent load; a Twilio webhook with zero signature verification found and fixed post-launch, not hypothetical |

## Repo layout

```
agent/       Pipecat voice pipeline (bot.py), SMS worker (worker.py), evals
dms/         FastAPI + Postgres mock DMS - appointments, technicians,
             handoffs, job queue, SSE event feed
dashboard/   Next.js + TypeScript Ops Dashboard
docs/adr/    Architecture decision records
doc/         Original execution plan
CONTEXT.md   Scenario and domain vocabulary
```

## Running it locally

Each service needs its own `.env` / `.env.local` - see each directory's
`requirements.txt` / `package.json` for dependencies.

```bash
# Postgres (or point DATABASE_URL at any Postgres instance)
docker run -d -p 5433:5432 -e POSTGRES_PASSWORD=pitline -e POSTGRES_DB=pitline postgres:16-alpine

# DMS
cd dms && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python seed.py && uvicorn main:app --port 8001

# Agent (separate terminal)
cd agent && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python bot.py -t twilio --host 0.0.0.0 --port 7860 -x <your-ngrok-or-public-domain>

# Worker (separate terminal)
python worker.py

# Dashboard (separate terminal)
cd dashboard && npm install && npm run dev
```

Point a Twilio phone number's Voice webhook at `https://<your-domain>/`
(POST) to actually call it.

## What's real vs. simulated

Everything is real except the **Handoff simulation** itself (CONTEXT.md):
when the agent decides a call needs a human - an Urgent case with no
Master Technician available, or a caller it can't understand twice in a
row - it speaks a fixed line and ends the call. No real transfer happens,
by design; that's the one deliberately-scoped-out piece, not an
accident. Everything upstream of that decision (classification, DMS
queries, the actual hangup, the queued follow-up SMS) is real.

## Verification

- **42 pytest tests** (`dms/`) covering booking, availability routing,
  rate limiting, the job queue, and the concurrency fixes below
- **`agent/eval_dispatch_classification.py`** - the full CONTEXT.md
  urgent/non-urgent category list, run against the real Anthropic API
  (Haiku and Sonnet), 22/22
- **`agent/eval_edge_cases.py`** - unintelligible-caller, no-availability,
  DMS-outage, and booking-confirmation flows against the real API, 8/8
- **`dms/loadtest_concurrency.py`** - fires genuine concurrent traffic at
  a live DMS instance to prove two fixes actually hold, not just in
  theory: 10 simultaneous bookings for the same slot (exactly one
  succeeds), a booking burst past the rate limit (blocked at exactly the
  right count), and 20 concurrent job-claim calls against 8 real jobs
  (never double-claimed) - run against the real Railway deployment while
  its own live worker was concurrently draining the same queue
- **Real phone calls**, repeatedly, against the deployed Railway
  services - not just local/ngrok - including one that caught a live bug
  (a Twilio `<Stream>` URL quirk breaking the WebSocket auth token) within
  the hour it shipped

`agent/latency_chart.png` is generated from real call data by
`agent/visualize_latency.py`, not synthetic numbers.

## Known limitations

Written down rather than glossed over:

- **No multi-replica load-bearing test on Railway itself.** The fixes in
  [ADR-0003](docs/adr/0003-horizontal-scaling-path.md) (Postgres
  `LISTEN`/`NOTIFY` for the SSE feed, `FOR UPDATE SKIP LOCKED` job
  claiming) are verified by actually running two local processes against
  the same database, not by scaling the live Railway services past one
  replica each (deliberately, to avoid the extra cost).
- **No connection pooling (PgBouncer).** Not needed at this scale; noted
  in ADR-0003 as the next thing to add if replica count grows.
- **Single dealership, fixed seed data.** No multi-tenant DMS, no admin
  UI for managing technicians/schedules - out of scope for a demo built
  around one scenario.
- **English only.** STT/TTS assume English; no language detection or
  localization.
- **The Twilio account required a paid upgrade mid-build** - trial
  accounts can't programmatically end calls or send free-text SMS. Noted
  here because it's a real operational constraint anyone extending this
  would hit immediately, not a design choice.

## Architecture decisions

- [ADR-0001](docs/adr/0001-dms-ops-layer-mirrors-vaultdrive-architecture.md) - the DMS is a dumb system of record; PitLine is the ops layer above it
- [ADR-0002](docs/adr/0002-postgres-queue-table-not-redis.md) - a Postgres queue table instead of Redis/Celery for the Follow-up job
- [ADR-0003](docs/adr/0003-horizontal-scaling-path.md) - what scaling past one replica of each service actually requires, and why none of it needs a new paid service
