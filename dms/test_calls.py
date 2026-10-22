REVIEW = {"X-Review-Key": "test-review-key"}


def _call(outcome, **kw):
    return {
        "call_sid": "CA1",
        "outcome": outcome,
        "prompt_version": "stable",
        "caller_verified": True,
        "caller_last4": "7007",
        "transcript": [{"role": "caller", "text": "my name is [name]"}],
        "tool_log": [],
        **kw,
    }


def test_transcripts_need_review_key(client):
    client.post("/calls", json=_call("booked"))
    # The dashboard's DMS_API_KEY is public - it must not read transcripts.
    assert client.get("/calls").status_code == 401
    assert client.get("/calls", headers={"X-Review-Key": "wrong"}).status_code == 401
    calls = client.get("/calls", headers=REVIEW).json()
    assert calls[0]["transcript"] == [{"role": "caller", "text": "my name is [name]"}]


def test_stats_are_aggregate_only(client):
    for outcome in ["booked", "resolved_no_action", "transferred", "abandoned"]:
        client.post("/calls", json=_call(outcome, transfer_reason="urgent_no_availability" if outcome == "transferred" else None))
    stats = client.get("/calls/stats").json()
    assert stats["total"] == 4
    assert stats["containment_by_rule"] == 0.5
    assert stats["containment_judged"] is None
    assert "transcript" not in str(stats) and "7007" not in str(stats)


def test_review_can_overturn_rule_containment(client):
    a = client.post("/calls", json=_call("resolved_no_action")).json()["id"]
    b = client.post("/calls", json=_call("booked")).json()["id"]
    client.patch(f"/calls/{a}/review", headers=REVIEW, json={"resolved": False, "hallucinated": True, "reason": "x"})
    client.patch(f"/calls/{b}/review", headers=REVIEW, json={"resolved": True, "hallucinated": False, "reason": "y"})
    stats = client.get("/calls/stats").json()
    assert stats["containment_by_rule"] == 1.0
    assert stats["containment_judged"] == 0.5
    assert stats["hallucination_rate"] == 0.5
    assert client.get("/calls", params={"unreviewed": True}, headers=REVIEW).json() == []


def test_stats_split_by_journey(client):
    find = {"tool": "find_appointment", "args": {}, "result": {"verified": True}}
    avail = {"tool": "check_availability", "args": {}, "result": {"slots": []}}
    client.post("/calls", json=_call("booked", tool_log=[avail]))
    client.post("/calls", json=_call("transferred", transfer_reason="urgent_no_availability", tool_log=[avail]))
    # A reschedule checks availability too, but it started from an existing appointment.
    client.post("/calls", json=_call("booked", tool_log=[find, avail]))
    client.post("/calls", json=_call("resolved_no_action"))
    by_journey = client.get("/calls/stats").json()["by_journey"]
    assert {j: (s["total"], s["containment_by_rule"]) for j, s in by_journey.items()} == {
        "existing_appointment": (1, 1.0),
        "new_booking": (2, 0.5),
        "other": (1, 1.0),
    }
    assert {c["journey"] for c in client.get("/calls", headers=REVIEW).json()} == set(by_journey)
