from conftest import future_slot


def _book(client, technician_id, slot, name="Carla", phone="555-0101", urgency="non_urgent"):
    return client.post(
        "/appointments",
        json={
            "technician_id": technician_id,
            "time_slot": slot,
            "customer_name": name,
            "customer_phone": phone,
            "vehicle_make": "Honda",
            "vehicle_model": "Civic",
            "vehicle_year": 2020,
            "urgency": urgency,
        },
    )


def test_create_appointment_success(client, technicians):
    resp = _book(client, technicians["mike"], future_slot())
    assert resp.status_code == 200
    assert resp.json()["status"] == "booked"


def test_unknown_technician_rejected(client, technicians):
    resp = _book(client, 999, future_slot())
    assert resp.status_code == 404


def test_invalid_urgency_rejected(client, technicians):
    resp = _book(client, technicians["mike"], future_slot(), urgency="whenever")
    assert resp.status_code == 422


def test_same_technician_same_slot_conflict(client, technicians):
    slot = future_slot(day_offset=1, hour=9)
    first = _book(client, technicians["mike"], slot)
    assert first.status_code == 200

    second = _book(client, technicians["mike"], slot, name="Bob")
    assert second.status_code == 409


def test_different_technician_same_slot_allowed(client, technicians):
    slot = future_slot(day_offset=1, hour=9)
    first = _book(client, technicians["mike"], slot)
    assert first.status_code == 200

    second = _book(client, technicians["dana"], slot, name="Bob")
    assert second.status_code == 200


def test_cancelled_appointment_frees_slot(client, technicians):
    slot = future_slot(day_offset=1, hour=9)
    first = _book(client, technicians["mike"], slot)
    appointment_id = first.json()["id"]

    cancel = client.patch(f"/appointments/{appointment_id}", params={"status": "cancelled"})
    assert cancel.status_code == 200

    rebook = _book(client, technicians["mike"], slot, name="Dana")
    assert rebook.status_code == 200


def test_booking_rate_limit_exceeded(client, technicians):
    phone = "555-9999"
    for i in range(3):
        slot = future_slot(day_offset=1, hour=9 + i)
        resp = _book(client, technicians["mike"], slot, phone=phone)
        assert resp.status_code == 200

    resp = _book(client, technicians["mike"], future_slot(day_offset=2, hour=9), phone=phone)
    assert resp.status_code == 429


def test_booking_rate_limit_skipped_with_header(client, technicians):
    phone = "555-8888"
    for i in range(3):
        slot = future_slot(day_offset=1, hour=9 + i)
        resp = _book(client, technicians["mike"], slot, phone=phone)
        assert resp.status_code == 200

    resp = client.post(
        "/appointments",
        json={
            "technician_id": technicians["mike"],
            "time_slot": future_slot(day_offset=2, hour=9),
            "customer_name": "Carla",
            "customer_phone": phone,
            "vehicle_make": "Honda",
            "vehicle_model": "Civic",
            "vehicle_year": 2020,
            "urgency": "non_urgent",
        },
        headers={"X-Skip-Rate-Limit": "true"},
    )
    assert resp.status_code == 200


def test_list_appointments_filters_by_phone_ignoring_formatting(client, technicians):
    _book(client, technicians["mike"], future_slot(day_offset=1, hour=9), phone="555-042-8871")
    _book(client, technicians["dana"], future_slot(day_offset=1, hour=10), phone="555-0101")

    resp = client.get("/appointments", params={"customer_phone": "+1 (555) 042-8871"})
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["customer_phone"] == "555-042-8871"


def test_list_appointments_filters_by_technician_id(client, technicians):
    _book(client, technicians["mike"], future_slot(day_offset=1, hour=9))
    _book(client, technicians["dana"], future_slot(day_offset=1, hour=10))

    resp = client.get("/appointments", params={"technician_id": technicians["mike"]})
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["technician_id"] == technicians["mike"]


def test_list_appointments_filters_by_status(client, technicians):
    slot = future_slot(day_offset=1, hour=9)
    booked = _book(client, technicians["mike"], slot).json()
    client.patch(f"/appointments/{booked['id']}", params={"status": "cancelled"})
    _book(client, technicians["dana"], future_slot(day_offset=1, hour=10))

    resp = client.get("/appointments", params={"status": "cancelled"})
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["id"] == booked["id"]


def test_get_appointment_by_id(client, technicians):
    booked = _book(client, technicians["mike"], future_slot()).json()

    resp = client.get(f"/appointments/{booked['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == booked["id"]


def test_get_unknown_appointment_404(client, technicians):
    resp = client.get("/appointments/999")
    assert resp.status_code == 404


def test_update_status_invalid_rejected(client, technicians):
    booked = _book(client, technicians["mike"], future_slot()).json()

    resp = client.patch(f"/appointments/{booked['id']}", params={"status": "whenever"})
    assert resp.status_code == 422


def test_update_unknown_appointment_404(client, technicians):
    resp = client.patch("/appointments/999", params={"status": "cancelled"})
    assert resp.status_code == 404
