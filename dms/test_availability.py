from conftest import future_slot


def _book(client, technician_id, slot, phone="555-0101"):
    # X-Skip-Rate-Limit: these helpers book far more than MAX_PER_WINDOW slots
    # per test to fill up a technician's whole week - that's fixture setup,
    # not what's under test here (see rate_limit.py; test_appointments.py
    # covers the rate limit itself).
    return client.post(
        "/appointments",
        json={
            "technician_id": technician_id,
            "time_slot": slot,
            "customer_name": "Carla",
            "customer_phone": phone,
            "vehicle_make": "Honda",
            "vehicle_model": "Civic",
            "vehicle_year": 2020,
            "urgency": "non_urgent",
        },
        headers={"X-Skip-Rate-Limit": "true"},
    )


def test_urgent_only_on_call_master_technicians(client, technicians):
    resp = client.get("/availability", params={"urgency": "urgent", "limit": 50})
    assert resp.status_code == 200
    body = resp.json()
    assert body["outside_window"] is False
    tech_ids = {slot["technician_id"] for slot in body["slots"]}
    assert tech_ids == {technicians["dana"]}  # on-call Master Technician
    assert technicians["mike"] not in tech_ids  # Quick Service can't cover urgent
    assert technicians["ravi"] not in tech_ids  # off-call Master Technician doesn't count


def test_non_urgent_prefers_quick_service_technicians(client, technicians):
    resp = client.get("/availability", params={"urgency": "non_urgent", "limit": 50})
    assert resp.status_code == 200
    body = resp.json()
    assert body["outside_window"] is False
    tech_ids = {slot["technician_id"] for slot in body["slots"]}
    # Quick Service (mike, ravi) has open slots, so Master Technician (dana)
    # shouldn't be offered yet - overflow only kicks in once Quick Service
    # is exhausted.
    assert technicians["mike"] in tech_ids
    assert technicians["ravi"] in tech_ids
    assert technicians["dana"] not in tech_ids


def test_non_urgent_falls_back_to_master_technician_when_quick_service_full(client, technicians):
    # Book every Quick Service slot in the normal window so nothing's left.
    for day_offset in range(0, 7):
        for hour in range(8, 18):
            slot = future_slot(day_offset=day_offset, hour=hour)
            _book(client, technicians["mike"], slot)
            _book(client, technicians["ravi"], slot)

    resp = client.get("/availability", params={"urgency": "non_urgent", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["outside_window"] is False
    assert len(body["slots"]) > 0
    tech_ids = {slot["technician_id"] for slot in body["slots"]}
    assert tech_ids == {technicians["dana"]}


def test_non_urgent_extends_past_window_when_everyone_is_full(client, technicians):
    for tech_id in technicians.values():
        for day_offset in range(0, 7):
            for hour in range(8, 18):
                slot = future_slot(day_offset=day_offset, hour=hour)
                _book(client, tech_id, slot)

    resp = client.get("/availability", params={"urgency": "non_urgent", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["outside_window"] is True
    assert len(body["slots"]) == 1


def test_excludes_already_booked_slot(client, technicians):
    slot = future_slot(day_offset=2, hour=10)
    booked = _book(client, technicians["mike"], slot)
    assert booked.status_code == 200

    resp = client.get("/availability", params={"urgency": "non_urgent", "limit": 100})
    body = resp.json()
    returned = {(s["technician_id"], s["time_slot"]) for s in body["slots"]}
    assert (technicians["mike"], slot) not in returned


def test_respects_limit(client, technicians):
    resp = client.get("/availability", params={"urgency": "non_urgent", "limit": 1})
    assert len(resp.json()["slots"]) == 1


def test_invalid_urgency_rejected(client, technicians):
    resp = client.get("/availability", params={"urgency": "whenever"})
    assert resp.status_code == 422


def test_results_sorted_soonest_first(client, technicians):
    resp = client.get("/availability", params={"urgency": "non_urgent", "limit": 20})
    times = [s["time_slot"] for s in resp.json()["slots"]]
    assert times == sorted(times)
