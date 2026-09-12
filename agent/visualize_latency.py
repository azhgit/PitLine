"""Day 4: turn agent/latency_log.jsonl (written by LatencyLogObserver during
real calls) into a bar chart of average time-to-first-byte/audio per pipeline
stage, in ms. Run manually after at least one real call: python
visualize_latency.py
"""

import json
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")  # headless-friendly backend for PNG output
import matplotlib.pyplot as plt

from latency_observer import LATENCY_LOG_PATH

STAGE_ORDER = ["STT", "LLM", "TTS"]

# Matched by vendor prefix, not by searching for "STT"/"TTS" as a substring
# of the class name: "ElevenLabsTTSService" contains the run "STTS", which
# contains "STT" as a substring, so a naive substring check checking for
# "STT" before "TTS" would misclassify it as STT.
PROCESSOR_PREFIXES = {
    "Deepgram": "STT",
    "Anthropic": "LLM",
    "ElevenLabs": "TTS",
}


def friendly_stage(processor: str) -> str:
    for prefix, stage in PROCESSOR_PREFIXES.items():
        if processor.startswith(prefix):
            return stage
    return processor


def load_rows() -> list[dict]:
    if not LATENCY_LOG_PATH.exists():
        return []
    rows = []
    with open(LATENCY_LOG_PATH, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"Skipping malformed JSON on line {line_no} in {LATENCY_LOG_PATH}")
    return rows


def main():
    rows = load_rows()
    if not rows:
        print(
            f"No data in {LATENCY_LOG_PATH} yet - run a real call first "
            "(LatencyLogObserver writes to it during agent/bot.py's pipeline)."
        )
        return

    by_stage_metric = defaultdict(list)
    for row in rows:
        processor = row.get("processor")
        value_s = row.get("value_s")
        if processor is None or value_s is None:
            continue
        by_stage_metric[(friendly_stage(processor), str(row.get("stage", "unknown")).upper())].append(
            value_s * 1000
        )

    ordered_groups = [
        group for stage in STAGE_ORDER for group in sorted(by_stage_metric) if group[0] == stage
    ] + [
        group for group in sorted(by_stage_metric) if group[0] not in STAGE_ORDER
    ]
    labels = [f"{stage} ({metric})" for stage, metric in ordered_groups]
    averages = [sum(by_stage_metric[group]) / len(by_stage_metric[group]) for group in ordered_groups]
    call_count = len({row.get("call_id") for row in rows if row.get("call_id")})

    fig, ax = plt.subplots(figsize=(6, 4))
    bars = ax.bar(labels, averages, color=[plt.cm.tab10(i % 10) for i in range(len(labels))])
    for bar, avg in zip(bars, averages):
        ax.annotate(
            f"{avg:.0f}ms",
            xy=(bar.get_x() + bar.get_width() / 2, avg),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
        )
    ax.set_ylabel("Avg latency (ms)")
    ax.set_title(f"Pipeline latency by stage ({call_count} call(s), {len(rows)} samples)")
    fig.tight_layout()

    out_path = LATENCY_LOG_PATH.parent / "latency_chart.png"
    fig.savefig(out_path, dpi=150)
    print(f"Wrote {out_path} from {len(rows)} samples across {call_count} call(s).")


if __name__ == "__main__":
    main()
