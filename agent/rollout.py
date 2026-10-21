import hashlib


def bucket(call_sid: str) -> int:
    """Stable 0-99 bucket for a call: the same CallSid always lands in the
    same bucket, so a call's prompt version can be reproduced from its SID.
    """
    return int(hashlib.sha256(call_sid.encode()).hexdigest(), 16) % 100


def pick_version(call_sid: str | None, candidate_percent: int, has_candidate: bool) -> str:
    """'candidate' for the first candidate_percent buckets, else 'stable'.
    No candidate defined, no CallSid, or 0% always means 'stable' - the
    rollback is setting the percent back to 0.
    """
    if not has_candidate or not call_sid:
        return "stable"
    return "candidate" if bucket(call_sid) < candidate_percent else "stable"


if __name__ == "__main__":
    sids = [f"CA{i:032x}" for i in range(10_000)]

    assert all(pick_version(s, 100, False) == "stable" for s in sids[:100])
    assert pick_version(None, 100, True) == "stable"
    assert all(pick_version(s, 0, True) == "stable" for s in sids)
    assert all(pick_version(s, 100, True) == "candidate" for s in sids)
    assert all(pick_version(s, 10, True) == pick_version(s, 10, True) for s in sids[:100])

    # Ramp is monotonic: raising the percent only moves calls stable -> candidate.
    at_10 = {s for s in sids if pick_version(s, 10, True) == "candidate"}
    at_50 = {s for s in sids if pick_version(s, 50, True) == "candidate"}
    assert at_10 <= at_50
    assert 800 < len(at_10) < 1200, len(at_10)  # ~10%
    print("ok")
