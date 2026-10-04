"""§3.2 health, §3.3 reset."""
from __future__ import annotations


def test_health_is_ok(anon):
    resp = anon.get("/health")
    assert resp.status == 200
    assert resp.body == {"status": "ok"}


def test_reset_replaces_all_state(reset, anon):
    reset({"restaurants": [{
        "id": "r_one", "name": "One", "timezone": "UTC", "slot_minutes": 30,
        "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
        "opening_hours": [], "tables": [],
    }], "users": [], "reservations": []})
    assert [r["id"] for r in anon.get("/restaurants").body["restaurants"]] == ["r_one"]

    reset({"restaurants": [{
        "id": "r_two", "name": "Two", "timezone": "UTC", "slot_minutes": 30,
        "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
        "opening_hours": [], "tables": [],
    }], "users": [], "reservations": []})
    ids = [r["id"] for r in anon.get("/restaurants").body["restaurants"]]
    assert ids == ["r_two"], "the first restaurant must be gone after the second reset"


def test_reset_is_repeatable(reset, anon):
    fixture = {"restaurants": [{
        "id": "r_x", "name": "X", "timezone": "UTC", "slot_minutes": 30,
        "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
        "opening_hours": [], "tables": [],
    }], "users": [], "reservations": []}
    reset(fixture)
    reset(fixture)
    assert [r["id"] for r in anon.get("/restaurants").body["restaurants"]] == ["r_x"]


def test_reset_clears_users_and_tokens(reset, anon, world):
    # world seeded ada/bob with valid tokens; a bare reset must invalidate them.
    reset({"restaurants": [], "users": [], "reservations": []})
    resp = world.ada.get("/reservations")
    assert resp.status == 401, "tokens from before the reset must no longer work"
