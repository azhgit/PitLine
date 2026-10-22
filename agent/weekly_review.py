"""The weekly read of real calls: containment per journey and per prompt
version, then a fix list - every call that didn't end contained or that the
judge flagged, grouped by journey and what went wrong, biggest group first.
Run review_calls.py first so the judge's verdicts are in.

Prints Markdown. Reads transcripts' metadata, so it needs REVIEW_API_KEY:
    REVIEW_DMS_BASE_URL=https://... REVIEW_API_KEY=... python weekly_review.py [days] [out.md]
`python weekly_review.py --check` runs the self-check, no network.
"""

import os
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx

CONTAINED = {"booked", "cancelled", "resolved_no_action"}  # same as dms/calls.py


def pct(n: int, d: int) -> str:
    return f"{round(100 * n / d)}%" if d else "-"


def failure(call: dict) -> str | None:
    """Why this call belongs on the fix list, or None. Hallucination outranks
    everything; then a judge overturning a contained call; then whatever
    kept it from being contained."""
    review = call["review"] or {}
    if review.get("hallucinated"):
        return "hallucinated"
    if call["outcome"] in CONTAINED:
        return "judged unresolved" if review.get("resolved") is False else None
    return f"transferred: {call['transfer_reason']}" if call["outcome"] == "transferred" else call["outcome"]


def table(title: str, groups: dict[str, list[dict]]) -> list[str]:
    lines = [f"## {title}", "", "| | Calls | Contained (rule) | Contained (judged) | Reviewed |", "|---|---|---|---|---|"]
    for key, calls in sorted(groups.items()):
        reviewed = [c for c in calls if c["review"]]
        judged = sum(c["outcome"] in CONTAINED and c["review"]["resolved"] for c in reviewed)
        contained = sum(c["outcome"] in CONTAINED for c in calls)
        lines.append(f"| {key} | {len(calls)} | {pct(contained, len(calls))} | {pct(judged, len(reviewed))} | {len(reviewed)} |")
    return lines + [""]


def build_report(calls: list[dict], start: datetime, end: datetime) -> str:
    by_journey, by_version, fixes = defaultdict(list), defaultdict(list), defaultdict(list)
    for c in calls:
        by_journey[c["journey"]].append(c)
        by_version[c["prompt_version"]].append(c)
        if why := failure(c):
            fixes[(c["journey"], why)].append(c)

    lines = [f"# Call review, {start:%Y-%m-%d} to {end:%Y-%m-%d}", "", f"{len(calls)} calls.", ""]
    lines += table("By journey", by_journey)
    lines += table("By prompt version", by_version)
    lines += ["## Fix list", "", "Hallucinations first, then the biggest groups. The reason is the judge's; read the call before changing anything. "
              "Lock a fix in with `python call_to_scenario.py <id>`.", ""]
    ranked = sorted(fixes.items(), key=lambda kv: (kv[0][1] != "hallucinated", -len(kv[1])))
    for i, ((journey, why), group) in enumerate(ranked, 1):
        lines.append(f"### {i}. {journey}: {why} ({len(group)})")
        for c in group:
            reason = (c["review"] or {}).get("reason", "not reviewed yet")
            lines.append(f"- #{c['id']} ({c['prompt_version']}): {reason}")
        lines.append("")
    if not ranked:
        lines.append("Nothing to fix: every call was contained and nothing was flagged.")
    return "\n".join(lines)


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    base_url = os.getenv("REVIEW_DMS_BASE_URL") or os.getenv("DMS_BASE_URL", "http://localhost:8000")
    response = httpx.get(f"{base_url}/calls", headers={"X-Review-Key": os.environ["REVIEW_API_KEY"]}, timeout=30)
    # ponytail: pulls every call and filters here; add a ?since= param when that gets slow.
    # created_at is the DMS's naive server time, UTC on Railway.
    end = datetime.now(UTC).replace(tzinfo=None)
    start = end - timedelta(days=days)
    calls = [c for c in response.raise_for_status().json() if datetime.fromisoformat(c["created_at"]) >= start]
    report = build_report(calls, start, end)
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w") as f:
            f.write(report + "\n")
    print(report)


def _check():
    def call(id, journey, outcome, reason=None, review=None, version="stable"):
        return {"id": id, "journey": journey, "outcome": outcome, "transfer_reason": reason, "review": review, "prompt_version": version}

    ok = {"resolved": True, "hallucinated": False, "reason": "fine"}
    calls = [
        call(1, "new_booking", "booked", review=ok),
        call(2, "existing_appointment", "transferred", "cannot_verify_caller"),
        call(3, "existing_appointment", "transferred", "cannot_verify_caller", version="candidate"),
        call(4, "new_booking", "booked", review={"resolved": True, "hallucinated": True, "reason": "said $90"}),
        call(5, "other", "resolved_no_action", review={"resolved": False, "hallucinated": False, "reason": "hung up on them"}),
    ]
    assert [failure(c) for c in calls] == [None, "transferred: cannot_verify_caller", "transferred: cannot_verify_caller", "hallucinated", "judged unresolved"]
    report = build_report(calls, datetime(2026, 10, 14), datetime(2026, 10, 21))
    assert "| new_booking | 2 | 100% | 100% | 2 |" in report
    assert "| candidate | 1 | 0% | - | 0 |" in report
    # Hallucination ranks first even with one call; then the biggest group.
    assert report.index("### 1. new_booking: hallucinated (1)") < report.index("### 2. existing_appointment: transferred: cannot_verify_caller (2)")
    assert "- #3 (candidate): not reviewed yet" in report
    assert "#1 " not in report  # contained and judged fine: not on the fix list
    print("ok")


if __name__ == "__main__":
    _check() if sys.argv[1:] == ["--check"] else main()
