def test_list_technicians(client, technicians):
    resp = client.get("/technicians")
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()}
    assert names == {"Mike Alvarez", "Ravi Patel", "Dana Whitfield"}


def test_get_technician_by_id(client, technicians):
    resp = client.get(f"/technicians/{technicians['mike']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Mike Alvarez"
    assert body["specialty"] == "quick_service"
    assert body["on_call"] is True


def test_get_unknown_technician_404(client, technicians):
    resp = client.get("/technicians/999")
    assert resp.status_code == 404
