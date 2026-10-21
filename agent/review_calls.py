"""Run simulate.py's judge over real calls: pulls every unreviewed call
from the DMS, asks the same judge model with the same schema whether the
caller's problem was handled and whether the agent stated anything no tool
or instruction supported, and writes the verdict back. The DMS's
/calls/stats then reports judged containment and hallucination rate for
real traffic next to the code-decided outcome.

Real calls have no scripted expected outcome, so the judge is asked what an
agent following its instructions should have done for this caller.

Needs REVIEW_API_KEY (transcripts aren't readable with DMS_API_KEY). Costs
real API spend. Against the deployed DMS:
    REVIEW_DMS_BASE_URL=https://... REVIEW_DMS_API_KEY=... REVIEW_API_KEY=... python review_calls.py
"""

import json
import os

import httpx
from anthropic import Anthropic
from dotenv import load_dotenv

from bot import PROMPT_VERSIONS, build_tool_schemas
from simulate import JUDGE_MODEL, JUDGE_SCHEMA

load_dotenv(override=True)

# REVIEW_* first: load_dotenv(override=True) (here and in bot.py) clobbers a
# shell-set DMS_BASE_URL/DMS_API_KEY with .env's local values, and reviews
# usually target the deployed DMS.
DMS_BASE_URL = os.getenv("REVIEW_DMS_BASE_URL") or os.getenv("DMS_BASE_URL", "http://localhost:8000")
DMS_API_KEY = os.getenv("REVIEW_DMS_API_KEY") or os.getenv("DMS_API_KEY")


def judge_call(client: Anthropic, tool_docs: str, call: dict) -> dict:
    instructions = PROMPT_VERSIONS.get(call["prompt_version"]) or PROMPT_VERSIONS["stable"]
    lines = "\n".join(f"{line['role'].upper()}: {line['text']}" for line in call["transcript"])
    tools_used = "\n".join(json.dumps(t) for t in call["tool_log"])
    prompt = (
        "You are auditing one real call handled by a car dealership's AI phone agent.\n\n"
        f"Agent instructions:\n<instructions>\n{instructions}\n</instructions>\n\n"
        f"Agent's tool descriptions (also count as things it was told):\n<tool_docs>\n{tool_docs}\n</tool_docs>\n\n"
        f"Outcome recorded by code: {call['outcome']}"
        + (f" ({call['transfer_reason']})" if call["transfer_reason"] else "")
        + f". Caller ID verified: {call['caller_verified']}.\n\n"
        f"Tool calls and their results (the only facts the agent had):\n<tools>\n{tools_used or '(none)'}\n</tools>\n\n"
        f"What was said aloud ([name]/[phone] are redactions, not agent errors):\n<transcript>\n{lines}\n</transcript>\n\n"
        "There is no scripted expected outcome. For 'resolved', judge whether the call ended where an agent "
        "following its instructions should have taken this caller - a correct transfer counts as resolved. "
        "If the caller hung up before anything could be done, it is not resolved, but that is not the agent's "
        "hallucination. Judge only what the AGENT said and did."
    )
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=4000,
        output_config={"format": JUDGE_SCHEMA, "effort": "low"},
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def main():
    review_key = os.environ["REVIEW_API_KEY"]
    dms = httpx.Client(base_url=DMS_BASE_URL, headers={"X-Review-Key": review_key}, timeout=30)
    calls = dms.get("/calls", params={"unreviewed": True}).raise_for_status().json()
    if not calls:
        print("no unreviewed calls")
        return

    client = Anthropic(max_retries=8)
    tool_docs = "\n".join(f"- {s.name}: {s.description}" for s in build_tool_schemas())
    for call in calls:
        verdict = judge_call(client, tool_docs, call)
        dms.patch(f"/calls/{call['id']}/review", json=verdict).raise_for_status()
        flags = ("UNRESOLVED " if not verdict["resolved"] else "") + ("HALLUCINATED" if verdict["hallucinated"] else "")
        print(f"  #{call['id']:<4} {call['outcome']:<20} {flags or 'ok':<24} {verdict['reason'][:110]}")

    stats = httpx.get(
        f"{DMS_BASE_URL}/calls/stats", headers={"X-API-Key": DMS_API_KEY}, timeout=30
    ).raise_for_status().json()
    print(f"\n  {json.dumps(stats, indent=2)}")


if __name__ == "__main__":
    main()
