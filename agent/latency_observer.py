import json
import time
import uuid
from pathlib import Path

from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import TTFAMetricsData, TTFBMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed

LATENCY_LOG_PATH = Path(__file__).parent / "latency_log.jsonl"


class LatencyLogObserver(BaseObserver):
    """Appends STT/LLM/TTS time-to-first-byte metrics to a JSONL file, one
    row per pipeline stage per turn. Pipecat's own MetricsLogObserver only
    prints to the console - this persists the same data (which stage,
    how many ms, which call) so Day 4 has something to chart.
    """

    def __init__(self):
        super().__init__()
        self._call_id = uuid.uuid4().hex[:8]
        self._frames_seen = set()

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if not isinstance(frame, MetricsFrame):
            return
        if frame.id in self._frames_seen:
            return
        self._frames_seen.add(frame.id)

        for metrics_data in frame.data:
            if isinstance(metrics_data, TTFBMetricsData):
                self._write(
                    stage="ttfb",
                    processor=metrics_data.processor,
                    model=metrics_data.model,
                    value_s=metrics_data.value,
                )
            elif isinstance(metrics_data, TTFAMetricsData):
                self._write(
                    stage="ttfa",
                    processor=metrics_data.processor,
                    model=metrics_data.model,
                    value_s=metrics_data.ttfa,
                    leading_silence_s=metrics_data.leading_silence,
                )

    def _write(self, **fields):
        row = {"call_id": self._call_id, "wall_time": time.time(), **fields}
        with open(LATENCY_LOG_PATH, "a") as f:
            f.write(json.dumps(row) + "\n")
