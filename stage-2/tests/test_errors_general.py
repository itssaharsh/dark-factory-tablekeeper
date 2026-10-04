"""§5 the shared error shape and the generic validation rules."""
from __future__ import annotations


def test_every_error_has_the_documented_shape(world):
    resp = world.ada.get("/reservations/NOSUCHREF")
    assert resp.status == 404
    assert set(resp.body.keys()) == {"error"}
    assert set(resp.body["error"].keys()) == {"code", "message"}
    assert resp.body["error"]["code"] == "not_found"
    assert isinstance(resp.body["error"]["message"], str) and resp.body["error"]["message"]


def test_malformed_json_body_is_400(world, anon):
    resp = anon.raw("POST", "/auth/signup", b"{not json")
    assert resp.status == 400
    assert resp.error_code == "malformed_request"


def test_non_object_json_body_is_400(world, anon):
    resp = anon.raw("POST", "/auth/signup", b"[1, 2, 3]")
    assert resp.status == 400
    assert resp.error_code == "malformed_request"


def test_unknown_route_is_404(anon):
    resp = anon.get("/no/such/route")
    assert resp.status == 404


def test_availability_query_int_rejects_non_digit_forms(world, anon):
    base = {"restaurant_id": world.rid, "date": world.date}
    for bad in ("1e9", "4.0", "+4", "-4", "four", ""):
        resp = anon.get("/availability", params={**base, "party_size": bad})
        assert resp.status == 422, f"{bad!r} -> {resp}"
        assert resp.error_code == "validation_failed"


def test_availability_query_int_accepts_plain_digits(world, anon):
    resp = anon.get("/availability", params={
        "restaurant_id": world.rid, "date": world.date, "party_size": "04"})
    assert resp.status == 200


def test_availability_invalid_date_is_422(world, anon):
    for bad in ("not-a-date", "2026-13-40", "2026-02-30", "20260101"):
        resp = anon.get("/availability", params={
            "restaurant_id": world.rid, "date": bad, "party_size": 2})
        assert resp.status == 422, f"{bad!r} -> {resp}"


def test_unknown_query_parameters_are_ignored(world, anon):
    resp = anon.get("/availability", params={
        "restaurant_id": world.rid, "date": world.date, "party_size": 2, "sort": "whatever"})
    assert resp.status == 200


def test_booking_in_the_past_is_not_rejected_solely_for_that(reset, anon):
    import datetime as dt

    reset({
        "users": [{"id": "u_p", "email": "past@example.com",
                   "password": "correct horse", "display_name": "P"}],
        "restaurants": [{
            "id": "r_past", "name": "Past", "timezone": "UTC", "slot_minutes": 30,
            "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
            "opening_hours": [{"weekday": d, "opens": "00:00", "closes": "23:30"}
                               for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")],
            "tables": [{"id": "t_1", "label": "1", "capacity": 2}],
        }],
        "reservations": [],
    })
    token = anon.post("/auth/login", json_body={
        "email": "past@example.com", "password": "correct horse"}).body["token"]
    client = anon.with_token(token)
    yesterday = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    resp = client.post("/reservations", json_body={
        "restaurant_id": "r_past", "table_id": "t_1",
        "starts_at_local": f"{yesterday}T10:00", "party_size": 2}, idempotency_key="past-1")
    assert resp.status == 201


def test_idempotency_key_header_name_is_case_insensitive(world):
    import http.client
    import json as json_mod

    conn = http.client.HTTPConnection("127.0.0.1", world.ada.port, timeout=5)
    body = {"restaurant_id": world.rid, "table_id": "t_2",
            "starts_at_local": f"{world.date}T19:00", "party_size": 4}
    conn.request("POST", "/reservations", body=json_mod.dumps(body).encode(), headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {world.ada.token}",
        "idempotency-key": "lowercase-header",
    })
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 201
