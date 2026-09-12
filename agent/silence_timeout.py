import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    StartFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

CHECK_IN_MESSAGE = "Are you still there?"
GOODBYE_MESSAGE = (
    "I haven't heard from you in a while, so I'll go ahead and end the call "
    "now. Feel free to call back anytime."
)


class SilenceTimeoutProcessor(FrameProcessor):
    """~8s with no caller response after the bot finishes speaking -> bot
    checks in once. Another ~8s of silence after that -> bot says goodbye
    and ends the call.

    The waiting window only starts once the bot has actually stopped
    speaking (BotStoppedSpeakingFrame) and is paused for the entire span
    from the caller starting to talk (UserStartedSpeakingFrame) through
    the bot's next BotStoppedSpeakingFrame - covering both the caller's
    own speech and the LLM/TTS generation time that follows it, not just
    audio playback (BotStartedSpeakingFrame..BotStoppedSpeakingFrame).
    Otherwise a slow turn - caller talking, or the bot thinking, or the
    bot speaking - eats into the caller's silence budget, and the
    check-in can fire mid-turn instead of after real caller silence.
    The escalation counter (_triggers) only resets on the caller actually
    speaking (UserStartedSpeakingFrame), not on BotStoppedSpeakingFrame -
    otherwise the check-in's own TTS turn would reset it and stage 2
    (goodbye) would never be reachable.

    BotStoppedSpeakingFrame itself fires when the TTS service finishes
    *generating* audio (on TTSStoppedFrame, see pipecat's
    transports/base_output.py), not when the transport has finished
    *sending/playing* it - for a longer reply, generation typically
    finishes before playback does, so the window would otherwise start
    while the caller can still hear the bot talking. playback_grace_secs
    adds a one-off buffer to the first wait after the bot stops, as a
    best-effort compensation (not a precise fix - there's no signal here
    for "the caller has actually finished hearing this").
    """

    def __init__(
        self, *, timeout_secs: float = 8.0, playback_grace_secs: float = 1.5, **kwargs
    ):
        super().__init__(**kwargs)
        self._timeout_secs = timeout_secs
        self._playback_grace_secs = playback_grace_secs
        self._next_wait_secs = timeout_secs
        self._triggers = 0
        # Starts True so the watcher can't fire before the bot's first turn
        # even begins - the very first BotStoppedSpeakingFrame is what
        # actually opens the caller-silence window (see class docstring).
        self._bot_turn_active = True
        self._activity_event = asyncio.Event()
        self._timeout_task = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._timeout_task = self.create_task(self._watch())
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._triggers = 0
            self._next_wait_secs = self._timeout_secs
            # Closes the window for the caller's own speech and the LLM/TTS
            # generation time that follows it; only the next
            # BotStoppedSpeakingFrame reopens it.
            self._bot_turn_active = True
            self._activity_event.set()
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_turn_active = True
            self._activity_event.set()  # don't let a timeout land mid-speech
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_turn_active = False
            # First wait after a bot turn gets extra slack for any audio
            # still draining out; later waits during genuine silence don't.
            self._next_wait_secs = self._timeout_secs + self._playback_grace_secs
            self._activity_event.set()

        await self.push_frame(frame, direction)

    async def cleanup(self):
        if self._timeout_task:
            await self.cancel_task(self._timeout_task)

    async def _watch(self):
        while True:
            wait_secs = self._next_wait_secs
            self._next_wait_secs = self._timeout_secs
            try:
                await asyncio.wait_for(self._activity_event.wait(), timeout=wait_secs)
                self._activity_event.clear()
            except TimeoutError:
                if self._bot_turn_active:
                    # The caller just spoke, or a long bot turn is still in
                    # progress (thinking or talking) - not caller silence,
                    # just keep waiting for it to finish.
                    continue
                self._triggers += 1
                if self._triggers == 1:
                    await self.push_frame(
                        TTSSpeakFrame(text=CHECK_IN_MESSAGE), FrameDirection.DOWNSTREAM
                    )
                else:
                    await self.push_frame(
                        TTSSpeakFrame(text=GOODBYE_MESSAGE), FrameDirection.DOWNSTREAM
                    )
                    await self.push_frame(
                        EndFrame(reason="silence timeout"), FrameDirection.DOWNSTREAM
                    )
                    return
