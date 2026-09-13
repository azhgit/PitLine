def test_create_handoff_enqueues_sms_job(client):
    resp = client.post(
        "/handoffs", json={"customer_phone": "555-0101", "reason": "urgent_no_availability"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["customer_phone"] == "555-0101"
    assert body["reason"] == "urgent_no_availability"

    jobs = client.get("/jobs", params={"status": "pending"}).json()
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "handoff_sms"
    assert jobs[0]["payload"]["to"] == "555-0101"


def test_list_handoffs_sorted_newest_first(client):
    client.post("/handoffs", json={"customer_phone": "555-0101", "reason": "cannot_understand_caller"})
    client.post("/handoffs", json={"customer_phone": "555-0102", "reason": "cannot_verify_caller"})

    resp = client.get("/handoffs")
    assert resp.status_code == 200
    reasons = [h["reason"] for h in resp.json()]
    assert reasons == ["cannot_verify_caller", "cannot_understand_caller"]
