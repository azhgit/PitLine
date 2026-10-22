"""Turn a real call into a simulator regression scenario: the call's own tool
results become the scripted ones, and a model drafts the caller persona and
what an agent following its instructions SHOULD have done - not what this
call did, or the scenario would only lock the failure in.

Real calls come from weekly_review.py's fix list. The draft lands in
real_call_scenarios.json, which simulate.py loads; read the diff and fix the
expect block before committing - the model drafts it, a person signs off.

    REVIEW_DMS_BASE_URL=https://... REVIEW_API_KEY=... python call_to_scenario.py <call_id>
`python call_to_scenario.py --check` runs the self-check, no network.
"""

import json
import os
import sys

import httpx
from anthropic import Anthropic
from dotenv import load_dotenv

from bot import PROMPT_VERSIONS
from call_transfer import TRANSFER_MESSAGES
from simulate import CALLER_BASICS, JUDGE_MODEL, OTP, REAL_CALL_SCENARIOS

load_dotenv(override=True)

# Only what the stub needs to replay a result; the DMS's tool_log is
# unredacted, and this file gets committed.
KEEP = {"slots", "outside_window", "verified", "appointments", "error", "today"}
APPOINTMENT_KEEP = {"id", "technician_name", "time_slot", "vehicle_make", "vehicle_model", "vehicle_year", "urgency", "status"}
# Results simulate.stub_tool already computes itself (step-up, bookings).
REPLAYED_TOOLS = {"check_availability", "find_appointment", "cancel_appointment"}

DRAFT_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reason": {"type": "string", "description": "What should have happened and why, before the fields below."},
            "persona": {"type": "string", "description": "Second-person role-play brief for the caller."},
            "terminal": {"type": "string", "enum": ["end_call", "transfer_and_end_call"]},
            "transfer_reason": {"anyOf": [{"type": "string", "enum": list(TRANSFER_MESSAGES)}, {"type": "null"}]},
            "book": {"anyOf": [{"type": "boolean"}, {"type": "null"}], "description": "true must book, false must not, null either is fine."},
            "cancel_id": {"anyOf": [{"type": "integer"}, {"type": "null"}], "description": "appointment id that must be cancelled, if any."},
            "contained": {"type": "boolean", "description": "Whether the correct outcome needs no human."},
        },
        "required": ["reason", "persona", "terminal", "transfer_reason", "book", "cancel_id", "contained"],
    },
}


def scrub(result: dict) -> dict:
    out = {k: v for k, v in result.items() if k in KEEP}
    if "appointments" in out:
        out["appointments"] = [{k: v for k, v in a.items() if k in APPOINTMENT_KEEP} for a in out["appointments"]]
    return out


def scripted_tools(tool_log: list[dict]) -> tuple[dict, dict | None]:
    """(tools, verified_find): each replayed tool's first result, plus the
    lookup after a passed step-up, which simulate.stub_tool switches to."""
    tools, verified_find, stepped_up = {}, None, False
    for t in tool_log:
        if t["tool"] == "check_verification_code" and (t.get("result") or {}).get("verified"):
            stepped_up = True
        elif t["tool"] == "find_appointment" and stepped_up:
            verified_find = scrub(t["result"])
        elif t["tool"] in REPLAYED_TOOLS and t["tool"] not in tools:
            tools[t["tool"]] = scrub(t.get("result") or {})
    return tools, verified_find


def to_scenario(call: dict, draft: dict) -> dict:
    tools, verified_find = scripted_tools(call["tool_log"])
    expect = {"terminal": draft["terminal"], "book": draft["book"]}
    if draft["transfer_reason"]:
        expect["reason"] = draft["transfer_reason"]
    if draft["cancel_id"] is not None:
        expect["cancel_id"] = draft["cancel_id"]
    scenario = {
        "name": f"real_call_{call['id']}",
        "persona": draft["persona"],
        "tools": tools,
        "expect": expect,
        "contained": draft["contained"],
        "source": f"call #{call['id']} ({call['prompt_version']}): {draft['reason']}",
    }
    if verified_find:
        scenario["verified_find"] = verified_find
    return scenario


def draft(client: Anthropic, call: dict) -> dict:
    instructions = PROMPT_VERSIONS.get(call["prompt_version"]) or PROMPT_VERSIONS["stable"]
    lines = "\n".join(f"{line['role'].upper()}: {line['text']}" for line in call["transcript"])
    prompt = (
        "This real call to a car dealership's AI phone agent went wrong. Turn it into a regression test.\n\n"
        f"Agent instructions:\n<instructions>\n{instructions}\n</instructions>\n\n"
        f"Transcript ([name]/[phone] are redactions):\n<transcript>\n{lines}\n</transcript>\n\n"
        f"Tool calls and results:\n<tools>\n{json.dumps([{**t, 'result': scrub(t.get('result') or {})} for t in call['tool_log']])}\n</tools>\n\n"
        f"Recorded outcome: {call['outcome']} {call['transfer_reason'] or ''}. Judge's review: {json.dumps(call['review'])}\n\n"
        "Write a persona so a simulated caller reproduces this caller: their goal, what they say, and how they "
        "react, in the second person. Use only details from the call. In place of redactions, use this caller "
        f"identity: {CALLER_BASICS} If they key digits on the keypad, have them reply exactly like DTMF: 7785550199#; "
        f"a texted verification code is always {OTP}.\n"
        "Then give the outcome an agent following its instructions SHOULD have reached - not what this agent did."
    )
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=4000,
        output_config={"format": DRAFT_SCHEMA},
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def save(scenario: dict, path: str = REAL_CALL_SCENARIOS) -> None:
    existing = json.load(open(path)) if os.path.exists(path) else []
    existing = [s for s in existing if s["name"] != scenario["name"]] + [scenario]
    with open(path, "w") as f:
        json.dump(existing, f, indent=2)
        f.write("\n")


def main(call_id: int):
    base_url = os.getenv("REVIEW_DMS_BASE_URL") or os.getenv("DMS_BASE_URL", "http://localhost:8000")
    response = httpx.get(f"{base_url}/calls", headers={"X-Review-Key": os.environ["REVIEW_API_KEY"]}, timeout=30)
    call = next((c for c in response.raise_for_status().json() if c["id"] == call_id), None)
    if call is None:
        sys.exit(f"no call #{call_id}")
    scenario = to_scenario(call, draft(Anthropic(max_retries=8), call))
    save(scenario)
    print(json.dumps(scenario, indent=2))
    print(f"\nwrote {scenario['name']} to {os.path.basename(REAL_CALL_SCENARIOS)}. Before committing, check:")
    print("  - expect is what SHOULD happen, per the 'source' line")
    print("  - tools can reach it: a call that never got past a step it should have passed has no result")
    print("    for that step (e.g. no verified_find after step-up) - add one by hand")


def _check():
    import tempfile

    appt = {"id": 7, "technician_name": "Dana Kim", "time_slot": "2026-10-23T09:00:00", "customer_name": "Andrew Kong", "customer_phone": "672-399-7007", "status": "booked"}
    log = [
        {"tool": "find_appointment", "args": {}, "result": {"verified": True, "appointments": []}},
        {"tool": "send_verification_code", "args": {"customer_phone": "6723997007"}, "result": {"sent": True}},
        {"tool": "check_verification_code", "args": {"code": "123456"}, "result": {"verified": True}},
        {"tool": "find_appointment", "args": {}, "result": {"verified": True, "appointments": [appt]}},
        {"tool": "check_availability", "args": {}, "result": {"slots": [], "calling_number": "672-399-7007", "today": "Thursday, October 22"}},
    ]
    tools, verified_find = scripted_tools(log)
    assert tools == {"find_appointment": {"verified": True, "appointments": []}, "check_availability": {"slots": [], "today": "Thursday, October 22"}}
    assert verified_find == {"verified": True, "appointments": [{k: v for k, v in appt.items() if k in APPOINTMENT_KEEP}]}
    call = {"id": 12, "prompt_version": "candidate", "tool_log": log}
    d = {"reason": "r", "persona": "p", "terminal": "end_call", "transfer_reason": None, "book": False, "cancel_id": 7, "contained": True}
    scenario = to_scenario(call, d)
    assert scenario["expect"] == {"terminal": "end_call", "book": False, "cancel_id": 7}
    assert "Andrew" not in json.dumps(scenario) and "7007" not in json.dumps(scenario)

    path = os.path.join(tempfile.mkdtemp(), "s.json")
    save(scenario, path)
    save({**scenario, "persona": "p2"}, path)  # same call again replaces, not duplicates
    assert [s["persona"] for s in json.load(open(path))] == ["p2"]
    print("ok")


if __name__ == "__main__":
    _check() if sys.argv[1:] == ["--check"] else main(int(sys.argv[1]))
