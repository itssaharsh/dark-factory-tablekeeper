"""§8 GET /reservations, GET /reservations/{reference}, cancel, PATCH."""
from __future__ import annotations

import datetime as dt

from conftest import new_key


def test_list_is_empty_for_a_fresh_user(world):
    assert world.bob.get("/reservations").body == {"reservations": []}


def test_list_contains_confirmed_and_cancelled(world, book):
    ref1 = book(table_id="t_1", at="19:00", party_size=2).body["reference"]
    ref2 = book(table_id="t_2", at="19:00", party_size=2).body["reference"]
    world.ada.post(f"/reservations/{ref1}/cancel")
    refs = {r["reference"] for r in world.ada.get("/reservations").body["reservations"]}
    assert refs == {ref1, ref2}


def test_list_is_ordered_by_starts_at_descending(world, book):
    early = book(table_id="t_1", at="18:00", party_size=2).body
    late = book(table_id="t_2", at="20:00", party_size=2).body
    refs = [r["reference"] for r in world.ada.get("/reservations").body["reservations"]]
    assert refs.index(late["reference"]) < refs.index(early["reference"])


def test_get_by_reference(world, book):
    created = book(table_id="t_2", at="19:00").body
    fetched = world.ada.get(f"/reservations/{created['reference']}").body
    assert fetched == created


def test_get_someone_elses_reservation_is_404(world, book):
    created = book(table_id="t_2", at="19:00").body
    resp = world.bob.get(f"/reservations/{created['reference']}")
    assert resp.status == 404
    assert resp.error_code == "not_found"


def test_get_unknown_reference_is_404(world):
    resp = world.ada.get("/reservations/NOSUCHREF")
    assert resp.status == 404


def test_cancel_sets_status_and_frees_the_table(world, book, anon):
    created = book(table_id="t_2", at="19:00", party_size=4).body
    resp = world.ada.post(f"/reservations/{created['reference']}/cancel")
    assert resp.status == 200
    assert resp.body["status"] == "cancelled"
    assert resp.body["reference"] == created["reference"]

    slots = anon.get("/availability", params={
        "restaurant_id": world.rid, "date": world.date, "party_size": 4}).body["slots"]
    slot = next(s for s in slots if s["starts_at_local"].endswith("19:00"))
    assert "t_2" in slot["available_table_ids"]


def test_cancel_twice_is_not_an_error(world, book):
    created = book(table_id="t_2", at="19:00").body
    first = world.ada.post(f"/reservations/{created['reference']}/cancel")
    second = world.ada.post(f"/reservations/{created['reference']}/cancel")
    assert first.status == 200
    assert second.status == 200
    assert second.body["status"] == "cancelled"


def test_cancel_someone_elses_reservation_is_404(world, book):
    created = book(table_id="t_2", at="19:00").body
    resp = world.bob.post(f"/reservations/{created['reference']}/cancel")
    assert resp.status == 404


def test_cancel_within_cutoff_is_409(reset, anon):
    restaurant = {
        "id": "r_soon", "name": "Soon", "timezone": "UTC", "slot_minutes": 30,
        "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 120,
        "opening_hours": [{"weekday": d, "opens": "00:00", "closes": "23:30"}
                           for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")],
        "tables": [{"id": "t_1", "label": "1", "capacity": 2}],
    }
    target = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=45)
    target = target.replace(minute=(target.minute // 30) * 30, second=0, microsecond=0)
    local = target.strftime("%Y-%m-%dT%H:%M")
    reset({
        "users": [{"id": "u_a", "email": "soon@example.com", "password": "correct horse", "display_name": "A"}],
        "restaurants": [restaurant], "reservations": [],
    })
    token = anon.post("/auth/login", json_body={
        "email": "soon@example.com", "password": "correct horse"}).body["token"]
    client = anon.with_token(token)
    created = client.post("/reservations", json_body={
        "restaurant_id": "r_soon", "table_id": "t_1", "starts_at_local": local, "party_size": 2},
        idempotency_key=new_key())
    assert created.status == 201, created
    resp = client.post(f"/reservations/{created.body['reference']}/cancel")
    assert resp.status == 409
    assert resp.error_code == "cutoff_passed"


def test_patch_changes_table_and_frees_old_one(world, book, anon):
    created = book(table_id="t_2", at="19:00", party_size=4).body
    resp = world.ada.patch(f"/reservations/{created['reference']}", json_body={"table_id": "t_3"})
    assert resp.status == 200
    assert resp.body["table_id"] == "t_3"
    assert resp.body["reference"] == created["reference"]
    assert resp.body["reservation_id"] == created["reservation_id"]

    slots = anon.get("/availability", params={
        "restaurant_id": world.rid, "date": world.date, "party_size": 4}).body["slots"]
    slot = next(s for s in slots if s["starts_at_local"].endswith("19:00"))
    assert "t_2" in slot["available_table_ids"]
    assert "t_3" not in slot["available_table_ids"]


def test_patch_changes_time(world, book):
    created = book(table_id="t_2", at="19:00").body
    resp = world.ada.patch(f"/reservations/{created['reference']}",
                            json_body={"starts_at_local": f"{world.date}T20:00"})
    assert resp.status == 200
    assert resp.body["starts_at_local"] == f"{world.date}T20:00"


def test_patch_onto_conflicting_slot_is_409_and_leaves_booking_unchanged(world, book):
    created = book(table_id="t_2", at="19:00").body
    book(table_id="t_3", at="19:00")  # occupy t_3 at the same time
    resp = world.ada.patch(f"/reservations/{created['reference']}", json_body={"table_id": "t_3"})
    assert resp.status == 409
    assert resp.error_code == "table_unavailable"

    unchanged = world.ada.get(f"/reservations/{created['reference']}").body
    assert unchanged["table_id"] == "t_2"


def test_patch_cancelled_reservation_is_409(world, book):
    created = book(table_id="t_2", at="19:00").body
    world.ada.post(f"/reservations/{created['reference']}/cancel")
    resp = world.ada.patch(f"/reservations/{created['reference']}", json_body={"table_id": "t_3"})
    assert resp.status == 409
    assert resp.error_code == "reservation_cancelled"


def test_patch_someone_elses_reservation_is_404(world, book):
    created = book(table_id="t_2", at="19:00").body
    resp = world.bob.patch(f"/reservations/{created['reference']}", json_body={"table_id": "t_3"})
    assert resp.status == 404


def test_patch_invalid_new_time_is_422(world, book):
    created = book(table_id="t_2", at="19:00").body
    resp = world.ada.patch(f"/reservations/{created['reference']}",
                            json_body={"starts_at_local": f"{world.date}T19:15"})
    assert resp.status == 422
    assert resp.error_code == "not_on_slot_grid"
