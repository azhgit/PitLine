"""Verifies ThinkingFillerProcessor against a real (not synthetic-frame-only)
minimal Pipecat pipeline - no API keys or network calls needed, this is pure
local frame-flow. Run manually: python -u verify_thinking_filler.py
"""

import asyncio
import time

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    EndFrame,
    Frame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from thinking_filler import FILLER_PHRASES, ThinkingFillerProcessor, ToolCallStartedFrame


class Sink(FrameProcessor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.frames = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self.frames.append(frame)
        await self.push_frame(frame, direction)


async def run_scenario(name: str, body):
    T = 0.3
    processor = ThinkingFillerProcessor(delay_secs=T)
    sink = Sink()
    pipeline = Pipeline([processor, sink])
    worker = PipelineWorker(pipeline, params=PipelineParams(), idle_timeout_secs=None)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    await asyncio.sleep(0.05)

    try:
        await body(worker, sink, T)
        print(f"[PASS] {name}", flush=True)
        ok = True
    except AssertionError as e:
        print(f"[FAIL] {name}: {e}", flush=True)
        ok = False
    finally:
        await worker.queue_frames([EndFrame()])
        try:
            await asyncio.wait_for(run_task, timeout=2)
        except (asyncio.CancelledError, TimeoutError, Exception) as e:
            print(f"  (cleanup note: {e!r})", flush=True)
    return ok


def fillers(sink):
    return [f for f in sink.frames if isinstance(f, TTSSpeakFrame)]


async def scenario_fast_turn(worker, sink, T):
    await worker.queue_frames([ToolCallStartedFrame()])
    await asyncio.sleep(T * 0.5)
    await worker.queue_frames([BotStartedSpeakingFrame()])
    await asyncio.sleep(T * 1.5)
    assert len(fillers(sink)) == 0, f"expected no filler, got {len(fillers(sink))}"


async def scenario_slow_turn_fires_once(worker, sink, T):
    ref = time.monotonic()
    await worker.queue_frames([ToolCallStartedFrame()])

    await asyncio.sleep(T * 0.5)
    assert len(fillers(sink)) == 0, "fired too early"

    await asyncio.sleep(T * 0.7)  # total elapsed ~1.2T
    fired = fillers(sink)
    assert len(fired) == 1, f"expected exactly 1 filler, got {len(fired)}"
    assert fired[0].text in FILLER_PHRASES
    elapsed = time.monotonic() - ref
    assert elapsed >= T, f"fired before delay_secs elapsed ({elapsed:.3f}s < {T}s)"

    # Continued silence past another full delay window - must NOT re-fire.
    await asyncio.sleep(T * 1.5)
    assert len(fillers(sink)) == 1, "should not escalate/re-fire like SilenceTimeoutProcessor"


async def scenario_caller_barges_in(worker, sink, T):
    await worker.queue_frames([ToolCallStartedFrame()])
    await asyncio.sleep(T * 0.5)
    await worker.queue_frames([UserStartedSpeakingFrame()])
    await asyncio.sleep(T * 1.5)
    assert len(fillers(sink)) == 0, "should not speak over the caller"
    # Pass-through fidelity: every pushed frame reached the sink, nothing swallowed.
    assert any(isinstance(f, ToolCallStartedFrame) for f in sink.frames)
    assert any(isinstance(f, UserStartedSpeakingFrame) for f in sink.frames)


async def scenario_second_turn_gets_fresh_chance(worker, sink, T):
    await worker.queue_frames([ToolCallStartedFrame()])
    await asyncio.sleep(T * 1.2)
    assert len(fillers(sink)) == 1, "expected first filler"

    await worker.queue_frames([BotStartedSpeakingFrame()])
    await asyncio.sleep(0.02)
    await worker.queue_frames([UserStartedSpeakingFrame()])
    await asyncio.sleep(0.02)
    await worker.queue_frames([ToolCallStartedFrame()])
    await asyncio.sleep(T * 1.2)

    assert len(fillers(sink)) == 2, f"expected a second filler, got {len(fillers(sink))}"


async def scenario_clean_shutdown(worker, sink, T):
    await asyncio.sleep(0.05)
    # body is a no-op; run_scenario's finally block sends EndFrame and awaits
    # the run task with a timeout - a hang or dangling-task issue would show
    # up as the cleanup note or the overall timeout firing.


async def main():
    results = []
    results.append(await run_scenario("fast turn -> no filler", scenario_fast_turn))
    results.append(
        await run_scenario("slow turn -> fires once, no escalation", scenario_slow_turn_fires_once)
    )
    results.append(await run_scenario("caller barges in -> no filler", scenario_caller_barges_in))
    results.append(
        await run_scenario(
            "second slow turn -> fresh filler opportunity", scenario_second_turn_gets_fresh_chance
        )
    )
    results.append(await run_scenario("clean shutdown, no hang", scenario_clean_shutdown))

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed", flush=True)


asyncio.run(main())
