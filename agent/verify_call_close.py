"""Verifies CallCloseProcessor against a real minimal Pipecat pipeline - no
API keys or network calls needed. Run manually: python -u verify_call_close.py
"""

import asyncio
import time

from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, EndFrame, Frame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from call_close import CallCloseProcessor, CloseCallRequestedFrame


class Sink(FrameProcessor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.frames = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self.frames.append(frame)
        await self.push_frame(frame, direction)


async def run_scenario(name: str, body):
    G = 0.3
    processor = CallCloseProcessor(grace_secs=G)
    sink = Sink()
    pipeline = Pipeline([processor, sink])
    worker = PipelineWorker(pipeline, params=PipelineParams(), idle_timeout_secs=None)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    await asyncio.sleep(0.05)

    try:
        await body(worker, sink, G)
        print(f"[PASS] {name}", flush=True)
        ok = True
    except AssertionError as e:
        print(f"[FAIL] {name}: {e}", flush=True)
        ok = False
    finally:
        # Some scenarios already end the pipeline themselves via EndFrame;
        # queuing another is harmless if it already stopped.
        try:
            await worker.queue_frames([EndFrame()])
        except Exception:
            pass
        try:
            await asyncio.wait_for(run_task, timeout=2)
        except (asyncio.CancelledError, TimeoutError, Exception) as e:
            print(f"  (cleanup note: {e!r})", flush=True)
    return ok


def end_frames(sink):
    return [f for f in sink.frames if isinstance(f, EndFrame)]


async def scenario_waits_for_bot_to_stop(worker, sink, G):
    ref = time.monotonic()
    await worker.queue_frames([BotStartedSpeakingFrame()])
    await asyncio.sleep(0.05)
    await worker.queue_frames([CloseCallRequestedFrame()])

    # Bot is still "speaking" - must not hang up yet even past grace_secs.
    await asyncio.sleep(G * 1.5)
    assert len(end_frames(sink)) == 0, "hung up while bot was still speaking"

    await worker.queue_frames([BotStoppedSpeakingFrame()])
    await asyncio.sleep(G * 0.5)
    assert len(end_frames(sink)) == 0, "hung up before grace_secs elapsed after bot stopped"

    await asyncio.sleep(G * 0.7)
    assert len(end_frames(sink)) == 1, "expected exactly one EndFrame after grace_secs"
    elapsed = time.monotonic() - ref
    assert elapsed >= G, f"EndFrame arrived too early ({elapsed:.3f}s)"


async def scenario_hangs_up_when_bot_already_quiet(worker, sink, G):
    ref = time.monotonic()
    # No BotStartedSpeakingFrame at all - bot isn't speaking when the close
    # request arrives (e.g. text finished generating/playing very fast).
    await worker.queue_frames([CloseCallRequestedFrame()])

    await asyncio.sleep(G * 0.5)
    assert len(end_frames(sink)) == 0, "hung up before grace_secs elapsed"

    await asyncio.sleep(G * 0.7)
    assert len(end_frames(sink)) == 1, "expected EndFrame after grace_secs with no bot speech"
    elapsed = time.monotonic() - ref
    assert elapsed >= G


async def scenario_no_close_requested_no_hangup(worker, sink, G):
    await worker.queue_frames([BotStartedSpeakingFrame()])
    await asyncio.sleep(0.05)
    await worker.queue_frames([BotStoppedSpeakingFrame()])
    await asyncio.sleep(G * 2)
    assert len(end_frames(sink)) == 0, "should never hang up without a close request"


async def main():
    results = []
    results.append(
        await run_scenario("waits for bot to stop, then grace, then EndFrame", scenario_waits_for_bot_to_stop)
    )
    results.append(
        await run_scenario(
            "bot already quiet -> hangs up after grace directly", scenario_hangs_up_when_bot_already_quiet
        )
    )
    results.append(
        await run_scenario("no close requested -> never hangs up", scenario_no_close_requested_no_hangup)
    )

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed", flush=True)


asyncio.run(main())
