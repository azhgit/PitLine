import asyncio
from dataclasses import dataclass

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    SystemFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


@dataclass
class CloseCallRequestedFrame(SystemFrame):
    """Pushed by the end_call tool handler once the LLM has said a natural
    closing line (booking confirmed, or "someone will call back") and
    decided the call is over. CallCloseProcessor watches for this and ends
    the call shortly after - so the agent hangs up instead of sitting on
    the line waiting for the caller to do it.

    SystemFrame, not DataFrame: needs to reliably reach CallCloseProcessor
    even if an interruption happens around the same time, matching how the
    other custom control signaling in this codebase (UserStartedSpeakingFrame
    etc., which SilenceTimeoutProcessor/ThinkingFillerProcessor rely on) is
    also SystemFrame-based. The actual hangup (EndFrame) is separately
    uninterruptible by pipecat's own design regardless of this choice - see
    EndFrame's docstring.
    """

    pass


class CallCloseProcessor(FrameProcessor):
    """On CloseCallRequestedFrame: if the bot is still speaking its closing
    line, wait for it to finish (BotStoppedSpeakingFrame) before hanging
    up; if it's not currently speaking (already finished, or the tool
    result arrived before TTS even started), hang up after grace_secs
    directly rather than waiting for a BotStoppedSpeakingFrame that might
    not come. Either way, grace_secs is added before EndFrame to cover the
    same generation-vs-playback lag documented in silence_timeout.py's
    playback_grace_secs.
    """

    def __init__(self, *, grace_secs: float = 1.5, **kwargs):
        super().__init__(**kwargs)
        self._grace_secs = grace_secs
        self._closing = False
        self._bot_speaking = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, CloseCallRequestedFrame):
            if self._bot_speaking:
                self._closing = True
            else:
                self.create_task(self._hangup_after_grace())
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            if self._closing:
                self._closing = False
                self.create_task(self._hangup_after_grace())

        await self.push_frame(frame, direction)

    async def _hangup_after_grace(self):
        await asyncio.sleep(self._grace_secs)
        await self.push_frame(EndFrame(reason="call closed"), FrameDirection.DOWNSTREAM)
