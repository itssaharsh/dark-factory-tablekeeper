"""§8 GET /restaurants, GET /restaurants/{id}, GET /availability -- all public."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("path_fn", [
    lambda world: "/restaurants",
    lambda world: f"/restaurants/{world.rid}",
])
def test_restaurant_endpoints_need_no_token(world, anon, path_fn):
    resp = anon.get(path_fn(world))
    assert resp.status == 200


def test_availability_needs_no_token(world, anon):
    resp = anon.get("/availability", params={
        "restaurant_id": world.rid, "date": world.date, "party_size": 2})
    assert resp.status == 200


def test_restaurant_list_shape(world, anon):
    body = anon.get("/restaurants").body
    entry = next(r for r in body["restaurants"] if r["id"] == world.rid)
    assert entry["name"] == world.restaurant["name"]
    assert entry["timezone"] == world.timezone
    assert set(entry.keys()) == {"id", "name", "timezone"}


def test_restaurant_detail_shape(world, anon):
    body = anon.get(f"/restaurants/{world.rid}").body
    assert body["slot_minutes"] == world.restaurant["slot_minutes"]
    assert body["reservation_duration_minutes"] == world.restaurant["reservation_duration_minutes"]
    assert body["cancellation_cutoff_minutes"] == world.restaurant["cancellation_cutoff_minutes"]
    assert len(body["opening_hours"]) == 7
    assert [t["id"] for t in body["tables"]] == ["t_1", "t_2", "t_3"]


def test_unknown_restaurant_is_404(world, anon):
    resp = anon.get("/restaurants/nope")
    assert resp.status == 404
    assert resp.error_code == "not_found"


def test_availability_slot_grid_steps_by_slot_minutes(world, anon):
    body = anon.get("/availability", params={
        "restaurant_id": world.rid, "date": world.date, "party_size": 2}).body
    times = [s["starts_at_local"].split("T")[1] for s in body["slots"]]
    # opens 18:00 closes 23:00, slot 30 min, duration 90 min -> last slot starts 21:30
    assert times == ["18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30"]


def test_availability_filters_by_capacity_in_fixture_order(world, anon):
    body = anon.get("/availability", params={
        "restaurant_id": world.rid, "date": world.date, "party_size": 4}).body
    assert body["slots"][0]["available_table_ids"] == ["t_2", "t_3"]

    body6 = anon.get("/availability", params={
        "restaurant_id": world.rid, "date": world.date, "party_size": 6}).body
    assert body6["slots"][0]["available_table_ids"] == ["t_3"]


def test_closed_day_returns_no_slots(reset, anon):
    reset({
        "users": [], "reservations": [],
        "restaurants": [{
            "id": "r_weekend", "name": "Weekend only", "timezone": "UTC",
            "slot_minutes": 30, "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
            "opening_hours": [{"weekday": "sat", "opens": "10:00", "closes": "14:00"}],
            "tables": [{"id": "t_1", "label": "1", "capacity": 2}],
        }],
    })
    # find the next Monday, guaranteed closed
    import datetime as dt
    d = dt.date.today()
    while d.weekday() != 0:
        d += dt.timedelta(days=1)
    body = anon.get("/availability", params={
        "restaurant_id": "r_weekend", "date": d.isoformat(), "party_size": 2}).body
    assert body["slots"] == []


def test_slot_with_no_free_table_still_appears_empty(world, anon, book):
    book(table_id="t_1", at="18:00", party_size=2)
    book(table_id="t_2", at="18:00", party_size=2)
    book(table_id="t_3", at="18:00", party_size=2)
    body = anon.get("/availability", params={
        "restaurant_id": world.rid, "date": world.date, "party_size": 2}).body
    slot = next(s for s in body["slots"] if s["starts_at_local"].endswith("18:00"))
    assert slot["available_table_ids"] == []


def test_half_open_interval_does_not_overlap_adjacent_slot(world, anon, book):
    """A 90-minute 19:00 booking must not block the 20:30 slot on the same table."""
    book(table_id="t_2", at="19:00", party_size=4)
    body = anon.get("/availability", params={
        "restaurant_id": world.rid, "date": world.date, "party_size": 4}).body
    slot = next(s for s in body["slots"] if s["starts_at_local"].endswith("20:30"))
    assert "t_2" in slot["available_table_ids"]


def test_availability_requires_all_three_params(world, anon):
    for missing in ("restaurant_id", "date", "party_size"):
        params = {"restaurant_id": world.rid, "date": world.date, "party_size": 2}
        del params[missing]
        resp = anon.get("/availability", params=params)
        assert resp.status == 422, f"missing {missing} should be 422, got {resp}"
        assert resp.error_code == "validation_failed"


def test_availability_unknown_restaurant_is_404(anon):
    resp = anon.get("/availability", params={
        "restaurant_id": "no_such", "date": "2026-01-01", "party_size": 2})
    assert resp.status == 404
