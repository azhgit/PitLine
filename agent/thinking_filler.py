import asyncio
import random
from dataclasses import dataclass

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    Frame,
    StartFrame,
    SystemFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

FILLER_PHRASES = [
    "Let me check on that...",
    "One moment...",
    "Let me look into that for you...",
]


@dataclass
class ToolCallStartedFrame(SystemFrame):
    """Pushed by a tool handler (see check_availability/book_appointment in
    bot.py) right as it starts a DMS round trip, so ThinkingFillerProcessor
    knows a tool-call gap - not just any LLM thinking time - is starting.

    Why only tool calls, not every LLM turn: checked empirically
    (see the eval_dispatch_classification.py-style probe run this session)
    that Haiku's own first-turn text already says something like "Let me
    check what we have available" before it calls a tool - so an earlier
    version of this processor that armed on UserStoppedSpeakingFrame (any
    caller-finished-talking gap) would sometimes fire its own filler phrase
    right on top of that, sounding redundant right before the real answer.
    The tool-call round trip (DMS HTTP call + a second LLM turn to produce
    the spoken result) is the gap that ISN'T already covered by the model's
    own transitional text, so that's what this now specifically targets.
    """

    pass


class ThinkingFillerProcessor(FrameProcessor):
    """If the bot hasn't started speaking again within delay_secs of a tool
    call starting, speak one short filler phrase - masks the DMS round trip
    + the second LLM turn that produces the real spoken result, without
    duplicating the transitional text the model already says before the
    tool call itself (see ToolCallStartedFrame's docstring for why that
    distinction matters).

    Fires at most once per tool call: disarms on BotStartedSpeakingFrame
    (the real response is coming, no filler needed) and on
    UserStartedSpeakingFrame (the caller is talking again, don't talk over
    them). The only way to re-arm is the next ToolCallStartedFrame, so
    firing and re-arming are the same _armed flag by construction - no
    separate "already fired this turn" state needed.
    """

    def __init__(
        self, *, delay_secs: float = 1.2, phrases: list[str] | None = None, **kwargs
    ):
        super().__init__(**kwargs)
        self._delay_secs = delay_secs
        self._phrases = phrases or list(FILLER_PHRASES)
        self._armed = False
        self._activity_event = asyncio.Event()
        self._watch_task = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._watch_task = self.create_task(self._watch())
        elif isinstance(frame, ToolCallStartedFrame):
            self._armed = True
            self._activity_event.set()
        elif isinstance(frame, (BotStartedSpeakingFrame, UserStartedSpeakingFrame)):
            self._armed = False
            self._activity_event.set()

        await self.push_frame(frame, direction)

    async def cleanup(self):
        if self._watch_task:
            await self.cancel_task(self._watch_task)

    async def _watch(self):
        while True:
            while not self._armed:
                await self._activity_event.wait()
                self._activity_event.clear()
            try:
                await asyncio.wait_for(self._activity_event.wait(), timeout=self._delay_secs)
                self._activity_event.clear()
                # Disarmed before the delay elapsed (bot started for real, or
                # the caller started talking again) - loop back to the outer
                # wait, which will go idle since _armed is now False.
            except TimeoutError:
                if self._armed:
                    self._armed = False  # single-fire-per-tool-call reset
                    await self.push_frame(
                        TTSSpeakFrame(text=random.choice(self._phrases)),
                        FrameDirection.DOWNSTREAM,
                    )
