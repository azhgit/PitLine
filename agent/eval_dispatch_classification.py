"""Day 1/2 model comparison: does the service-department system prompt
actually get the fixed Urgent/Non-urgent classification right (CONTEXT.md's
category list), and how much slower is Sonnet than Haiku on time-to-first
-token?

There's no specialty to grade here (unlike the old plumbing/HVAC version) -
technician type is derived from urgency alone inside the DMS, not classified
by the model. See dms/availability.py and CONTEXT.md's Technician entry.

Text-only - approximates what STT would have handed the LLM, not a real
call. Costs a small amount of real API spend each run (~16 short requests).
Run manually: python eval_dispatch_classification.py
"""

import os
import time
from dataclasses import dataclass

from anthropic import Anthropic
from dotenv import load_dotenv
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.adapters.services.anthropic_adapter import AnthropicLLMAdapter

from bot import SYSTEM_INSTRUCTION, build_tool_schemas

load_dotenv(override=True)

MODELS = {
    "haiku": os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5"),
    "sonnet": "claude-sonnet-5",
}

# A plausible hardcoded greeting stands in for turn 1 (which we're not
# grading) so every scenario starts from the same two-turn context the real
# pipeline would have by the time the caller states their problem.
GREETING = (
    "Thanks for calling our dealership's after-hours service department. "
    "What's going on with your vehicle?"
)


@dataclass
class Scenario:
    label: str
    utterance: str
    expected_urgency: str


# One scenario per CONTEXT.md category, on both sides of the Urgent/
# Non-urgent line.
SCENARIOS = [
    Scenario(
        "brake_grinding",
        "My brakes have been grinding really badly the last two days.",
        "urgent",
    ),
    Scenario(
        "soft_brake_pedal",
        "My brake pedal feels really soft, like it's sinking to the floor.",
        "urgent",
    ),
    Scenario(
        "steering_failure",
        "My steering wheel is shaking violently and feels loose.",
        "urgent",
    ),
    Scenario(
        "fluid_leak",
        "There's a big puddle of fluid under my car this morning, it's actively dripping.",
        "urgent",
    ),
    Scenario(
        "overheating",
        "My engine temperature gauge is pegged in the red and there's smoke coming from the hood.",
        "urgent",
    ),
    Scenario(
        "airbag_light",
        "My airbag warning light just came on and stayed on.",
        "urgent",
    ),
    Scenario(
        "oil_change",
        "Just want to schedule my regular oil change.",
        "non_urgent",
    ),
    Scenario(
        "tire_rotation",
        "I'd like to get my tires rotated, nothing urgent.",
        "non_urgent",
    ),
    Scenario(
        "inspection",
        "I need to get my annual inspection done sometime this week.",
        "non_urgent",
    ),
    Scenario(
        "minor_noise",
        "There's a little squeak coming from one of my doors, not a big deal.",
        "non_urgent",
    ),
    Scenario(
        "infotainment",
        "My touchscreen radio keeps freezing up, kind of annoying but the car drives fine.",
        "non_urgent",
    ),
]


def run_scenario(client: Anthropic, model_id: str, tools: list[dict], scenario: Scenario) -> dict:
    messages = [
        {"role": "user", "content": "The caller just connected."},
        {"role": "assistant", "content": GREETING},
        {"role": "user", "content": scenario.utterance},
    ]

    start = time.monotonic()
    first_event_at = None
    with client.messages.stream(
        model=model_id,
        max_tokens=300,
        system=SYSTEM_INSTRUCTION,
        tools=tools,
        messages=messages,
    ) as stream:
        for _ in stream:
            if first_event_at is None:
                first_event_at = time.monotonic()
        final = stream.get_final_message()

    tool_use = next((b for b in final.content if b.type == "tool_use"), None)

    result = {
        "scenario": scenario.label,
        "ttft_s": round(first_event_at - start, 3),
        "called_tool": tool_use.name if tool_use else None,
        "urgency": tool_use.input.get("urgency") if tool_use else None,
    }
    result["urgency_ok"] = (
        tool_use is not None
        and tool_use.name == "check_availability"
        and result["urgency"] == scenario.expected_urgency
    )
    return result


def main():
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    tools = AnthropicLLMAdapter().to_provider_tools_format(
        ToolsSchema(standard_tools=build_tool_schemas())
    )

    header = f"{'model':<8} {'scenario':<16} {'tool':<20} {'urgency':<10} {'ttft_s':<8} ok"
    print(header)
    print("-" * len(header))

    for model_name, model_id in MODELS.items():
        for scenario in SCENARIOS:
            r = run_scenario(client, model_id, tools, scenario)
            ok = "OK" if r["urgency_ok"] else "MISS"
            print(
                f"{model_name:<8} {r['scenario']:<16} {str(r['called_tool']):<20} "
                f"{str(r['urgency']):<10} {r['ttft_s']:<8} {ok}"
            )


if __name__ == "__main__":
    main()
