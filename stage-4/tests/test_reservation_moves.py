"""§11 POST /reservation-moves."""
from __future__ import annotations

from conftest import booking_date, make_restaurant, new_key


def test_single_move_changes_the_table(world, book):
    created = book(table_id="t_1", at="19:00", party_size=2).body
    resp = world.ada.post("/reservation-moves", json_body={
        "moves": [{"reference": created["reference"], "table_id": "t_2"}]},
        idempotency_key=new_key())
    assert resp.status == 201
    assert [r["table_id"] for r in resp.body["reservations"]] == ["t_2"]
    assert resp.body["reservations"][0]["reference"] == created["reference"]
    assert resp.body["reservations"][0]["reservation_id"] == created["reservation_id"]


def test_swap_two_bookings_between_tables_atomically(world, book):
    r1 = book(table_id="t_1", at="19:00", party_size=2).body
    r2 = book(table_id="t_2", at="19:00", party_size=2).body
    resp = world.ada.post("/reservation-moves", json_body={"moves": [
        {"reference": r1["reference"], "table_id": "t_2"},
        {"reference": r2["reference"], "table_id": "t_1"},
    ]}, idempotency_key=new_key())
    assert resp.status == 201
    by_ref = {r["reference"]: r for r in resp.body["reservations"]}
    assert by_ref[r1["reference"]]["table_id"] == "t_2"
    assert by_ref[r2["reference"]]["table_id"] == "t_1"
    # input order is preserved
    assert [r["reference"] for r in resp.body["reservations"]] == [r1["reference"], r2["reference"]]


def test_unchanged_items_are_included(world, book):
    r1 = book(table_id="t_1", at="19:00", party_size=2).body
    r2 = book(table_id="t_2", at="19:30", party_size=2).body
    resp = world.ada.post("/reservation-moves", json_body={"moves": [
        {"reference": r1["reference"], "table_id": "t_3"},
        {"reference": r2["reference"]},  # no change requested
    ]}, idempotency_key=new_key())
    assert resp.status == 201
    by_ref = {r["reference"]: r for r in resp.body["reservations"]}
    assert by_ref[r2["reference"]]["table_id"] == "t_2"
    assert by_ref[r2["reference"]]["starts_at_local"] == r2["starts_at_local"]


def test_duplicate_references_is_422(world, book):
    r1 = book(table_id="t_1", at="19:00", party_size=2).body
    resp = world.ada.post("/reservation-moves", json_body={"moves": [
        {"reference": r1["reference"], "table_id": "t_2"},
        {"reference": r1["reference"], "table_id": "t_3"},
    ]}, idempotency_key=new_key())
    assert resp.status == 422
    assert resp.error_code == "validation_failed"


def test_empty_moves_is_422(world):
    resp = world.ada.post("/reservation-moves", json_body={"moves": []}, idempotency_key=new_key())
    assert resp.status == 422


def test_too_many_moves_is_422(world, book):
    # nine bookings on nine different dates so none of them can conflict with another
    refs = [book(table_id="t_1", at="19:00",
                  starts_at_local=f"{world.date[:-2]}{10 + i:02d}T19:00",
                  party_size=2).body["reference"] for i in range(9)]
    moves = [{"reference": r} for r in refs]
    resp = world.ada.post("/reservation-moves", json_body={"moves": moves}, idempotency_key=new_key())
    assert resp.status == 422


def test_unknown_reference_is_404(world, book):
    r1 = book(table_id="t_1", at="19:00", party_size=2).body
    resp = world.ada.post("/reservation-moves", json_body={"moves": [
        {"reference": r1["reference"], "table_id": "t_2"},
        {"reference": "NOSUCHREF", "table_id": "t_3"},
    ]}, idempotency_key=new_key())
    assert resp.status == 404


def test_someone_elses_reference_is_404(world, book):
    bobs = book(client=world.bob, table_id="t_1", at="19:00", party_size=2).body
    resp = world.ada.post("/reservation-moves", json_body={
        "moves": [{"reference": bobs["reference"], "table_id": "t_2"}]},
        idempotency_key=new_key())
    assert resp.status == 404


def test_no_token_is_401(world, book, anon):
    r1 = book(table_id="t_1", at="19:00", party_size=2).body
    resp = anon.post("/reservation-moves", json_body={
        "moves": [{"reference": r1["reference"], "table_id": "t_2"}]},
        idempotency_key=new_key())
    assert resp.status == 401


def test_cancelled_booking_in_batch_is_409(world, book):
    r1 = book(table_id="t_1", at="19:00", party_size=2).body
    world.ada.post(f"/reservations/{r1['reference']}/cancel")
    resp = world.ada.post("/reservation-moves", json_body={
        "moves": [{"reference": r1["reference"], "table_id": "t_2"}]},
        idempotency_key=new_key())
    assert resp.status == 409
    assert resp.error_code == "reservation_cancelled"


def test_overlap_with_unlisted_booking_is_409_and_nothing_changes(world, book):
    r1 = book(table_id="t_1", at="19:00", party_size=2).body
    r2 = book(table_id="t_2", at="19:00", party_size=2).body  # unlisted, occupies t_2 at 19:00
    resp = world.ada.post("/reservation-moves", json_body={
        "moves": [{"reference": r1["reference"], "table_id": "t_2"}]},
        idempotency_key=new_key())
    assert resp.status == 409
    assert resp.error_code == "table_unavailable"

    # nothing changed: r1 is still on t_1
    unchanged = world.ada.get(f"/reservations/{r1['reference']}").body
    assert unchanged["table_id"] == "t_1"
    still_booked = world.ada.get(f"/reservations/{r2['reference']}").body
    assert still_booked["table_id"] == "t_2"


def test_overlap_among_resulting_bookings_is_409(world, book):
    r1 = book(table_id="t_1", at="19:00", party_size=2).body
    r2 = book(table_id="t_2", at="20:00", party_size=2).body
    resp = world.ada.post("/reservation-moves", json_body={"moves": [
        {"reference": r1["reference"], "table_id": "t_2"},
        {"reference": r2["reference"], "starts_at_local": f"{world.date}T19:00"},
    ]}, idempotency_key=new_key())
    assert resp.status == 409
    assert resp.error_code == "table_unavailable"

    unchanged1 = world.ada.get(f"/reservations/{r1['reference']}").body
    unchanged2 = world.ada.get(f"/reservations/{r2['reference']}").body
    assert unchanged1["table_id"] == "t_1"
    assert unchanged2["table_id"] == "t_2" and unchanged2["starts_at_local"] == f"{world.date}T20:00"


def test_different_restaurants_is_422(reset, anon):
    reset({
        "users": [{"id": "u_a", "email": "multi@example.com",
                   "password": "correct horse", "display_name": "A"}],
        "restaurants": [
            make_restaurant("r_one"),
            make_restaurant("r_two", tables=[{"id": "t_x", "label": "X", "capacity": 2}]),
        ],
        "reservations": [],
    })
    token = anon.post("/auth/login", json_body={
        "email": "multi@example.com", "password": "correct horse"}).body["token"]
    client = anon.with_token(token)
    date = booking_date()
    r1 = client.post("/reservations", json_body={
        "restaurant_id": "r_one", "table_id": "t_1",
        "starts_at_local": f"{date}T19:00", "party_size": 2}, idempotency_key=new_key()).body
    r2 = client.post("/reservations", json_body={
        "restaurant_id": "r_two", "table_id": "t_x",
        "starts_at_local": f"{date}T19:00", "party_size": 2}, idempotency_key=new_key()).body
    resp = client.post("/reservation-moves", json_body={"moves": [
        {"reference": r1["reference"], "table_id": "t_2"},
        {"reference": r2["reference"], "table_id": "t_x"},
    ]}, idempotency_key=new_key())
    assert resp.status == 422
    assert resp.error_code == "validation_failed"


def test_idempotency_applies_to_moves(world, book):
    r1 = book(table_id="t_1", at="19:00", party_size=2).body
    key = new_key()
    body = {"moves": [{"reference": r1["reference"], "table_id": "t_2"}]}
    first = world.ada.post("/reservation-moves", json_body=body, idempotency_key=key)
    replay = world.ada.post("/reservation-moves", json_body=body, idempotency_key=key)
    assert first.status == 201
    assert replay.status == 200
    assert replay.body == first.body


def test_missing_idempotency_key_is_400(world, book):
    r1 = book(table_id="t_1", at="19:00", party_size=2).body
    resp = world.ada.post("/reservation-moves", json_body={
        "moves": [{"reference": r1["reference"], "table_id": "t_2"}]})
    assert resp.status == 400
    assert resp.error_code == "missing_idempotency_key"
