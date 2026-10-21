# Mapping to the AI Experience Engineer role (Upgrade)

How PitLine lines up with the role's responsibilities and requirements: what
exists here, where to look, and where it doesn't reach. PitLine is built by
hand rather than on a no-code agent platform. So it shows the same concerns
(journeys, tools, evals, handoffs, guardrails) one layer down.

## What the role does

| Responsibility | In PitLine | Where |
|---|---|---|
| **Journey creation**: design and build journeys from discovery to rollout | Two journeys run end to end on a real phone number. *New problem* covers classify, check availability, book, and confirm. *Existing appointment* covers verify, look up, then cancel or reschedule. | `SYSTEM_INSTRUCTION` and tool schemas in [agent/bot.py](../agent/bot.py); [CONTEXT.md](../CONTEXT.md) |
| **Optimization**: review transcripts weekly, find failures, ship fixes | Every fix in the README's [findings table](../README.md#what-testing-found-and-what-changed) came from reading simulator transcripts or production call logs. Each one was then re-verified. | README, `git log` |
| **Edge cases**: unhappy paths end in a correct answer or a clean handoff | Simulator scenarios force each case: no availability, DMS outage, unverified caller, unintelligible speech, speech that never transcribes, unsupported request (pricing). | [agent/simulate.py](../agent/simulate.py) `SCENARIOS` |
| **Containment**: own per-journey targets | Every run reports containment, transfer rate, transfer correctness, and hallucination rate. | `simulate.py` summary output; `agent/sim_results_*.json` |
| **Staged rollouts** (1% → 100%) | Not built. See [gaps](#gaps-stated-plainly). | |
| **Safety and compliance**: tiered auth, verbatim language, anti-hallucination guardrails | **Auth:** Caller ID comes from Twilio, never from the conversation. Cancels re-check ownership in code. Bookings default to the verified number. **Guardrails:** the model never authors dates, phone digits, or verification status. Code computes them. The prompt forbids prices, hours, callbacks, and safety advice, and the simulator's judge enforces that. | `caller_id.py`, `cancel_appointment` / `book_appointment` / `add_spoken_times` in `bot.py` |
| **Handoffs** with accurate summaries and auth context | Handoff records split **code-owned facts** (verification status, every tool result) from **model-written text** (summary, caller-stated vehicle and symptom). Unverified callers are still logged. Shown on the dashboard. | `transfer_and_end_call` in `bot.py`; [dms/handoffs.py](../dms/handoffs.py); dashboard modal |
| **Testing**: every change ships with evidence it didn't break behavior | 33-call simulator with a gate: zero safety failures and ≥90% pass. Plus targeted evals and 44 DMS tests. Each change in the history was gated before deploy. | `simulate.py`, `eval_*.py`, `dms/test_*.py` |
| **Monitoring**: latency, errors, drop-offs | Per-stage latency is logged per call: STT, LLM time to first spoken token, TTS. Calls were diagnosed from production logs down to a 30 ms turn-taking race. No Grafana or alerting yet. | `latency_observer.py`; README findings table |
| **Voice tuning**: latency, fillers, interruptions | Thinking turned off after real calls showed 0.3–2.6 s per turn. Filler phrases cover tool calls only (measured to avoid double fillers). Barge-in verified. Turn-taking fixed for late STT finals and quiet speech. | `thinking_filler.py`, `turn_start.py`, `LLM_THINKING` in `bot.py` |

## What the role asks for

| Requirement | Evidence |
|---|---|
| How LLM agents behave: instructions, tool calls, retrieval, failure modes | The README's findings table is a catalogue of failure modes that were found and fixed, mostly by moving responsibility from prompt to code. |
| Evals for non-deterministic systems: test sets, simulations, LLM-as-judge, regression gates | `simulate.py`: an LLM caller, deterministic checks first, a calibrated judge only for what code can't check, and a gate. Its statistical limits are stated in the README. |
| Read API specs, REST integrations, authorization scopes | The agent reaches the DMS only through its REST API (ADR-0001). The DMS uses API-key auth. Ownership checks live server-side in code. |
| JavaScript/TypeScript | The Next.js and TypeScript dashboard, including the handoff summary view. |
| Ownership in production: rollouts, monitoring, incident response | Five Railway services. One real incident: an unpinned dependency crashed the DMS on redeploy. It was diagnosed from logs, fixed forward in about 5 minutes, and every service now pins its dependencies. |
| Clear writing | ADRs, commit messages that explain the why, and this document. |

## Gaps, stated plainly

- **No hands-on Sierra, Genesys, or no-code agent platform.** PitLine builds
  the same things those platforms provide (journeys, tool calls,
  simulations, handoff payloads) by hand.
- **No SumoLogic or Grafana.** Logs are structured and per-call latency is
  recorded, but there are no dashboards or alerts on top yet.
- **No GraphQL.** Tool integrations here are REST.
- **No fintech compliance experience** (UDAAP, FDCPA, TCPA, PCI). The
  guardrail patterns transfer, but the domain knowledge would be new.
- **Staged rollout and verbatim compliance language** aren't built yet.
- **Single channel (voice).** No chat or email journeys.
