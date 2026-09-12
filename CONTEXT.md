# PitLine

A job-application demo for VaultDrive (Senior Full-Stack Developer / Founding
Engineer role): an after-hours service-department line where an AI agent
takes real action against a mock dealership DMS, plus a live ops dashboard.
Adapted from an earlier plumbing/HVAC dispatch demo (`~/VoiceAgent`) built
for a different role.

## Language

**PitLine**:
The demo's own product identity — plays the role VaultDrive plays in the
real pitch: "the operational layer that sits above the DMS and runs the
store instead of it." Composed of the after-hours voice agent and the ops
dashboard together, both acting on the DMS only through tool calls / the
same typed API.
_Avoid_: "the agent" alone — PitLine is the agent *and* the dashboard, not
just the phone bot.

**DMS**:
The mock legacy system of record (FastAPI + Postgres) that PitLine sits
above. Holds Technicians and Appointments. Has no intelligence of its own —
PitLine's agent and dashboard reach it only through its API, mirroring how
VaultDrive positions itself relative to a real dealership DMS.
_Avoid_: CRM (leftover term from the prior plumbing/HVAC demo), backend

**Urgent case**:
A caller report matching a fixed safety-system category: brake problems
(grinding, soft pedal, warning light), steering or suspension failure, an
active fluid leak, overheating or smoke, or an airbag warning light.
Triggers the Handoff simulation instead of the booking flow, and can only
be booked to a Master Technician.
_Avoid_: Emergency — vague; "urgent case" names the fixed-category rule
that decides it.

**Non-urgent case**:
Any caller report that doesn't match the Urgent case category list — e.g.
routine maintenance (oil change, tire rotation, inspection), a non-dangerous
noise, or an infotainment/electrical nuisance. Goes through the booking flow
and can be booked to either technician type.

**Technician**:
An entity in the DMS representing a dispatchable worker: name, specialty,
on-call flag. Two specialties:
- **Quick Service** — routine maintenance only.
- **Master Technician** — a superset: handles Urgent-case diagnostics *and*
  can take Quick Service work when no Quick Service tech is free.

This asymmetry is deliberate — it's why a genuine Urgent case never gets
falsely bounced to a Callback promise for lack of a matching specialty, and
why a Quick Service booking has a real fallback path instead of a hard
no-slot dead end.
_Avoid_: Worker, staff, agent — "agent" is reserved for the voice agent
itself.

**Appointment**:
A booking record in the DMS: technician_id, time slot, customer info,
vehicle info (make/model/year, inline — no separate Vehicle entity; nothing
in this scenario looks vehicles up by VIN or needs cross-appointment
history), urgency flag, status.
_Avoid_: Booking, reservation, ticket, RO (a real Repair Order carries a lot
this demo doesn't model — parts, labor lines, invoicing)

**Handoff simulation**:
The scripted response to an Urgent case with no Master Technician available,
or a caller who remains unintelligible after one retry: the agent announces
a transfer ("connecting you to our on-call service advisor now") and ends
the call. No actual telephony transfer occurs.
_Avoid_: Escalation, transfer — those imply a real handoff mechanism, which
this deliberately isn't.

**Callback promise**:
The agent's response to a Non-urgent case with no matching Appointment slot
in the seeded schedule window: offers the nearest available time outside
that window, and tells the caller someone will call back sooner if a
cancellation opens up.

**Ops Dashboard**:
The read-only Next.js/TypeScript screen that renders DMS state (new
Appointments, Handoff simulations, queued Follow-up jobs) live via SSE as
they happen. Has no write path of its own — every mutation it displays came
from PitLine's agent, not from the dashboard itself.
_Avoid_: Admin panel, CRM UI — it doesn't let anyone edit anything.

**Follow-up job**:
A row in the DMS's Postgres-backed queue table, enqueued when a call ends,
processed by a worker loop that sends the customer a confirmation or
handoff SMS via Twilio. The literal instance of VaultDrive's "follow-up"
vertical in this demo.
_Avoid_: Task, background process — "Follow-up job" is the specific,
queryable row; the worker is just what drains the table.
