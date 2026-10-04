"""§8 POST /reservations."""
from __future__ import annotations

import re

import httpclient

REFERENCE_RE = re.compile(r"^[A-Z0-9]{6,12}$")


def test_create_returns_documented_shape(world, book):
    resp = book(table_id="t_2", at="19:00", party_size=4)
    assert resp.status == 201
    body = resp.body
    assert body["restaurant_id"] == world.rid
    assert body["table_id"] == "t_2"
    assert body["party_size"] == 4
    assert body["status"] == "confirmed"
    assert body["starts_at_local"] == f"{world.date}T19:00"
    assert body["reservation_id"]
    assert body["created_at"]
    assert REFERENCE_RE.match(body["reference"]), body["reference"]


def test_ends_at_is_start_plus_duration(world, book):
    body = book(at="19:00").body
    assert body["starts_at"].startswith(f"{world.date}T19:00:00")
    assert body["ends_at"].startswith(f"{world.date}T20:30:00")


def test_reference_is_unique_across_reservations(world, book):
    refs = {book(table_id=t, at="19:00", party_size=2).body["reference"] for t in ("t_1", "t_2", "t_3")}
    assert len(refs) == 3


def test_double_booking_same_table_same_slot_is_409(world, book):
    assert book(table_id="t_2", at="19:00").status == 201
    resp = book(table_id="t_2", at="19:00")
    assert resp.status == 409
    assert resp.error_code == "table_unavailable"


def test_overlapping_but_offset_time_is_409(world, book):
    assert book(table_id="t_2", at="19:00").status == 201  # occupies [19:00, 20:30)
    resp = book(table_id="t_2", at="20:00")  # [20:00, 21:30) overlaps
    assert resp.status == 409
    assert resp.error_code == "table_unavailable"


def test_adjacent_non_overlapping_booking_succeeds(world, book):
    assert book(table_id="t_2", at="19:00").status == 201  # [19:00, 20:30)
    resp = book(table_id="t_2", at="20:30")  # starts exactly when the first ends
    assert resp.status == 201


def test_not_on_slot_grid_is_422(world, book):
    resp = book(table_id="t_2", starts_at_local=f"{world.date}T19:15")
    assert resp.status == 422
    assert resp.error_code == "not_on_slot_grid"


def test_before_opening_is_outside_opening_hours(world, book):
    resp = book(table_id="t_2", starts_at_local=f"{world.date}T17:30")
    assert resp.status == 422
    assert resp.error_code == "outside_opening_hours"


def test_ending_after_closing_is_outside_opening_hours(world, book):
    # opens 18:00 closes 23:00, duration 90 -> 21:30 is the last valid start, 22:00 ends after close
    resp = book(table_id="t_2", starts_at_local=f"{world.date}T22:00")
    assert resp.status == 422
    assert resp.error_code == "outside_opening_hours"


def test_closed_weekday_is_outside_opening_hours(reset, anon):
    reset({
        "users": [{"id": "u_a", "email": "a@example.com", "password": "correct horse", "display_name": "A"}],
        "reservations": [],
        "restaurants": [{
            "id": "r_mon", "name": "Mon only", "timezone": "UTC",
            "slot_minutes": 30, "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
            "opening_hours": [{"weekday": "mon", "opens": "10:00", "closes": "14:00"}],
            "tables": [{"id": "t_1", "label": "1", "capacity": 2}],
        }],
    })
    token = anon.post("/auth/login", json_body={"email": "a@example.com", "password": "correct horse"}).body["token"]
    client = anon.with_token(token)
    import datetime as dt
    d = dt.date.today()
    while d.weekday() != 1:  # Tuesday: always closed for this fixture
        d += dt.timedelta(days=1)
    resp = client.post("/reservations", json_body={
        "restaurant_id": "r_mon", "table_id": "t_1",
        "starts_at_local": f"{d.isoformat()}T11:00", "party_size": 2}, idempotency_key="k")
    assert resp.status == 422
    assert resp.error_code == "outside_opening_hours"


def test_party_size_exceeds_capacity_is_422(world, book):
    resp = book(table_id="t_1", party_size=3)  # t_1 capacity is 2
    assert resp.status == 422
    assert resp.error_code == "party_exceeds_capacity"


def test_unknown_restaurant_is_404(world, book):
    resp = book(restaurant_id="nope")
    assert resp.status == 404
    assert resp.error_code == "not_found"


def test_unknown_table_is_404(world, book):
    resp = book(table_id="nope")
    assert resp.status == 404
    assert resp.error_code == "not_found"


def test_table_from_another_restaurant_is_404(reset, anon, world):
    reset({
        **world.fixture,
        "restaurants": world.fixture["restaurants"] + [{
            "id": "r_other", "name": "Other", "timezone": "UTC",
            "slot_minutes": 30, "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
            "opening_hours": [], "tables": [{"id": "t_other", "label": "O", "capacity": 2}],
        }],
    })
    token = anon.post("/auth/login", json_body={
        "email": "ada@example.com", "password": "correct horse"}).body["token"]
    resp = anon.with_token(token).post("/reservations", json_body={
        "restaurant_id": world.rid, "table_id": "t_other",
        "starts_at_local": f"{world.date}T19:00", "party_size": 2}, idempotency_key="k")
    assert resp.status == 404


def test_missing_idempotency_key_is_400(world):
    resp = world.ada.post("/reservations", json_body={
        "restaurant_id": world.rid, "table_id": "t_2",
        "starts_at_local": f"{world.date}T19:00", "party_size": 4})
    assert resp.status == 400
    assert resp.error_code == "missing_idempotency_key"


def test_requires_authentication(world):
    anon_like = httpclient.Client(world.ada.port)
    resp = anon_like.post("/reservations", json_body={
        "restaurant_id": world.rid, "table_id": "t_2",
        "starts_at_local": f"{world.date}T19:00", "party_size": 4}, idempotency_key="k")
    assert resp.status == 401


def test_party_size_wrong_type_is_422_not_400(world, book):
    for bad in ("4", True, 4.0, None):
        resp = book(party_size=bad, key=f"k-{bad}")
        assert resp.status == 422, f"{bad!r} -> {resp}"
        assert resp.error_code == "validation_failed"


def test_party_size_zero_or_negative_is_422(world, book):
    for bad in (0, -1):
        resp = book(party_size=bad, key=f"k-{bad}")
        assert resp.status == 422
        assert resp.error_code == "validation_failed"


def test_starts_at_local_bad_format_is_422(world, book):
    for bad in ("19:00", f"{world.date} 19:00", f"{world.date}T19:00:00", "not-a-date"):
        resp = book(starts_at_local=bad, key=f"k-{bad}")
        assert resp.status == 422
        assert resp.error_code == "validation_failed"


def test_starts_at_local_wrong_json_type_is_400(world, book):
    resp = book(starts_at_local=12345, key="k-type")
    assert resp.status == 400
    assert resp.error_code == "malformed_request"


def test_table_id_wrong_json_type_is_400(world, book):
    resp = book(table_id=123, key="k-table-type")
    assert resp.status == 400
    assert resp.error_code == "malformed_request"


def test_unknown_fields_are_ignored(world, book):
    resp = book(table_id="t_2", at="19:00", extra_junk="ignored me", key="k-extra")
    assert resp.status == 201
