import asyncio
import time

from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start import TranscriptionUserTurnStartStrategy

# Comfortably past the ~30ms late-final gap measured on real calls, well
# short of a caller's next utterance after a pause.
LATE_FINAL_WINDOW_SECS = 1.5


class LateFinalTolerantTranscriptionStart(TranscriptionUserTurnStartStrategy):
    """Pipecat's transcription-based turn start, minus the false starts.

    Two failure modes pulled against each other on real calls (2026-10-21):

    - With the stock TranscriptionUserTurnStartStrategy, Deepgram's last
      final landed ~30ms after smart-turn ended the turn and was read as a
      new turn - interrupting the reply that had just started, then timing
      out with the caller's words lost.
    - With VAD-only turn start, a caller holding the phone away said "yes"
      too quietly for VAD and sat in 22s of silence until the check-in.

    So transcripts still start a turn (catching speech VAD misses), except
    within LATE_FINAL_WINDOW_SECS of the user stopping - that's the tail of
    the turn that just ended, which the aggregation keeps anyway.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_stop = float("-inf")

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, (VADUserStoppedSpeakingFrame, UserStoppedSpeakingFrame)):
            self._last_stop = time.monotonic()
            return ProcessFrameResult.CONTINUE
        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            if time.monotonic() - self._last_stop < LATE_FINAL_WINDOW_SECS:
                return ProcessFrameResult.CONTINUE
        return await super().process_frame(frame)


if __name__ == "__main__":

    async def _check():
        s = LateFinalTolerantTranscriptionStart()
        started = []

        async def fake_trigger():
            started.append(True)

        s.trigger_user_turn_started = fake_trigger
        final = TranscriptionFrame(text="yes", user_id="", timestamp="")

        # A late final right after the turn ended: ignored.
        await s.process_frame(VADUserStoppedSpeakingFrame())
        assert await s.process_frame(final) == ProcessFrameResult.CONTINUE and not started

        # A quiet "yes" long after: starts a turn.
        s._last_stop -= LATE_FINAL_WINDOW_SECS + 0.1
        assert await s.process_frame(final) == ProcessFrameResult.STOP and started
        print("ok")

    asyncio.run(_check())
