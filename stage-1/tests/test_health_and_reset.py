"""§3.2 health, §3.3 reset."""
from __future__ import annotations

import pytest


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


def test_reset_rejects_ids_longer_than_64_characters(anon):
    too_long = "u_" + "x" * 64
    resp = anon.post("/_test/reset", json_body={
        "users": [{"id": too_long, "email": "long@example.com",
                   "password": "correct horse", "display_name": "Long"}],
        "restaurants": [], "reservations": [],
    })
    assert resp.status == 422
    assert resp.error_code == "validation_failed"


@pytest.mark.parametrize("bad_reference", ["x", "lower01", "TOO-LONG-WITH-DASH"])
def test_reset_rejects_invalid_reservation_references(anon, bad_reference):
    resp = anon.post("/_test/reset", json_body={
        "users": [{"id": "u_a", "email": "r@example.com",
                   "password": "correct horse", "display_name": "A"}],
        "restaurants": [{
            "id": "r_x", "name": "X", "timezone": "UTC", "slot_minutes": 30,
            "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
            "opening_hours": [{"weekday": d, "opens": "00:00", "closes": "23:00"}
                               for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")],
            "tables": [{"id": "t_1", "label": "1", "capacity": 2}],
        }],
        "reservations": [{
            "id": "res_bad", "reference": bad_reference, "user_id": "u_a",
            "restaurant_id": "r_x", "table_id": "t_1",
            "starts_at_local": "2026-01-05T10:00", "party_size": 2,
        }],
    })
    assert resp.status == 422
    assert resp.error_code == "validation_failed"


def test_reset_rejects_opening_hours_that_cross_midnight(anon):
    resp = anon.post("/_test/reset", json_body={
        "users": [],
        "restaurants": [{
            "id": "r_bad", "name": "Bad", "timezone": "Europe/Berlin", "slot_minutes": 30,
            "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
            "opening_hours": [{"weekday": "fri", "opens": "22:00", "closes": "02:00"}],
            "tables": [{"id": "t_1", "label": "1", "capacity": 2}],
        }],
        "reservations": [],
    })
    assert resp.status == 422
    assert resp.error_code == "validation_failed"


def test_invalid_reset_fixture_leaves_prior_state_untouched(reset, anon, world):
    too_long = "x" * 65
    resp = anon.post("/_test/reset", json_body={
        "users": [{"id": too_long, "email": "bad@example.com",
                   "password": "correct horse", "display_name": "Bad"}],
        "restaurants": [], "reservations": [],
    })
    assert resp.status == 422
    # world's fixture (seeded by the `world` fixture before this call) must still be live
    assert anon.get("/restaurants").body["restaurants"][0]["id"] == world.rid
