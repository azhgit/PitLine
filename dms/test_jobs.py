def _create_job(client, phone="555-0101"):
    resp = client.post("/handoffs", json={"customer_phone": phone, "reason": "urgent_no_availability"})
    jobs = client.get("/jobs", params={"status": "pending"}).json()
    return next(j for j in jobs if j["payload"]["to"] == phone)


def test_list_jobs_filters_by_status(client):
    _create_job(client)
    pending = client.get("/jobs", params={"status": "pending"}).json()
    assert len(pending) == 1
    sent = client.get("/jobs", params={"status": "sent"}).json()
    assert sent == []


def test_update_job_marks_sent(client):
    job = _create_job(client)
    resp = client.patch(f"/jobs/{job['id']}", json={"status": "sent"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"

    pending = client.get("/jobs", params={"status": "pending"}).json()
    assert pending == []


def test_update_job_records_attempt_count_on_retry(client):
    job = _create_job(client)
    resp = client.patch(f"/jobs/{job['id']}", json={"status": "pending", "attempt_count": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["attempt_count"] == 1


def test_update_job_invalid_status_rejected(client):
    job = _create_job(client)
    resp = client.patch(f"/jobs/{job['id']}", json={"status": "whenever"})
    assert resp.status_code == 422


def test_update_unknown_job_404(client):
    resp = client.patch("/jobs/999", json={"status": "sent"})
    assert resp.status_code == 404


def test_claim_returns_pending_job_and_marks_it_claimed(client):
    job = _create_job(client)
    resp = client.post("/jobs/claim")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == job["id"]
    assert body["status"] == "claimed"

    # No longer pending - a second claim call shouldn't see it again.
    pending = client.get("/jobs", params={"status": "pending"}).json()
    assert pending == []


def test_claim_returns_null_when_nothing_pending(client):
    resp = client.post("/jobs/claim")
    assert resp.status_code == 200
    assert resp.json() is None


def test_claim_ignores_already_claimed_or_terminal_jobs(client):
    job = _create_job(client)
    client.patch(f"/jobs/{job['id']}", json={"status": "sent"})
    resp = client.post("/jobs/claim")
    assert resp.json() is None
