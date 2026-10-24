import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    EndFrame,
    FunctionCallResultProperties,
    LLMMessagesAppendFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.dtmf_aggregator import DTMFAggregator
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import BaseTransport
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.workers.runner import WorkerRunner

import call_record
from call_close import CallCloseProcessor, CloseCallRequestedFrame
from call_transfer import TRANSFER_MESSAGES
from caller_id import lookup_caller_number, normalize_phone
from latency_observer import LatencyLogObserver
from rollout import pick_version
from silence_timeout import SilenceTimeoutProcessor
from step_up import StepUp, check_code, send_code
from thinking_filler import ThinkingFillerProcessor, ToolCallStartedFrame
from turn_start import LateFinalTolerantTranscriptionStart

load_dotenv(override=True)

DMS_BASE_URL = os.getenv("DMS_BASE_URL", "http://localhost:8000")


def _vad_params_from_env() -> VADParams:
    # Untuned - Silero's own defaults (confidence=0.7, start_secs=0.2,
    # stop_secs=0.2) were picked for generic mic audio, not 8kHz phone
    # audio, but tuning them for real needs live call testing we can't do
    # here. Exposed as env vars so that testing can iterate without a code
    # change; unset vars fall back to VADParams' own defaults.
    overrides = {}
    for field, env_var in (
        ("confidence", "VAD_CONFIDENCE"),
        ("start_secs", "VAD_START_SECS"),
        ("stop_secs", "VAD_STOP_SECS"),
    ):
        value = os.getenv(env_var)
        if value:
            overrides[field] = float(value)
    return VADParams(**overrides)

# Day 1: real service-department prompt with fixed urgency classification
# rules (see doc/pitline-plan.md and CONTEXT.md) - the caller never gets
# asked to self-classify, the agent decides from what they describe. There's
# no specialty choice for the agent to make: urgency alone picks the
# technician pool (Master Technician only for urgent, Quick Service first
# for non-urgent) - that's handled inside check_availability's DMS call, not
# something the model needs to reason about.
SYSTEM_INSTRUCTION = (
    "You are the after-hours service department line for a car dealership. "
    "Keep every reply to one or two short sentences - this is a phone call, "
    "not a chat.\n\n"
    "If any tool call's result has an 'error' field of 'dms_unavailable' or "
    "'booking_failed', that's a system problem, not something about what "
    "the caller said - don't retry the same tool call, don't guess a time "
    "or confirm a booking that didn't actually happen. Tell the caller "
    "you're having trouble reaching the system right now and someone will "
    "call them back shortly, then call end_call. This overrides every rule "
    "below - it can happen at any step.\n\n"
    # Added after agent/simulate.py's baseline caught each of these in
    # simulated calls (sim_results_baseline.json): "tomorrow" for a date
    # the agent can't place, invented callbacks/hours/staff, and its own
    # words spoken ahead of the fixed transfer line.
    "Never state a fact or make a promise that a tool result or these "
    "instructions didn't give you - no prices, business hours, phone "
    "numbers, staff, callbacks, or follow-ups beyond the ones described "
    "here (a cancellation callback is only ever offered when "
    "outside_window is true). Say a slot's day using its 'spoken' field "
    "(e.g. 'Tuesday, June 18 at 10am'). check_availability's 'today' is the "
    "dealership's current date - only say 'today' or 'tomorrow' when that "
    "field backs it up, even when echoing the caller. Don't give mechanical, diagnostic, "
    "or safety advice (like whether it's safe to drive) - that's the "
    "technician's call, not yours. If "
    "asked about pricing, say you don't have pricing on this line and the "
    "service advisor will go over cost at the appointment; offer to book, "
    "and if they don't want to, close the call. When you call "
    "transfer_and_end_call, say nothing yourself - it speaks the handoff "
    "line on its own. Never call end_call before the caller has spoken.\n\n"
    "If the caller wants to check, reschedule, or cancel an EXISTING "
    "appointment (not describing a new problem), call find_appointment "
    "first - it looks up the caller's own record automatically off the "
    "real calling number, so don't ask them for their phone number for "
    "this. If it comes back unverified, call transfer_and_end_call with "
    "reason 'cannot_verify_caller' right away - verification only works "
    "through find_appointment, don't try asking for their phone number as "
    "a substitute. If verified with no appointment found, say so, then "
    "fall through to the normal flow below if they still want service. If "
    "verified with an appointment found, read back its 'spoken' time and "
    "technician, then ask whether they want to reschedule or cancel it:\n"
    "   - Cancel: get an explicit yes, call cancel_appointment with that "
    "appointment_id, confirm it's cancelled, then call end_call.\n"
    "   - Reschedule: run the normal check_availability + book_appointment "
    "flow below to book the new slot FIRST. Only after that booking "
    "succeeds, call cancel_appointment on the old appointment_id, confirm "
    "the change, then call end_call. Booking the new slot before "
    "cancelling the old one means they're never left with nothing if the "
    "new booking fails.\n\n"
    "For every caller describing a new problem:\n"
    "1. Ask what's wrong with the vehicle if they haven't said, then "
    "classify the urgency from their description. Use this fixed category "
    "list, don't improvise:\n"
    "   - urgency 'urgent': brakes that are grinding, have a soft pedal, "
    "or have their warning light on; steering or suspension failure, an "
    "active fluid leak, overheating or smoke, or an airbag warning light.\n"
    "   - urgency 'non_urgent': routine maintenance (oil change, tire "
    "rotation, inspection), a non-dangerous noise - including a light "
    "brake squeak with none of the urgent brake signs - or an infotainment/"
    "electrical nuisance - anything not on the urgent list above.\n"
    "   If what they said doesn't make sense - including an "
    "'[inaudible: ...]' message, which means they spoke but nothing could "
    "be transcribed - ask them to repeat it once. If the very next reply is "
    "still unclear, you MUST call transfer_and_end_call with reason "
    "'cannot_understand_caller' - don't ask a third time or keep "
    "guessing.\n"
    "2. Call check_availability with that urgency. Its result has 'slots' "
    "and 'outside_window':\n"
    "   - slots non-empty, outside_window false: read them back as "
    "concrete options using each slot's 'spoken' field word for word (e.g. "
    "'Tuesday, June 18 at 2pm with Mike, or Wednesday, June 19 at 10am with "
    "Dana') - never work out a day or time yourself. Continue to step 3.\n"
    "   - slots non-empty, outside_window true: nothing's open in the next "
    "week - tell the caller that, offer the one returned slot as the "
    "nearest available time, and mention someone will call back sooner if "
    "a cancellation opens up. Continue to step 3 only if they want that "
    "slot; otherwise call end_call.\n"
    "   - slots empty (only possible for urgent - non_urgent always finds "
    "something): call transfer_and_end_call with reason "
    "'urgent_no_availability'.\n"
    "3. Once the caller picks a slot, get their name and vehicle "
    "make/model/year, and ask whether the number they're calling from is "
    "the best one to reach them. For that number, leave customer_phone out "
    "of book_appointment - it's filled in from the verified calling number, "
    "so never ask them to say it. check_availability's 'calling_number' is "
    "that verified number (null if unknown - then ask for their number); "
    "read it back from that field digit for digit, never from memory. Only "
    "if they want a different number, "
    "collect it and pass it as customer_phone. Read the name, vehicle, and "
    "number back (the calling_number digits, or the digits they gave) and "
    "get an explicit yes before booking - STT can mishear a "
    "name, a digit, or a vehicle detail and there'd be no other way to "
    "catch it. If book_appointment returns error 'phone_needed', ask for "
    "their number and call it again with customer_phone. Only after they "
    "confirm, call book_appointment with that exact slot, read back the "
    "confirmed time and technician name to close the call, then call "
    "end_call.\n"
    "Call end_call right after any of these natural conclusions - don't "
    "just stop talking and wait for the caller to hang up.\n"
)
def add_spoken_times(items: list[dict]) -> list[dict]:
    """Adds 'spoken' ("Tuesday, June 18 at 10am") to each item with a
    time_slot. Computed here, not by the model: with thinking off, Haiku
    5.5 read 2030-06-18 back as "Thursday" in simulate.py runs.
    """
    for item in items:
        dt = datetime.fromisoformat(item["time_slot"])
        minutes = f":{dt:%M}" if dt.minute else ""
        item["spoken"] = f"{dt:%A, %B} {dt.day} at {dt.hour % 12 or 12}{minutes}{dt:%p}".replace("AM", "am").replace("PM", "pm")
    return items


# Same as dms/dealership_tz.py - slot times are dealership wall-clock time.
DEALERSHIP_TZ = ZoneInfo("America/Vancouver")


def spoken_today() -> str:
    """The dealership's current date, so "tomorrow" is a checkable fact
    rather than the model's guess - it echoed a caller's "tomorrow" in
    simulate.py runs despite a prompt rule against it.
    """
    now = datetime.now(DEALERSHIP_TZ)
    return f"{now:%A, %B} {now.day}"


# Haiku 5.5 thinks by default and Pipecat only auto-disables that for
# Sonnet. A real call on 2026-10-21 measured 0.3-2.6s of thinking per turn
# on top of a ~0.6s first byte; simulate.py with thinking off held 96%.
# Shared with simulate.py so the suite tests what production runs.
LLM_THINKING = "disabled"
# Stands in for a user turn that had speech but no transcript - shared with
# simulate.py. See on_user_turn_stopped in run_bot.
INAUDIBLE_MESSAGE = "[inaudible: the caller spoke but nothing could be transcribed]"
# Staged rollout of prompt changes (see rollout.py). To ship a change: put
# the new prompt in "candidate", gate it with SIM_PROMPT_VERSION=candidate
# python simulate.py, then ramp CANDIDATE_PERCENT on Railway (1 -> 10 ->
# 50 -> 100) while comparing prompt_version in the logs and handoffs;
# finally promote it to "stable" and clear "candidate". Rollback is
# CANDIDATE_PERCENT=0 - no deploy of code.
_STABLE_UNVERIFIED_RULE = (
    "If it comes back unverified, call transfer_and_end_call with "
    "reason 'cannot_verify_caller' right away - verification only works "
    "through find_appointment, don't try asking for their phone number as "
    "a substitute. If verified with no appointment found, say so, then "
)
_STABLE_READBACK = "collect it and pass it as customer_phone. Read the name, vehicle, and "
assert _STABLE_UNVERIFIED_RULE in SYSTEM_INSTRUCTION and _STABLE_READBACK in SYSTEM_INSTRUCTION
# Candidate: SMS step-up (step_up.py) instead of transferring every caller
# whose Caller ID doesn't match, and a consent question before the
# confirmation text. Only these two rules differ from stable.
CANDIDATE_INSTRUCTION = SYSTEM_INSTRUCTION.replace(
    _STABLE_UNVERIFIED_RULE,
    "If it comes back unverified, or verified with no appointment found "
    "while the caller says it's booked under a different number, offer to "
    "text a code to the number the appointment is under. Ask them to key "
    "that number in on their phone's keypad and press pound - keyed digits "
    "reach you as 'DTMF: ...'; never take the number spoken. Pass those "
    "digits to send_verification_code. Once it's sent, ask them to key in "
    "the code from the text the same way and pass it to "
    "check_verification_code. If it returns verified true, call "
    "find_appointment again and carry on below. If verified false with "
    "attempts_left above 0, ask them to key it in once more; at 0, call "
    "transfer_and_end_call with reason 'verification_failed'. If they "
    "can't or won't get the text, or either verification tool returns an "
    "error, call transfer_and_end_call with reason 'cannot_verify_caller'. "
    "A number they say or key in is never verification by itself - only "
    "check_verification_code is. If verified with no appointment found "
    "otherwise, say so, then ",
).replace(
    _STABLE_READBACK,
    "collect it and pass it as customer_phone. Ask whether it's OK to text "
    "a confirmation to that number, and pass sms_consent true only if they "
    "say yes - false if they say no or don't want texts. Read the name, vehicle, and ",
)
PROMPT_VERSIONS: dict[str, str | None] = {"stable": SYSTEM_INSTRUCTION, "candidate": CANDIDATE_INSTRUCTION}

KICKOFF_MESSAGE = "The caller just connected."
# Spoken by code on connect, never generated: disclosure language (automated
# assistant, recording) has to be word for word, which a model can't
# guarantee. Also skips an LLM round trip before the caller hears anything.
GREETING = (
    "Thanks for calling the after-hours service department. You're speaking "
    "with an automated assistant, and this call may be recorded. What's going "
    "on with your vehicle?"
)


def build_tool_schemas() -> list[FunctionSchema]:
    """Schema-only tool definitions (no handlers attached here - those are
    wired separately via llm.register_function in run_bot). Factored out to
    a module-level function so eval_dispatch_classification.py can send the
    LLM the exact same tool definitions the live pipeline uses.
    """
    return [
        FunctionSchema(
            name="check_availability",
            description=(
                "Look up available appointment slots for the given urgency. The "
                "DMS picks the eligible technician pool itself (Master Technician "
                "only for urgent, Quick Service first for non-urgent) - just pass "
                "urgency, not a technician type. Returns 'slots' (a short list of "
                "concrete slots to read back to the caller verbatim) and "
                "'outside_window' (true if these slots are past the normal "
                "one-week schedule - the Callback promise case)."
            ),
            properties={
                "urgency": {
                    "type": "string",
                    "enum": ["urgent", "non_urgent"],
                    "description": "Urgency classified per the fixed dispatch rules.",
                },
            },
            required=["urgency"],
        ),
        FunctionSchema(
            name="book_appointment",
            description=(
                "Book one of the slots previously returned by check_availability "
                "for the caller."
            ),
            properties={
                "technician_id": {
                    "type": "integer",
                    "description": "technician_id from the chosen check_availability slot.",
                },
                "time_slot": {
                    "type": "string",
                    "description": (
                        "ISO 8601 timestamp of the chosen slot, exactly as returned "
                        "by check_availability."
                    ),
                },
                "customer_name": {"type": "string"},
                "customer_phone": {
                    "type": "string",
                    "description": (
                        "Only if the caller wants a number other than the one "
                        "they're calling from - omit it to use the verified "
                        "calling number."
                    ),
                },
                "vehicle_make": {"type": "string"},
                "vehicle_model": {"type": "string"},
                "vehicle_year": {"type": "integer"},
                "urgency": {
                    "type": "string",
                    "enum": ["urgent", "non_urgent"],
                },
                "sms_consent": {
                    "type": "boolean",
                    "description": (
                        "Whether the caller said yes to a text confirmation. "
                        "false sends no text."
                    ),
                },
            },
            required=[
                "technician_id",
                "time_slot",
                "customer_name",
                "vehicle_make",
                "vehicle_model",
                "vehicle_year",
                "urgency",
            ],
        ),
        FunctionSchema(
            name="find_appointment",
            description=(
                "Look up any existing booked appointment for this caller, "
                "verified against the real calling phone number - never a "
                "number the caller states. Call this before doing anything else "
                "when they want to check, reschedule, or cancel an existing "
                "appointment. Takes no arguments."
            ),
            properties={},
            required=[],
        ),
        FunctionSchema(
            name="cancel_appointment",
            description=(
                "Cancel one existing appointment, by the appointment_id returned "
                "from find_appointment. Only succeeds if it belongs to this "
                "verified caller."
            ),
            properties={
                "appointment_id": {
                    "type": "integer",
                    "description": "appointment_id from a find_appointment result.",
                },
            },
            required=["appointment_id"],
        ),
        FunctionSchema(
            name="send_verification_code",
            description=(
                "Text a one-time code to the number the caller's appointment "
                "is under, when find_appointment couldn't match their calling "
                "number. Only with digits they keyed in."
            ),
            properties={
                "customer_phone": {
                    "type": "string",
                    "description": "The digits from the caller's 'DTMF: ...' entry, exactly as keyed.",
                },
            },
            required=["customer_phone"],
        ),
        FunctionSchema(
            name="check_verification_code",
            description=(
                "Check the code the caller keyed in from the text. On "
                "verified true, find_appointment and cancel_appointment work "
                "for the number the code was sent to."
            ),
            properties={
                "code": {
                    "type": "string",
                    "description": "The digits from the caller's 'DTMF: ...' entry, exactly as keyed.",
                },
            },
            required=["code"],
        ),
        FunctionSchema(
            name="transfer_and_end_call",
            description=(
                "Speak a fixed line for the given reason, then end the call - "
                "the Handoff simulation from CONTEXT.md. No real transfer "
                "happens. Use this instead of continuing the conversation once "
                "one of the listed situations applies. Call it with NO text of "
                "your own in the same reply: it speaks the handoff line itself, "
                "and anything you write would be spoken to the caller first."
            ),
            properties={
                "reason": {
                    "type": "string",
                    "enum": list(TRANSFER_MESSAGES.keys()),
                    "description": "Why the call is being transferred.",
                },
                "summary": {
                    "type": "string",
                    "description": (
                        "One or two sentences for the service advisor taking "
                        "over: what the caller needs and what's already been "
                        "tried. Only what the caller said or a tool returned - "
                        "if they couldn't be understood, say exactly that "
                        "rather than guessing what they wanted. Never include "
                        "a verification code, right or wrong."
                    ),
                },
                "vehicle": {
                    "type": "string",
                    "description": "Make/model/year as the caller stated it, if they did.",
                },
                "symptom": {
                    "type": "string",
                    "description": "The problem in the caller's words, if they described one.",
                },
            },
            required=["reason", "summary"],
        ),
        FunctionSchema(
            name="end_call",
            description=(
                "End the call after a natural conclusion - a confirmed booking, "
                "or telling a non-urgent caller someone will call back. Call this "
                "right after saying the closing line so the call actually hangs "
                "up instead of sitting there waiting for the caller to do it. "
                "Don't use this for transfers - use transfer_and_end_call instead."
            ),
            properties={},
            required=[],
        ),
    ]


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    call_sid = (getattr(runner_args, "call_data", None) or {}).get("call_id")
    prompt_version = pick_version(
        call_sid,
        int(os.getenv("CANDIDATE_PERCENT", "0")),
        has_candidate=PROMPT_VERSIONS["candidate"] is not None,
    )
    logger.info(f"call {call_sid}: prompt_version={prompt_version}")

    llm = AnthropicLLMService(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        settings=AnthropicLLMService.Settings(
            model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5"),
            system_instruction=PROMPT_VERSIONS[prompt_version],
            thinking=AnthropicLLMService.ThinkingConfig(type=LLM_THINKING),
            # Not enable_prompt_caching=True: measured with eval_prompt_caching.py
            # against the real API - Haiku 4.5 needs >=4096 tokens to actually
            # cache anything, and SYSTEM_INSTRUCTION + tools + a turn is ~2155
            # (re-measured after Day 1's prompt rewrite - still well under).
            # Confirmed via cache_creation_input_tokens/cache_read_input_tokens
            # in the response (both 0), not just inferred from timing. Revisit
            # only if the prompt grows past that floor for other reasons.
        ),
    )
    stt = DeepgramSTTService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        settings=DeepgramSTTService.Settings(model="nova-3-general"),
    )
    tts = ElevenLabsTTSService(
        api_key=os.getenv("ELEVENLABS_API_KEY"),
        settings=ElevenLabsTTSService.Settings(
            voice=os.getenv("ELEVENLABS_VOICE_ID"),
            model=os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5"),
        ),
    )

    dms_client = httpx.AsyncClient(
        base_url=DMS_BASE_URL,
        timeout=5.0,
        headers={"X-API-Key": os.getenv("DMS_API_KEY", "")},
    )

    # Lazy, per-call cache for the Twilio-verified Caller ID - looked up at
    # most once per call (only if a booking or lookup actually needs it, not
    # on every call) since it costs a Twilio REST round trip. A dict cell
    # rather than a bare variable so the closures below can both read and
    # write it without `nonlocal`.
    _caller_number_cache: dict[str, str | None] = {}
    # One-shot flag: set when cancel_appointment verifies+cancels an old
    # appointment, consumed by the very next book_appointment call so only
    # that rebook - not any unrelated future booking in the same call -
    # skips the DMS's per-phone rate limit. See dms/appointments.py.
    _reschedule_state = {"exempt_next_booking": False}
    # Every tool result this call, recorded by code (see logged() below) so
    # a handoff's record of what was tried never depends on the LLM.
    _tool_log: list[dict] = []
    _step_up = StepUp()

    def logged(name, handler):
        async def wrapper(params: FunctionCallParams):
            original = params.result_callback

            async def record(result, **kwargs):
                _tool_log.append({"tool": name, "args": params.arguments, "result": result})
                await original(result, **kwargs)

            params.result_callback = record
            await handler(params)

        return wrapper

    async def get_verified_caller_number() -> str | None:
        # A passed step-up replaces Caller ID for the rest of the call, so
        # lookups, cancel's ownership check, and a reschedule's rebook all
        # follow the phone the code proved - no second ownership path.
        if _step_up.verified_phone:
            return _step_up.verified_phone
        if "value" not in _caller_number_cache:
            _caller_number_cache["value"] = await lookup_caller_number(call_sid)
        return _caller_number_cache["value"]

    async def check_availability(params: FunctionCallParams):
        # Arms ThinkingFillerProcessor for this DMS round trip - see
        # ToolCallStartedFrame's docstring for why it's pushed here rather
        # than on every LLM turn.
        await llm.push_frame(ToolCallStartedFrame(), FrameDirection.DOWNSTREAM)
        urgency = params.arguments["urgency"]
        try:
            response = await dms_client.get("/availability", params={"urgency": urgency})
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"DMS availability lookup failed: {e}")
            await params.result_callback({"error": "dms_unavailable"})
            return
        # {"slots": [...], "outside_window": bool} - see dms/availability.py.
        result = response.json()
        add_spoken_times(result["slots"])
        # Verified digits for the booking readback, so the model never
        # transcribes a phone number itself. Called before every booking.
        caller = normalize_phone(await get_verified_caller_number() or "")
        result["calling_number"] = f"{caller[:3]}-{caller[3:6]}-{caller[6:]}" if len(caller) == 10 else None
        result["today"] = spoken_today()
        await params.result_callback(result)

    async def book_appointment(params: FunctionCallParams):
        await llm.push_frame(ToolCallStartedFrame(), FrameDirection.DOWNSTREAM)
        args = params.arguments
        # The verified calling number by default, not digits the LLM
        # transcribed: a real call on 2026-10-21 heard "672 022 9889" and
        # booked "672-202-2989" after a readback the caller said yes to.
        phone = args.get("customer_phone") or await get_verified_caller_number()
        if not phone:
            await params.result_callback({"error": "phone_needed"})
            return
        headers = {}
        if _reschedule_state["exempt_next_booking"]:
            headers["X-Skip-Rate-Limit"] = "true"
            _reschedule_state["exempt_next_booking"] = False
        try:
            response = await dms_client.post(
                "/appointments",
                json={
                    "technician_id": args["technician_id"],
                    "time_slot": args["time_slot"],
                    "customer_name": args["customer_name"],
                    "customer_phone": phone,
                    "vehicle_make": args["vehicle_make"],
                    "vehicle_model": args["vehicle_model"],
                    "vehicle_year": args["vehicle_year"],
                    "urgency": args["urgency"],
                    # Omitted means the prompt never asked (stable), which
                    # keeps the DMS's default of texting, as before.
                    "sms_consent": args.get("sms_consent", True),
                },
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            # Includes 409 slot-conflict responses from the DMS - surfaced to
            # the LLM as a generic failure rather than a distinct "recheck
            # availability" signal. Fine for now; revisit if this shows up in
            # real calls.
            logger.error(f"DMS booking failed: {e}")
            await params.result_callback({"error": "booking_failed"})
            return
        await params.result_callback({"appointment": response.json()})

    async def find_appointment(params: FunctionCallParams):
        await llm.push_frame(ToolCallStartedFrame(), FrameDirection.DOWNSTREAM)
        caller_number = await get_verified_caller_number()
        if not caller_number:
            await params.result_callback({"verified": False})
            return
        try:
            response = await dms_client.get(
                "/appointments", params={"customer_phone": caller_number, "status": "booked"}
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"DMS appointment lookup failed: {e}")
            await params.result_callback({"error": "dms_unavailable"})
            return
        await params.result_callback({"verified": True, "appointments": add_spoken_times(response.json())})

    async def cancel_appointment(params: FunctionCallParams):
        await llm.push_frame(ToolCallStartedFrame(), FrameDirection.DOWNSTREAM)
        appointment_id = params.arguments["appointment_id"]
        caller_number = await get_verified_caller_number()
        if not caller_number:
            await params.result_callback({"verified": False})
            return
        try:
            response = await dms_client.get(f"/appointments/{appointment_id}")
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"DMS appointment lookup failed: {e}")
            await params.result_callback({"error": "dms_unavailable"})
            return
        # Re-verified here, in code, against the real appointment record -
        # not just trusted from find_appointment's result - so a caller
        # can't talk the LLM into cancelling an appointment_id that isn't
        # theirs by simply asking for a different number.
        if normalize_phone(response.json()["customer_phone"]) != normalize_phone(caller_number):
            logger.warning(
                f"cancel_appointment: appointment {appointment_id} does not belong to "
                "the verified caller"
            )
            await params.result_callback({"error": "not_your_appointment"})
            return
        try:
            response = await dms_client.patch(
                f"/appointments/{appointment_id}", params={"status": "cancelled"}
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"DMS cancellation failed: {e}")
            await params.result_callback({"error": "dms_unavailable"})
            return
        _reschedule_state["exempt_next_booking"] = True
        await params.result_callback({"cancelled": True})

    async def send_verification_code(params: FunctionCallParams):
        await llm.push_frame(ToolCallStartedFrame(), FrameDirection.DOWNSTREAM)
        phone = params.arguments["customer_phone"]
        if error := _step_up.send_error(phone):
            await params.result_callback({"error": error})
            return
        digits = normalize_phone(phone)
        if not await send_code(digits):
            await params.result_callback({"error": "sms_unavailable"})
            return
        _step_up.record_send(digits)
        await params.result_callback({"sent": True})

    async def check_verification_code(params: FunctionCallParams):
        await llm.push_frame(ToolCallStartedFrame(), FrameDirection.DOWNSTREAM)
        if error := _step_up.check_error():
            await params.result_callback({"error": error})
            return
        approved = await check_code(_step_up.phone, re.sub(r"\D", "", params.arguments["code"]))
        await params.result_callback(_step_up.record_check(approved))

    async def transfer_and_end_call(params: FunctionCallParams):
        # Speaks the transfer line and ends the call directly, rather than
        # letting the LLM narrate its own transfer message and hoping to
        # catch "it finished speaking" later - same push-then-EndFrame
        # pattern SilenceTimeoutProcessor uses for its goodbye, which keeps
        # frame ordering (audio before hangup) a single actor's problem.
        reason = params.arguments["reason"]
        message = TRANSFER_MESSAGES[reason]
        await llm.push_frame(TTSSpeakFrame(text=message), FrameDirection.DOWNSTREAM)
        # Logs the Handoff simulation to the DMS, which enqueues its
        # Follow-up job (handoff SMS) in the same DB transaction - see
        # dms/handoffs.py and ADR-0002. Best-effort: a DMS error is logged,
        # not a reason to fail the call itself.
        # Logged even unverified (customer_phone None, no SMS) - that's
        # the handoff the advisor most needs to see. Verification and
        # tool_results come from code; only summary/caller_stated are the
        # LLM's words, and are labelled that way.
        caller_number = await get_verified_caller_number()
        args = params.arguments
        summary = {
            "summary": args.get("summary"),
            "caller_stated": {"vehicle": args.get("vehicle"), "symptom": args.get("symptom")},
            "verification": {
                "caller_id_verified": bool(caller_number),
                "method": "sms_otp" if _step_up.verified_phone else "twilio_caller_id",
            },
            "tool_results": _tool_log,
            "prompt_version": prompt_version,
        }
        try:
            response = await dms_client.post(
                "/handoffs",
                json={"customer_phone": caller_number, "reason": reason, "summary": summary},
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"DMS handoff logging failed: {e}")
        await llm.push_frame(EndFrame(reason=reason), FrameDirection.DOWNSTREAM)
        await params.result_callback(
            {"transferred": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    async def end_call(params: FunctionCallParams):
        # Unlike transfer_and_end_call, the closing line here is the LLM's
        # own normal text response (already flowing through the regular
        # llm->tts path), not a fixed phrase we push ourselves - this tool
        # just signals "hang up after that line finishes." CallCloseProcessor
        # does the actual waiting/hangup once this frame reaches it.
        await llm.push_frame(CloseCallRequestedFrame(), FrameDirection.DOWNSTREAM)
        await params.result_callback(
            {"closing": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    llm.register_function("check_availability", logged("check_availability", check_availability))
    llm.register_function("book_appointment", logged("book_appointment", book_appointment))
    llm.register_function("find_appointment", logged("find_appointment", find_appointment))
    llm.register_function("cancel_appointment", logged("cancel_appointment", cancel_appointment))
    llm.register_function("send_verification_code", logged("send_verification_code", send_verification_code))
    llm.register_function("check_verification_code", logged("check_verification_code", check_verification_code))
    llm.register_function("transfer_and_end_call", logged("transfer_and_end_call", transfer_and_end_call))
    llm.register_function("end_call", logged("end_call", end_call))

    tools = build_tool_schemas()

    # Seeded as a "user" turn, not "system": Anthropic's context requires the
    # message list to start with a real conversational role. GREETING joins
    # the context as the assistant's turn once on_client_connected speaks it
    # (TTSSpeakFrame appends to context by default).
    context = LLMContext([{"role": "user", "content": KICKOFF_MESSAGE}], tools=tools)
    # Hard-stop barge-in (plan's Day 3 requirement) is Pipecat's own default
    # here, not something built for this project: the VAD-based turn-start
    # strategy has enable_interruptions=True by default, and
    # TTSService._handle_interruption discards all pending/queued state on
    # InterruptionFrame rather than buffering it for resume - verified by
    # reading pipecat's source (turns/user_start/base_user_turn_start_strategy.py,
    # services/tts_service.py), not by a live call. Would need
    # user_turn_strategies=... here to change.
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=_vad_params_from_env()),
            # VAD start plus a transcription start that ignores the late
            # final of the turn that just ended - see turn_start.py for the
            # two real-call failures this balances. Stop strategy stays the
            # default.
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy(), LateFinalTolerantTranscriptionStart()]
            ),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            # Keypad entry for step-up, so phone numbers and codes never go
            # through STT. Emits "DTMF: <digits>" as a transcript on # or
            # after the idle timeout.
            # ponytail: 3s idle is a guess at how long callers pause mid-number;
            # tune from real calls. A DTMF-only turn has no speech for
            # smart-turn to end, so the worst case is the 5s user_turn_stop_timeout.
            DTMFAggregator(timeout=3.0),
            user_aggregator,  # emits UserStartedSpeakingFrame - must come before the watcher below
            # Must come before llm/tts: its check-in/goodbye TTSSpeakFrame and
            # EndFrame are pushed downstream and need to pass through both to
            # actually be synthesized. It still sees Bot*SpeakingFrame despite
            # sitting upstream of tts/transport.output() - those are emitted
            # there in both directions, and tts/llm forward the upstream copy
            # back through here (transports/base_output.py's _bot_started/
            # stopped_speaking, tts_service.py's BotStartedSpeakingFrame
            # handling, and AnthropicLLMService's else-branch passthrough).
            SilenceTimeoutProcessor(),
            llm,
            # Must come after llm, before tts: check_availability/
            # book_appointment push ToolCallStartedFrame from llm's own
            # position (flows downstream from there, so a processor sitting
            # upstream of llm would never see it), and its own injected
            # TTSSpeakFrame needs to reach tts to actually be synthesized -
            # sitting after tts like CallCloseProcessor below would bypass
            # tts entirely. Still sees Bot*SpeakingFrame via tts's upstream
            # forwarding (confirmed reliable, see the comment above).
            ThinkingFillerProcessor(),
            tts,
            # Unlike the two watchers above, this doesn't inject its own
            # TTSSpeakFrame (only EndFrame, which doesn't need to pass
            # through tts), so it sits after tts for direct access to
            # transport.output()'s upstream-broadcast Bot*SpeakingFrame.
            # end_call's CloseCallRequestedFrame (pushed from llm's
            # position) still reaches it fine flowing downstream through tts.
            CallCloseProcessor(),
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,  # Twilio Media Streams: 8kHz mulaw
            audio_out_sample_rate=8000,
            enable_metrics=True,  # feeds the TTFB/TTFA frames LatencyLogObserver reads
            enable_usage_metrics=True,
        ),
        observers=[LatencyLogObserver(prompt_version=prompt_version)],
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    # A turn VAD heard but STT turned into no text ends with nothing to
    # send, so the LLM never runs and the caller hears silence - 56s of it
    # on a real call (2026-10-21) where every unclear utterance was dropped
    # this way. Feed it to the LLM as an explicit inaudible turn so the
    # prompt's ask-once-then-transfer rule applies.
    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message):
        if not (message.content or "").strip():
            await worker.queue_frames(
                [
                    LLMMessagesAppendFrame(
                        messages=[{"role": "user", "content": INAUDIBLE_MESSAGE}], run_llm=True
                    )
                ]
            )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Twilio stream connected - speaking greeting")
        await worker.queue_frames([TTSSpeakFrame(text=GREETING)])

    _recorded = {"done": False}

    async def record_call():
        # Called from both the caller hanging up (on_client_disconnected) and
        # the pipeline ending after the agent hangs up (on_pipeline_finished)
        # - whichever comes first writes the row.
        if _recorded["done"]:
            return
        _recorded["done"] = True
        # One row per call for real-traffic containment and transcript
        # review (review_calls.py). Outcome comes from the tool log, not the
        # model; the transcript has the booked name and phone numbers
        # redacted. Best-effort: a failed write never affects the call.
        try:
            caller_number = await get_verified_caller_number()
            digits = normalize_phone(caller_number or "")
            outcome, transfer_reason = call_record.classify(_tool_log)
            terms = call_record.redaction_terms(_tool_log, caller_number)
            response = await dms_client.post(
                "/calls",
                json={
                    "call_sid": call_sid,
                    "outcome": outcome,
                    "transfer_reason": transfer_reason,
                    "prompt_version": prompt_version,
                    "caller_verified": bool(caller_number),
                    "caller_last4": digits[-4:] or None,
                    "transcript": call_record.transcript(context.get_messages(), KICKOFF_MESSAGE, terms),
                    "tool_log": _tool_log,
                },
            )
            response.raise_for_status()
            logger.info(f"call {call_sid}: outcome={outcome} prompt_version={prompt_version}")
        except Exception as e:
            logger.error(f"call record write failed: {e}")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await record_call()
        await worker.cancel()
        await dms_client.aclose()

    @worker.event_handler("on_pipeline_finished")
    async def on_pipeline_finished(worker, frame):
        await record_call()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint, force_gc=True)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    transport_params = {
        "twilio": lambda: FastAPIWebsocketParams(audio_in_enabled=True, audio_out_enabled=True),
    }
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


def _cli_proxy_arg() -> str | None:
    """Reads -x/--proxy from argv directly, before pipecat's own main()
    parses it - the TwilioWebhookAuthMiddleware needs the same proxy
    hostname pipecat would otherwise embed in its own (now bypassed, see
    twilio_auth.py) TwiML template.
    """
    import sys

    for flag in ("-x", "--proxy"):
        if flag in sys.argv:
            i = sys.argv.index(flag)
            if i + 1 < len(sys.argv):
                return sys.argv[i + 1]
    return None


if __name__ == "__main__":
    import pipecat.runner.run as pipecat_run
    from pipecat.runner.run import main

    from twilio_auth import TwilioWebhookAuthMiddleware

    # Not passed via --ws-auth: this is read by pipecat's own argparse
    # default (PIPECAT_WEBSOCKET_AUTH env var) inside main(), so it has to
    # be set before main() runs, same requirement as the CLI flag would have.
    os.environ.setdefault("PIPECAT_WEBSOCKET_AUTH", "token")

    # Railway auto-injects this for any service with a generated domain;
    # falls back to argv for local ngrok dev, where it isn't set.
    proxy = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or _cli_proxy_arg() or ""
    if not proxy:
        logger.warning(
            "No public proxy hostname found (RAILWAY_PUBLIC_DOMAIN unset, no -x/--proxy "
            "arg) - Twilio's TwiML response will point at an empty wss:// host."
        )
    pipecat_run.app.add_middleware(TwilioWebhookAuthMiddleware, proxy=proxy)

    main()
