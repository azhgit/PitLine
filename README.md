# PitLine

An after-hours service line for a car dealership: an AI voice agent that
answers real phone calls, classifies the problem, checks and books real
appointments against a mock dealer management system (DMS), and hands
anything it shouldn't handle to a human with a summary and the caller's
verification status. A live ops dashboard shows the schedule and every
handoff as it happens.

It's a portfolio project, but it runs on a real phone number and has been
tuned on real calls. Most of what's interesting here is **how the agent is
tested, measured, and fixed**, more than the booking flow itself.

- [CONTEXT.md](CONTEXT.md): the scenario and domain vocabulary
- [docs/adr/](docs/adr/): architecture decisions, and why each beat the obvious alternative

## How the agent is evaluated

Unit tests can't tell you whether an LLM agent will invent a callback
promise or read a phone number back wrong. So the agent has three layers of
checks, each catching a different class of failure.

### 1. Simulated callers with a regression gate ([agent/simulate.py](agent/simulate.py))

An LLM plays the caller. The agent runs on the **production** system prompt,
tool schemas, model, and thinking setting, all imported from `bot.py`, so
the suite can't drift from what's deployed. There are 14 scenarios, run 3
times each:

| Kind | Scenarios |
|---|---|
| Happy paths | oil change booked, urgent brakes booked with a Master Technician, verified cancel, cancel from another phone via SMS step-up, caller wants a different callback number |
| Unhappy paths | urgent with no slots → transfer, unintelligible twice → transfer, speech that never transcribes → transfer, DMS outage → no false confirmation, unverified cancel with no way to get a text → transfer, wrong step-up code twice → transfer |
| Compliance | caller declines a text confirmation → booked with no text |
| Hallucination traps | caller pushes for a time that was never offered; caller asks for a price the agent doesn't have |

Tool results are scripted per scenario rather than coming from a live DMS.
That's what lets the unhappy paths be forced on every run. The DMS itself is
covered by its own 44 tests.

Scoring is **code first, LLM judge only where code can't see**:

- **Deterministic checks.** The call must end with the right tool and
  transfer reason. Every booked slot must be one that `check_availability`
  actually returned. No cancel may be attempted for an unverified caller
  who hasn't passed step-up, or for an appointment no lookup returned. A
  booking may not send a text the caller declined.
  Every booking must land on the expected phone number. Every transfer must
  carry a handoff summary.
- **LLM judge** (Sonnet 5.5, structured output). It sees the transcript, the
  tool trace, and the agent's instructions. It answers two questions: was
  the caller's problem handled, and did the agent state anything (aloud or
  in the handoff summary) that no tool or instruction supported?

**Gate:** zero safety failures, plus a ≥90% pass rate. Anything else exits
non-zero. Every run reports containment, transfer rate, transfer
correctness, hallucination rate, and time to first spoken token.

### 2. Targeted evals

- [eval_dispatch_classification.py](agent/eval_dispatch_classification.py)
  checks urgent vs. non-urgent classification across the full category list.
- [eval_edge_cases.py](agent/eval_edge_cases.py) covers single-turn edge
  cases. It also runs on production's thinking setting.

### 3. Real calls, recorded and reviewed

The simulator is text-only, so it can't see speech-to-text errors or
turn-taking timing. Real calls are where those bugs appeared (see below).

Every call writes one record to the DMS when it ends. The record holds the
transcript, the tool log, and the prompt version, plus an **outcome decided
by code from the tool log, not by the model**. There are five outcomes:
booked, cancelled, resolved without action, transferred, and caller hung up.
That gives real-traffic containment with no model in the loop.

[review_calls.py](agent/review_calls.py) then runs the **same judge** the
simulator uses over every unreviewed call and writes its verdict back. The
judge can overturn a "resolved" call that it decides wasn't actually
resolved. The dashboard shows both numbers side by side: containment by
recorded outcome, and containment as judged.

Containment is also split **by journey**: new booking, existing
appointment (lookups, cancels, reschedules, step-up), and other. The DMS
derives the journey from each call's tool log when it's read, so like the
outcome it's decided by code, and older calls get one without a migration.

[weekly_review.py](agent/weekly_review.py) is the weekly read. It prints
containment per journey and per prompt version (the rollout comparison),
then a **fix list**: every call that wasn't contained or that the judge
flagged, grouped by journey and failure, with hallucinations first and then
the biggest groups. Each entry carries the judge's reason and the call id
to read before changing anything.

[call_to_scenario.py](agent/call_to_scenario.py) closes the loop. It turns
a call from the fix list into a simulator scenario. The call's own tool
results, stripped of names and numbers, become the scripted ones. A model
drafts the caller persona and the outcome an agent following its
instructions **should** have reached, so the scenario fails until the fix
is in. Scenarios land in `real_call_scenarios.json`, and `simulate.py` gates
them with the rest. The draft is reviewed by hand before it's committed. A
call that never got past a step it should have passed, such as a transfer
in place of step-up, has no recorded result for that step, so a person adds
one.

Transcripts are stored with the booked name and every phone number
redacted, and they can be read only with a separate `REVIEW_API_KEY`. The
dashboard's key can see aggregate stats only (see limitations).

Deeper diagnosis still comes from the Railway logs: per-turn latency, what
STT actually heard, which turn-taking strategy fired.

## What testing found and what changed

Each row was found by the simulator or a real call, fixed, and re-verified.
Where a prompt rule couldn't hold, the fix moved into code.

| Found by | Problem | Fix |
|---|---|---|
| Simulator baseline | The agent said "tomorrow" for dates it couldn't place, invented callbacks, hours, and staff, and talked over its own transfer line | Prompt rules; then a model change (below) |
| Simulator | With thinking off, the agent read 2030-06-18 back as "Thursday" | Code computes a `spoken` time for every slot, and the agent reads it verbatim |
| Simulator | The agent echoed a caller's "tomorrow" despite a prompt rule against it | `check_availability` returns the dealership's `today`, so "tomorrow" is checkable |
| Real call | 0.3–2.6 s of model "thinking" per turn on top of a ~0.6 s first byte | Thinking disabled. Simulator still 100% |
| Real call | Heard "672 022 9889", booked "672-202-2989", and the caller said yes to the readback | Bookings default to the Twilio-verified calling number. Code formats it, and the model never transcribes digits |
| Real call | Bookings made from a phone couldn't be found or cancelled from that same phone | Same fix: one source of truth for the caller's number |
| Real call | Deepgram's last segment arrived ~30 ms after the turn ended. It was read as a new turn, cut off the reply, and lost the caller's vehicle details after 13 s of dead air | [turn_start.py](agent/turn_start.py) ignores transcripts within 1.5 s of the caller stopping |
| Real call | VAD-only turn start missed a quiet "yes" (22 s of silence) | Same fix: transcripts can still start a turn outside that window |
| Real call | Garbled speech produced **no** transcript, so the LLM never ran: 56 s of silence | Empty turns reach the LLM as an explicit `[inaudible]` message, so the rule "ask once, then transfer" applies |
| Real call | On cancel calls the agent said "technician 2" | The DMS returns `technician_name` |

### Choosing the model with the suite

Prompt fixes on Haiku 4.5 plateaued: fixing one failure surfaced another.
Same prompt, same scenarios, 27 runs each:

| | Haiku 4.5 | Haiku 5.5 | Haiku 5.5, thinking off |
|---|---|---|---|
| Pass rate | 78% | 96% | 96% → 100% after the fixes above |
| Hallucination rate | 19% | 0% | 4% → 0% |
| Safety failures | 5 | 0 | 1 → 0 |
| Time to first token, p50 / p95 (simulator) | 0.45 / 0.70 s | 0.63 / 1.43 s | 0.57 / 1.05 s |

Haiku 5.5 is also a tenth of Haiku 4.5's price per token. The simulator
under-predicted thinking time. Real calls showed it, and that's why thinking
is off in production. After the change, real calls measured 0.46–0.81 s to
the first spoken token on every turn.

## Shipping prompt changes

Prompt changes roll out in stages rather than all at once (see
`PROMPT_VERSIONS` in [bot.py](agent/bot.py) and [rollout.py](agent/rollout.py)):

1. Put the new prompt in `PROMPT_VERSIONS["candidate"]`.
2. Gate it: `SIM_PROMPT_VERSION=candidate python simulate.py`. No rollout
   starts until the gate passes.
3. Ramp `CANDIDATE_PERCENT` on Railway: 1 → 10 → 50 → 100. Each call is
   bucketed by a hash of its Twilio CallSid, so the same call always gets
   the same version, and raising the percent only moves calls *onto* the
   candidate.
4. Compare versions using the `prompt_version` recorded in the call logs,
   the per-stage latency log, and every handoff record.
5. Promote the candidate to `stable`. To roll back at any step, set
   `CANDIDATE_PERCENT=0`. That's an environment variable change, not a code
   deploy.

## Compliance language and consent

Anything that has to be said word for word is spoken by code, never
generated. The greeting discloses that the caller is talking to an
automated assistant and that the call may be recorded. Because nothing
would notice if that code path broke, `weekly_review.py` checks every real
call's first line against the exact text and puts any mismatch at the top
of the fix list.

Texts need a yes. Before booking, the candidate prompt asks whether it's OK
to text a confirmation, and `book_appointment` carries the answer as
`sms_consent`. On `false`, the DMS books without queuing the text, and that
rule lives in code. The yes or no stays on record in the call's tool log.
Every text ends with "Reply STOP to opt out." The handoff text after a
transfer doesn't ask first: a transfer usually comes before the booking
step, and adding a question in front of it would make the caller wait.
This is consent engineering for a demo, not legal advice on TCPA.

## Verification tiers

Who the caller is decides what the agent may do. Each tier is enforced in
code, not by the prompt:

| Tier | How | Unlocks |
|---|---|---|
| 0 | None | New bookings, general questions |
| 1 | Twilio Caller ID matches the appointment | Look up, reschedule, cancel |
| 2 | SMS step-up via Twilio Verify ([step_up.py](agent/step_up.py)) | The same, for a caller on another phone or with a withheld number |

Step-up exists because "calling from my work phone" used to be an automatic
transfer. Now the agent asks the caller to **key in** the number the
appointment is under. A real call had already shown that STT can't be
trusted with digits, so the number and the code both come in over DTMF. The
agent texts a code to that number, and a passed check makes that number the
call's verified identity. Lookups, cancel's ownership check, and a
reschedule's rebook all follow it, with no second ownership path.

- **Limits live in code:** at most 2 codes sent and 2 wrong codes per call,
  then a `verification_failed` transfer. Codes go only to dialable US/Canada
  numbers, which keeps premium international numbers (the SMS-pumping
  target) out.
- **No DMS lookup before sending.** Whether a number has an appointment is
  revealed only after the caller proves they hold that phone. Twilio owns
  the code, its expiry, and its attempt limits, so nothing is stored here.
- **Not done:** step-up when Caller ID already matches. The code would go to
  the same phone a spoofer is spoofing, so it would add friction without
  adding proof.

Step-up ships as the `candidate` prompt (only the unverified-caller rule
differs) through the staged rollout below. Until it's promoted, the
`stepup_*` scenarios pass only with `SIM_PROMPT_VERSION=candidate`.

## Handoffs

When the agent transfers a call, the advisor gets a structured record (see
`transfer_and_end_call` in [bot.py](agent/bot.py)), split by who is allowed
to write each part:

- **Written by code, never the model:** the transfer reason, whether the
  caller was verified and how (Caller ID or SMS code), and every tool call made during the call along
  with its result.
- **Written by the model, labelled as such:** a one- or two-sentence summary,
  plus the vehicle and symptom *as the caller stated them*.

Unverified callers are logged too, with no phone number and no SMS sent.
That's the handoff an advisor most needs to see. The dashboard shows a
verification badge, the caller-stated fields, the summary, and a "what the
agent tried" list. The phone transfer itself is simulated: the agent speaks
a fixed line and ends the call.

## Architecture

```
Inbound call (Twilio Media Streams)
  → agent/  Pipecat pipeline: Deepgram STT → Claude Haiku 5.5 tool-calling → ElevenLabs TTS
      → reads/writes the DMS through its HTTP API only
  → back to Twilio

agent/worker.py
  → claims jobs from the DMS queue, sends confirmation/handoff SMS via Twilio

dashboard/  Next.js + TypeScript
  → read-only live view over the DMS's SSE feed

dms/  FastAPI + Postgres
  → technicians, appointments, handoffs, job queue
  → the only service that touches Postgres (ADR-0001)
```

There are five services on Railway: agent, dms, worker, dashboard, and
Postgres.

Some engineering choices that hold up under load, not just in a demo:

- A **partial unique index** on technician and slot closes a double-booking
  race that an app-level check can't. It was proven with 10 concurrent
  bookings against the live deployment.
- A **Postgres job queue** uses `FOR UPDATE SKIP LOCKED`, so multiple worker
  replicas never double-send. The SMS job is written in the same transaction
  as the booking (ADR-0002).
- **Caller identity comes from Twilio, never from the conversation.** Lookups
  and cancels use the verified Caller ID. `cancel_appointment` re-checks
  ownership in code, so the model can't be talked into cancelling someone
  else's appointment.
- **Twilio webhook signatures are verified**, and the media WebSocket is
  token-gated.
- **Dependencies are pinned** to the versions the tests ran against. An
  unpinned redeploy once pulled a newer SQLAlchemy and took the DMS down
  for about 5 minutes. That's how this rule got here.

## Repo layout

```
agent/       Pipecat pipeline (bot.py), SMS worker, simulator, evals, turn-taking, prompt rollout
dms/         FastAPI + Postgres DMS: appointments, technicians, handoffs, job queue, SSE
dashboard/   Next.js + TypeScript ops dashboard
docs/adr/    Architecture decision records
CONTEXT.md   Scenario and domain vocabulary
```

## Running it locally

Each service needs its own `.env` / `.env.local`.

```bash
# Postgres
docker run -d -p 5433:5432 -e POSTGRES_PASSWORD=pitline -e POSTGRES_DB=pitline postgres:16-alpine

# DMS
cd dms && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python seed.py && uvicorn main:app --port 8001
pytest                                   # 44 tests

# Agent (separate terminal)
cd agent && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python bot.py -t twilio --host 0.0.0.0 --port 7860 -x <your-public-domain>
python simulate.py                       # regression gate; costs real API spend
python worker.py                         # separate terminal

# Dashboard (separate terminal)
cd dashboard && npm install && npm run dev
```

Point a Twilio number's Voice webhook at `https://<your-domain>/` (POST) to
call it.

## Known limitations

- **The simulator is text-only.** It can't catch speech-to-text errors or
  turn-taking timing. Every bug in the "Real call" rows above was invisible
  to it.
- **Three runs per scenario is a coarse signal.** The same configuration
  scored 70% and 85% on two consecutive runs. The suite catches big
  regressions, not 5-point ones. The 0-safety-failure gate will sometimes
  flag a rare failure that a rerun doesn't reproduce.
- **The judge is a model too.** It needed calibration: it had to see the
  tool descriptions, and it writes its reasoning before its verdict. An
  early version marked correct weekdays as hallucinations.
- **Evals share the live agent's API rate limit.** A back-to-back run once
  hit 429s. A real setup would use a separate key or workspace.
- **Quiet speech that VAD misses** gets a reply only after Pipecat's 5 s
  turn timeout. That's better than the 22 s it was, but it isn't instant.
- **Step-up is only partly covered.** The simulator types `DTMF: ...` as
  text, so keypad timing and the real Verify API are tested only on real
  calls. A keypad-only turn has no speech for smart-turn to end, so the
  worst case is Pipecat's 5 s turn timeout. The code the caller keys in also
  stays in the transcript. It's single-use and expires, but it isn't
  redacted.
- **The handoff is simulated.** There's no live transfer to a person.
- **Transcript redaction only covers what a tool saw.** The booked name and
  the phone numbers are redacted. A name the caller gave before a transfer,
  or digits STT spelled out as words, stay in the transcript. The next step
  is NER or an LLM redaction pass. There's also no retention policy yet; a
  real deployment would purge transcripts after a fixed window.
- **The dashboard's DMS key is public, but it's read-only.** It's a
  `NEXT_PUBLIC_` variable, so it ships in the browser bundle. It used to be
  the only DMS key, which meant anyone could take it from the page and
  cancel appointments or claim SMS jobs. Now it accepts GET requests only.
  Writes need a separate key, sent as a header and held only by the agent
  and worker. The old key was rotated. Transcripts sit behind a third key.
  Names and phone numbers in the schedule are still readable with the
  public key. The full fix is a server-side proxy, so the browser holds no
  DMS key at all.
- Single dealership, fixed seed data, English only.

## Architecture decisions

- [ADR-0001](docs/adr/0001-dms-ops-layer-mirrors-vaultdrive-architecture.md): the DMS is a dumb system of record, and PitLine is the ops layer above it
- [ADR-0002](docs/adr/0002-postgres-queue-table-not-redis.md): a Postgres queue table instead of Redis/Celery
- [ADR-0003](docs/adr/0003-horizontal-scaling-path.md): what scaling past one replica actually requires
