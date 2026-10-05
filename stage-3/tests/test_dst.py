"""§9 time and DST."""
from __future__ import annotations

from conftest import new_key


def _reset_dst_restaurant(reset, rid="r_dst", timezone="Europe/Berlin"):
    reset({
        "users": [{"id": "u_d", "email": "dst@example.com", "password": "correct horse", "display_name": "D"}],
        "restaurants": [{
            "id": rid, "name": "DST", "timezone": timezone, "slot_minutes": 30,
            "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 60,
            "opening_hours": [{"weekday": d, "opens": "00:00", "closes": "05:00"}
                               for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")],
            "tables": [{"id": "t_1", "label": "1", "capacity": 2}],
        }],
        "reservations": [],
    })


def _client(anon):
    token = anon.post("/auth/login", json_body={
        "email": "dst@example.com", "password": "correct horse"}).body["token"]
    return anon.with_token(token)


def test_spring_forward_gap_booking_is_422(reset, anon):
    """Europe/Berlin, 2026-03-29 02:00 -> 03:00; 02:30 never existed."""
    _reset_dst_restaurant(reset)
    client = _client(anon)
    resp = client.post("/reservations", json_body={
        "restaurant_id": "r_dst", "table_id": "t_1",
        "starts_at_local": "2026-03-29T02:30", "party_size": 2}, idempotency_key=new_key())
    assert resp.status == 422
    assert resp.error_code == "invalid_local_time"


def test_spring_forward_gap_never_appears_in_availability(reset, anon):
    _reset_dst_restaurant(reset)
    body = anon.get("/availability", params={
        "restaurant_id": "r_dst", "date": "2026-03-29", "party_size": 2}).body
    times = [s["starts_at_local"].split("T")[1] for s in body["slots"]]
    assert "02:00" not in times
    assert "02:30" not in times
    assert times == ["00:00", "00:30", "01:00", "01:30", "03:00", "03:30"]


def test_fall_back_slot_appears_once_and_resolves_to_earlier_offset(reset, anon):
    """Europe/Berlin, 2026-10-25 03:00 -> 02:00; 02:30 happens twice."""
    _reset_dst_restaurant(reset)
    body = anon.get("/availability", params={
        "restaurant_id": "r_dst", "date": "2026-10-25", "party_size": 2}).body
    matches = [s for s in body["slots"] if s["starts_at_local"].endswith("02:30")]
    assert len(matches) == 1
    # the earlier (pre-change) occurrence is CEST, +02:00
    assert matches[0]["starts_at"] == "2026-10-25T02:30:00+02:00"


def test_booking_fall_back_repeated_hour_resolves_to_first_occurrence(reset, anon):
    _reset_dst_restaurant(reset)
    client = _client(anon)
    resp = client.post("/reservations", json_body={
        "restaurant_id": "r_dst", "table_id": "t_1",
        "starts_at_local": "2026-10-25T02:30", "party_size": 2}, idempotency_key=new_key())
    assert resp.status == 201
    assert resp.body["starts_at"] == "2026-10-25T02:30:00+02:00"


def test_duration_is_absolute_time_across_fall_back(reset, anon):
    """A 90-minute booking at 01:30 on the fall-back night ends at local 02:00, not 03:00."""
    _reset_dst_restaurant(reset)
    client = _client(anon)
    resp = client.post("/reservations", json_body={
        "restaurant_id": "r_dst", "table_id": "t_1",
        "starts_at_local": "2026-10-25T01:30", "party_size": 2}, idempotency_key=new_key())
    assert resp.status == 201
    assert resp.body["starts_at"] == "2026-10-25T01:30:00+02:00"
    assert resp.body["ends_at"] == "2026-10-25T02:00:00+01:00"


def test_new_york_spring_forward_gap_is_422(reset, anon):
    """America/New_York, 2026-03-08 02:00 -> 03:00; 02:30 never existed."""
    _reset_dst_restaurant(reset, rid="r_ny", timezone="America/New_York")
    client = _client(anon)
    resp = client.post("/reservations", json_body={
        "restaurant_id": "r_ny", "table_id": "t_1",
        "starts_at_local": "2026-03-08T02:30", "party_size": 2}, idempotency_key=new_key())
    assert resp.status == 422
    assert resp.error_code == "invalid_local_time"


def test_new_york_fall_back_resolves_to_earlier_offset(reset, anon):
    """America/New_York, 2026-11-01 02:00 -> 01:00; 01:30 happens twice."""
    _reset_dst_restaurant(reset, rid="r_ny2", timezone="America/New_York")
    client = _client(anon)
    resp = client.post("/reservations", json_body={
        "restaurant_id": "r_ny2", "table_id": "t_1",
        "starts_at_local": "2026-11-01T01:30", "party_size": 2}, idempotency_key=new_key())
    assert resp.status == 201
    assert resp.body["starts_at"] == "2026-11-01T01:30:00-04:00"
