from conftest import future_slot


def test_urgent_excludes_off_call_technicians(client, technicians):
    resp = client.get("/availability", params={"specialty": "quick_service", "urgency": "urgent", "limit": 50})
    assert resp.status_code == 200
    tech_ids = {slot["technician_id"] for slot in resp.json()}
    assert technicians["mike"] in tech_ids  # on-call
    assert technicians["ravi"] not in tech_ids  # off-call


def test_non_urgent_includes_off_call_technicians(client, technicians):
    resp = client.get(
        "/availability", params={"specialty": "quick_service", "urgency": "non_urgent", "limit": 50}
    )
    assert resp.status_code == 200
    tech_ids = {slot["technician_id"] for slot in resp.json()}
    assert technicians["mike"] in tech_ids
    assert technicians["ravi"] in tech_ids


def test_excludes_already_booked_slot(client, technicians):
    slot = future_slot(day_offset=2, hour=10)
    booked = client.post(
        "/appointments",
        json={
            "technician_id": technicians["mike"],
            "time_slot": slot,
            "customer_name": "Carla",
            "customer_phone": "555-0101",
            "vehicle_make": "Honda",
            "vehicle_model": "Civic",
            "vehicle_year": 2020,
            "urgency": "non_urgent",
        },
    )
    assert booked.status_code == 200

    resp = client.get(
        "/availability", params={"specialty": "quick_service", "urgency": "non_urgent", "limit": 100}
    )
    returned = {(s["technician_id"], s["time_slot"]) for s in resp.json()}
    assert (technicians["mike"], slot) not in returned


def test_unknown_specialty_returns_empty(client, technicians):
    resp = client.get(
        "/availability", params={"specialty": "electrical", "urgency": "non_urgent"}
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_respects_limit(client, technicians):
    resp = client.get(
        "/availability", params={"specialty": "quick_service", "urgency": "non_urgent", "limit": 1}
    )
    assert len(resp.json()) == 1


def test_invalid_urgency_rejected(client, technicians):
    resp = client.get(
        "/availability", params={"specialty": "quick_service", "urgency": "whenever"}
    )
    assert resp.status_code == 422


def test_results_sorted_soonest_first(client, technicians):
    resp = client.get(
        "/availability", params={"specialty": "quick_service", "urgency": "non_urgent", "limit": 20}
    )
    times = [s["time_slot"] for s in resp.json()]
    assert times == sorted(times)
