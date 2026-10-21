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
