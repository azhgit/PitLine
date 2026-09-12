"""Day 2 model comparison: does the dispatch system prompt actually get the
fixed urgency/specialty classification right, and how much slower is Sonnet
than Haiku on time-to-first-token?

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
    "Thanks for calling our after-hours plumbing and HVAC line. What's going on tonight?"
)


@dataclass
class Scenario:
    label: str
    utterance: str
    expected_urgency: str
    expected_specialty: str | None  # None = ambiguous by design, don't grade


SCENARIOS = [
    Scenario(
        "water_damage",
        "There's water pouring out from under my kitchen sink and it's flooding onto the floor!",
        "urgent",
        "plumbing",
    ),
    Scenario(
        "no_heat_freezing",
        "My heat's completely out and it's like 15 degrees outside, I'm worried about my pipes.",
        "urgent",
        "hvac",
    ),
    Scenario(
        "sewage_backup",
        "My toilet is overflowing and sewage is backing up into the bathtub.",
        "urgent",
        "plumbing",
    ),
    Scenario(
        "gas_smell",
        "I smell gas near my furnace.",
        "urgent",
        None,  # system prompt never says which trade a gas smell falls under
    ),
    Scenario(
        "dripping_faucet",
        "My bathroom faucet has been dripping for like a week, not urgent but annoying.",
        "non_urgent",
        "plumbing",
    ),
    Scenario(
        "no_hot_water",
        "We haven't had hot water since yesterday morning.",
        "non_urgent",
        "plumbing",
    ),
    Scenario(
        "rattling_ac",
        "My AC unit is making a weird rattling noise but it still cools fine.",
        "non_urgent",
        "hvac",
    ),
    Scenario(
        "routine_maintenance",
        "Just want to schedule the yearly furnace maintenance check.",
        "non_urgent",
        "hvac",
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
        "specialty": tool_use.input.get("specialty") if tool_use else None,
    }
    result["urgency_ok"] = tool_use is not None and result["urgency"] == scenario.expected_urgency
    result["specialty_ok"] = (
        scenario.expected_specialty is None or result["specialty"] == scenario.expected_specialty
    )
    return result


def main():
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    tools = AnthropicLLMAdapter().to_provider_tools_format(
        ToolsSchema(standard_tools=build_tool_schemas())
    )

    header = f"{'model':<8} {'scenario':<20} {'tool':<20} {'urgency':<10} {'specialty':<10} {'ttft_s':<8} ok"
    print(header)
    print("-" * len(header))

    for model_name, model_id in MODELS.items():
        for scenario in SCENARIOS:
            r = run_scenario(client, model_id, tools, scenario)
            ok = "OK" if (r["urgency_ok"] and r["specialty_ok"]) else "MISS"
            print(
                f"{model_name:<8} {r['scenario']:<20} {str(r['called_tool']):<20} "
                f"{str(r['urgency']):<10} {str(r['specialty']):<10} {r['ttft_s']:<8} {ok}"
            )


if __name__ == "__main__":
    main()
