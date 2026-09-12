"""Does Anthropic prompt caching help this agent's LLM latency?

Confirmed result: no, not at the current prompt size. Haiku 4.5 requires
>=4096 tokens before it caches anything; SYSTEM_INSTRUCTION + tools + a
turn is ~1400. bot.py deliberately does NOT set enable_prompt_caching -
see the comment there.

Sends the same SYSTEM_INSTRUCTION + tools + first user turn as two
separate requests with a cache_control marker on the system block
(mirroring what pipecat's AnthropicLLMAdapter does by marking the last
two user messages - marking the system block directly is simpler to
verify standalone). Anthropic reports exact cache_creation/cache_read
token counts in usage, so this checks the real counters, not just timing
noise - re-run this if the prompt grows past the 4096-token floor for
other reasons, to confirm whether caching is worth turning on then.
Costs a small amount of real API spend. Run manually:
python eval_prompt_caching.py
"""

import os
import time

from anthropic import Anthropic
from dotenv import load_dotenv
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.adapters.services.anthropic_adapter import AnthropicLLMAdapter

from bot import SYSTEM_INSTRUCTION, build_tool_schemas

load_dotenv(override=True)

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
USER_TURN = "My bathroom faucet has been dripping for about a week."


def cached_system_block():
    return [
        {
            "type": "text",
            "text": SYSTEM_INSTRUCTION,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def run_once(client, tools, label: str):
    start = time.monotonic()
    first_event_at = None
    with client.messages.stream(
        model=MODEL,
        max_tokens=300,
        system=cached_system_block(),
        tools=tools,
        messages=[{"role": "user", "content": USER_TURN}],
    ) as stream:
        for _ in stream:
            if first_event_at is None:
                first_event_at = time.monotonic()
        final = stream.get_final_message()

    ttft = first_event_at - start
    usage = final.usage
    print(
        f"{label:<12} ttft={ttft:.3f}s  "
        f"cache_creation_input_tokens={usage.cache_creation_input_tokens}  "
        f"cache_read_input_tokens={usage.cache_read_input_tokens}  "
        f"input_tokens={usage.input_tokens}"
    )
    return ttft, usage


def main():
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    tools = AnthropicLLMAdapter().to_provider_tools_format(
        ToolsSchema(standard_tools=build_tool_schemas())
    )

    print(f"model={MODEL}\n")
    ttft1, usage1 = run_once(client, tools, "1st call")
    # Small gap to mimic a caller's real thinking pause between turns,
    # well inside the ~5min ephemeral cache TTL.
    time.sleep(2)
    ttft2, usage2 = run_once(client, tools, "2nd call")

    print()
    if usage1.cache_creation_input_tokens and usage2.cache_read_input_tokens:
        print(
            f"Cache confirmed: call 1 wrote {usage1.cache_creation_input_tokens} tokens, "
            f"call 2 read {usage2.cache_read_input_tokens} tokens from cache."
        )
        print(f"TTFT: {ttft1:.3f}s -> {ttft2:.3f}s ({(1 - ttft2 / ttft1) * 100:.0f}% faster)")
    else:
        print(
            "Cache did NOT engage as expected (check cache_creation/cache_read counts above) - "
            "possibly the prompt is under Anthropic's minimum cacheable length for this model."
        )


if __name__ == "__main__":
    main()
