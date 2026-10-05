"""stage-4: seating replans (closures, lexicographic optimization) and recurring amendments."""
from __future__ import annotations

import datetime as dt
import zoneinfo

from conftest import all_week, booking_date, make_restaurant, new_key

TZ = zoneinfo.ZoneInfo("Europe/Berlin")


def _managed_fixture(**over):
    restaurant = make_restaurant(manager_user_ids=["u_mgr"], cancellation_cutoff_minutes=0, **over)
    return {
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob"},
            {"id": "u_mgr", "email": "mgr@example.com", "password": "correct horse", "display_name": "Mgr"},
        ],
        "restaurants": [restaurant],
        "reservations": [],
    }


def _login(anon, email):
    resp = anon.post("/auth/login", json_body={"email": email, "password": "correct horse"})
    assert resp.status == 200, resp
    return anon.with_token(resp.body["token"])


def _instant(date_str: str, hhmm: str) -> str:
    hour, minute = (int(x) for x in hhmm.split(":"))
    naive = dt.datetime.fromisoformat(f"{date_str}T{hhmm}:00")
    return naive.replace(tzinfo=TZ).isoformat()


def _book(client, rid, table_id, date_str, hhmm, party_size, key=None):
    return client.post("/reservations", json_body={
        "restaurant_id": rid, "table_id": table_id,
        "starts_at_local": f"{date_str}T{hhmm}", "party_size": party_size,
    }, idempotency_key=key or new_key())


# ---------------------------------------------------------------------------
# Replan preview: auth, validation
# ---------------------------------------------------------------------------

def test_replan_requires_manager(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    resp = ada.post("/restaurants/r_anker/replans", json_body={
        "table_id": "t_2", "from": _instant(date, "18:00"), "to": _instant(date, "23:00"),
    }, idempotency_key=new_key())
    assert resp.status == 403 and resp.error_code == "forbidden", resp


def test_replan_requires_token(reset, anon):
    reset(_managed_fixture())
    date = booking_date()
    resp = anon.post("/restaurants/r_anker/replans", json_body={
        "table_id": "t_2", "from": _instant(date, "18:00"), "to": _instant(date, "23:00"),
    }, idempotency_key=new_key())
    assert resp.status == 401, resp


def test_replan_unknown_table_is_404(reset, anon):
    reset(_managed_fixture())
    mgr = _login(anon, "mgr@example.com")
    date = booking_date()
    resp = mgr.post("/restaurants/r_anker/replans", json_body={
        "table_id": "t_nope", "from": _instant(date, "18:00"), "to": _instant(date, "23:00"),
    }, idempotency_key=new_key())
    assert resp.status == 404 and resp.error_code == "not_found", resp


def test_replan_invalid_interval_is_422(reset, anon):
    reset(_managed_fixture())
    mgr = _login(anon, "mgr@example.com")
    date = booking_date()
    resp = mgr.post("/restaurants/r_anker/replans", json_body={
        "table_id": "t_2", "from": _instant(date, "23:00"), "to": _instant(date, "18:00"),
    }, idempotency_key=new_key())
    assert resp.status == 422 and resp.error_code == "validation_failed", resp


def test_replan_offset_required_is_422(reset, anon):
    reset(_managed_fixture())
    mgr = _login(anon, "mgr@example.com")
    date = booking_date()
    resp = mgr.post("/restaurants/r_anker/replans", json_body={
        "table_id": "t_2", "from": f"{date}T18:00:00", "to": _instant(date, "23:00"),
    }, idempotency_key=new_key())
    assert resp.status == 422 and resp.error_code == "validation_failed", resp


# ---------------------------------------------------------------------------
# Replan preview: assignment logic
# ---------------------------------------------------------------------------

def test_replan_moves_only_the_forced_booking(reset, anon):
    """A booking on the closing table must move; one on an unaffected table should stay put
    when that minimizes the moved-booking count (stage-4's primary lex objective)."""
    reset(_managed_fixture())
    mgr = _login(anon, "mgr@example.com")
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    forced = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    assert forced.status == 201, forced
    other = _book(ada, "r_anker", "t_3", date, "21:00", 2)
    assert other.status == 201, other

    resp = mgr.post("/restaurants/r_anker/replans", json_body={
        "table_id": "t_2", "from": _instant(date, "18:00"), "to": _instant(date, "23:00"),
    }, idempotency_key=new_key())
    assert resp.status == 201, resp
    assert resp.body["moved_count"] == 1, resp.body
    by_ref = {a["reference"]: a for a in resp.body["assignments"]}
    assert by_ref[forced.body["reference"]]["changed"] is True
    assert "t_2" not in by_ref[forced.body["reference"]]["table_ids"]
    assert by_ref[other.body["reference"]]["changed"] is False
    assert by_ref[other.body["reference"]]["table_ids"] == ["t_3"]
    assert list(by_ref) == sorted(by_ref)  # reference order


def test_replan_no_feasible_plan_is_409(reset, anon):
    # Only t_1 (capacity 1) remains once t_2 closes -- too small for the party-of-4 booking,
    # whatever else gets reassigned, so no plan can satisfy every considered booking.
    restaurant = make_restaurant(
        manager_user_ids=["u_mgr"], cancellation_cutoff_minutes=0,
        tables=[{"id": "t_1", "label": "1", "capacity": 1}, {"id": "t_2", "label": "2", "capacity": 4}],
    )
    reset({"users": [
        {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
        {"id": "u_mgr", "email": "mgr@example.com", "password": "correct horse", "display_name": "Mgr"},
    ], "restaurants": [restaurant], "reservations": []})
    mgr = _login(anon, "mgr@example.com")
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    forced = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    assert forced.status == 201, forced

    resp = mgr.post("/restaurants/r_anker/replans", json_body={
        "table_id": "t_2", "from": _instant(date, "18:00"), "to": _instant(date, "23:00"),
    }, idempotency_key=new_key())
    assert resp.status == 409 and resp.error_code == "no_feasible_plan", resp


def test_replan_planning_limit_on_considered_bookings(reset, anon):
    restaurant = make_restaurant(
        manager_user_ids=["u_mgr"], cancellation_cutoff_minutes=0, slot_minutes=90,
        reservation_duration_minutes=90, opening_hours=all_week("08:00", "23:00"),
        tables=[{"id": "t_1", "label": "1", "capacity": 20}],
    )
    reset({"users": [
        {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
        {"id": "u_mgr", "email": "mgr@example.com", "password": "correct horse", "display_name": "Mgr"},
    ], "restaurants": [restaurant], "reservations": []})
    mgr = _login(anon, "mgr@example.com")
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    times = ["08:00", "09:30", "11:00", "12:30", "14:00", "15:30", "17:00"]
    for hhmm in times:
        resp = _book(ada, "r_anker", "t_1", date, hhmm, 2)
        assert resp.status == 201, resp

    resp = mgr.post("/restaurants/r_anker/replans", json_body={
        "table_id": "t_1", "from": _instant(date, "08:00"), "to": _instant(date, "18:30"),
    }, idempotency_key=new_key())
    assert resp.status == 422 and resp.error_code == "planning_limit", resp


# ---------------------------------------------------------------------------
# Replan apply
# ---------------------------------------------------------------------------

def test_replan_apply_moves_booking_and_bumps_revisions(reset, anon):
    reset(_managed_fixture())
    mgr = _login(anon, "mgr@example.com")
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    booked = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    assert booked.status == 201
    original_ends_at = booked.body["ends_at"]
    original_terms = booked.body["accepted_terms"]

    preview = mgr.post("/restaurants/r_anker/replans", json_body={
        "table_id": "t_2", "from": _instant(date, "18:00"), "to": _instant(date, "23:00"),
    }, idempotency_key=new_key())
    assert preview.status == 201
    revision_before = preview.body["restaurant_revision"]
    plan_id = preview.body["plan_id"]

    applied = mgr.post(f"/restaurants/r_anker/replans/{plan_id}/apply", json_body={}, idempotency_key=new_key())
    assert applied.status == 201, applied
    assert applied.body["restaurant_revision"] == revision_before + 1
    moved = next(r for r in applied.body["reservations"] if r["reference"] == booked.body["reference"])
    assert moved["table_ids"] != ["t_2"]
    assert moved["revision"] == 2
    assert moved["ends_at"] == original_ends_at
    assert moved["accepted_terms"] == original_terms

    history = ada.get(f"/reservations/{booked.body['reference']}/history")
    last = history.body["entries"][-1]
    assert last["event"] == "reassigned"
    assert last["plan_id"] == plan_id
    assert last["changes"] == [{"field": "table_ids", "from": ["t_2"], "to": moved["table_ids"]}]


def test_replan_apply_blocks_future_bookings_and_explain(reset, anon):
    reset(_managed_fixture())
    mgr = _login(anon, "mgr@example.com")
    ada = _login(anon, "ada@example.com")
    date = booking_date()

    preview = mgr.post("/restaurants/r_anker/replans", json_body={
        "table_id": "t_2", "from": _instant(date, "18:00"), "to": _instant(date, "23:00"),
    }, idempotency_key=new_key())
    assert preview.status == 201
    applied = mgr.post(f"/restaurants/r_anker/replans/{preview.body['plan_id']}/apply",
                        json_body={}, idempotency_key=new_key())
    assert applied.status == 201

    blocked = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    assert blocked.status == 409 and blocked.error_code == "table_unavailable", blocked

    avail = anon.get("/availability", params={
        "restaurant_id": "r_anker", "date": date, "party_size": 2, "explain": "true"})
    slot = next(s for s in avail.body["slots"] if s["starts_at_local"].endswith("19:00"))
    assert "t_2" not in slot["available_table_ids"]
    t2_explain = next(e for e in slot["explain"] if e["table_id"] == "t_2")
    assert t2_explain["available"] is False
    rules = {r["rule"]: r["holds"] for r in t2_explain["rules"]}
    assert rules["no_overlap"] is False


def test_replan_apply_stale_plan_is_409(reset, anon):
    reset(_managed_fixture())
    mgr = _login(anon, "mgr@example.com")
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    booked = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    assert booked.status == 201

    preview = mgr.post("/restaurants/r_anker/replans", json_body={
        "table_id": "t_2", "from": _instant(date, "18:00"), "to": _instant(date, "23:00"),
    }, idempotency_key=new_key())
    assert preview.status == 201

    # Any intervening restaurant-revision-bumping write invalidates the plan.
    other_book = _book(ada, "r_anker", "t_1", date, "19:00", 2)
    assert other_book.status == 201

    applied = mgr.post(f"/restaurants/r_anker/replans/{preview.body['plan_id']}/apply",
                        json_body={}, idempotency_key=new_key())
    assert applied.status == 409 and applied.error_code == "stale_plan", applied


def test_replan_apply_already_applied_different_key_is_409_same_key_replays(reset, anon):
    reset(_managed_fixture())
    mgr = _login(anon, "mgr@example.com")
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    booked = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    assert booked.status == 201

    preview = mgr.post("/restaurants/r_anker/replans", json_body={
        "table_id": "t_2", "from": _instant(date, "18:00"), "to": _instant(date, "23:00"),
    }, idempotency_key=new_key())
    plan_id = preview.body["plan_id"]
    key = new_key()
    first = mgr.post(f"/restaurants/r_anker/replans/{plan_id}/apply", json_body={}, idempotency_key=key)
    assert first.status == 201

    again_same_key = mgr.post(f"/restaurants/r_anker/replans/{plan_id}/apply", json_body={}, idempotency_key=key)
    assert again_same_key.status == 200 and again_same_key.body == first.body

    again_other_key = mgr.post(f"/restaurants/r_anker/replans/{plan_id}/apply",
                                json_body={}, idempotency_key=new_key())
    assert again_other_key.status == 409 and again_other_key.error_code == "plan_already_applied", again_other_key


def test_replan_unknown_plan_is_404(reset, anon):
    reset(_managed_fixture())
    mgr = _login(anon, "mgr@example.com")
    resp = mgr.post("/restaurants/r_anker/replans/plan_nope/apply", json_body={}, idempotency_key=new_key())
    assert resp.status == 404 and resp.error_code == "not_found", resp


def test_replan_apply_is_atomic_across_multiple_bookings(reset, anon):
    reset(_managed_fixture())
    mgr = _login(anon, "mgr@example.com")
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    a = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    b = _book(ada, "r_anker", "t_3", date, "21:00", 2)
    assert a.status == 201 and b.status == 201

    preview = mgr.post("/restaurants/r_anker/replans", json_body={
        "table_id": "t_2", "from": _instant(date, "18:00"), "to": _instant(date, "23:00"),
    }, idempotency_key=new_key())
    assert preview.status == 201
    applied = mgr.post(f"/restaurants/r_anker/replans/{preview.body['plan_id']}/apply",
                        json_body={}, idempotency_key=new_key())
    assert applied.status == 201
    refs_in_response = {r["reference"] for r in applied.body["reservations"]}
    assert refs_in_response == {a.body["reference"], b.body["reference"]}
    # b was never forced to move (see test_replan_moves_only_the_forced_booking); its revision
    # must remain untouched since "unmoved bookings gain nothing."
    b_after = ada.get(f"/reservations/{b.body['reference']}")
    assert b_after.body["revision"] == 1


# ---------------------------------------------------------------------------
# restaurant_revision bookkeeping across ordinary writes (regression)
# ---------------------------------------------------------------------------

def test_restaurant_revision_increments_for_ordinary_writes(reset, anon):
    reset(_managed_fixture())
    mgr = _login(anon, "mgr@example.com")
    ada = _login(anon, "ada@example.com")
    date = booking_date()

    def revision_via_preview():
        resp = mgr.post("/restaurants/r_anker/replans", json_body={
            "table_id": "t_1", "from": _instant(date, "18:00"), "to": _instant(date, "18:30"),
        }, idempotency_key=new_key())
        assert resp.status == 201, resp
        return resp.body["restaurant_revision"]

    assert revision_via_preview() == 0
    booked = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    assert booked.status == 201
    assert revision_via_preview() == 1
    amended = ada.patch(f"/reservations/{booked.body['reference']}", json_body={"party_size": 2})
    assert amended.status == 200
    assert revision_via_preview() == 2
    cancelled = ada.post(f"/reservations/{booked.body['reference']}/cancel")
    assert cancelled.status == 200
    assert revision_via_preview() == 3


# ---------------------------------------------------------------------------
# Series amendment
# ---------------------------------------------------------------------------

def test_series_amend_moves_eligible_occurrences_only(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    anchor = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    assert anchor.status == 201
    series = ada.post("/series", json_body={
        "anchor_reference": anchor.body["reference"], "count": 3, "interval_weeks": 1,
    }, idempotency_key=new_key())
    assert series.status == 201
    sid = series.body["series_id"]

    resp = ada.post(f"/series/{sid}/amend", json_body={
        "expected_revision": 1, "from_index": 1, "local_time": "20:00",
    }, idempotency_key=new_key())
    assert resp.status == 201, resp
    assert resp.body["revision"] == 2
    occs = {o["index"]: o for o in resp.body["occurrences"]}
    assert occs[0]["reservation"]["starts_at_local"].endswith("19:00")
    assert occs[0]["reservation"]["revision"] == 1
    assert occs[1]["reservation"]["starts_at_local"].endswith("20:00")
    assert occs[1]["reservation"]["revision"] == 2
    assert occs[2]["reservation"]["starts_at_local"].endswith("20:00")
    assert occs[2]["reservation"]["revision"] == 2
    # series-amend changes must NOT mark occurrences as diner exceptions
    assert occs[1]["exception"] is False
    assert occs[2]["exception"] is False


def test_series_amend_excludes_cancelled_and_exception_occurrences(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    anchor = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    series = ada.post("/series", json_body={
        "anchor_reference": anchor.body["reference"], "count": 3, "interval_weeks": 1,
    }, idempotency_key=new_key())
    sid = series.body["series_id"]
    occ1_ref = series.body["occurrences"][1]["reference"]

    # Mark occurrence 1 as an exception via an ordinary PATCH; occurrence 2 stays eligible.
    ada.patch(f"/reservations/{occ1_ref}", json_body={"party_size": 2})

    resp = ada.post(f"/series/{sid}/amend", json_body={
        "expected_revision": 2, "from_index": 0, "local_time": "20:30",
    }, idempotency_key=new_key())
    assert resp.status == 201, resp
    occs = {o["index"]: o for o in resp.body["occurrences"]}
    assert occs[0]["reservation"]["starts_at_local"].endswith("20:30")  # not excepted -> moved
    assert occs[1]["reservation"]["starts_at_local"].endswith("19:00")  # exception -> untouched
    assert occs[2]["reservation"]["starts_at_local"].endswith("20:30")


def test_series_amend_stale_revision_precedes_field_validation(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    anchor = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    series = ada.post("/series", json_body={
        "anchor_reference": anchor.body["reference"], "count": 2, "interval_weeks": 1,
    }, idempotency_key=new_key())
    sid = series.body["series_id"]

    resp = ada.post(f"/series/{sid}/amend", json_body={
        "expected_revision": 99, "from_index": 999, "local_time": "25:99",
    }, idempotency_key=new_key())
    assert resp.status == 409 and resp.error_code == "stale_revision", resp


def test_series_amend_invalid_local_time_is_422(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    anchor = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    series = ada.post("/series", json_body={
        "anchor_reference": anchor.body["reference"], "count": 2, "interval_weeks": 1,
    }, idempotency_key=new_key())
    sid = series.body["series_id"]
    resp = ada.post(f"/series/{sid}/amend", json_body={
        "expected_revision": 1, "from_index": 0, "local_time": "9:00",
    }, idempotency_key=new_key())
    assert resp.status == 422 and resp.error_code == "validation_failed", resp


def test_series_amend_all_noop_keeps_revisions_unchanged(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    anchor = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    series = ada.post("/series", json_body={
        "anchor_reference": anchor.body["reference"], "count": 2, "interval_weeks": 1,
    }, idempotency_key=new_key())
    sid = series.body["series_id"]
    resp = ada.post(f"/series/{sid}/amend", json_body={
        "expected_revision": 1, "from_index": 0, "local_time": "19:00",
    }, idempotency_key=new_key())
    assert resp.status == 201
    assert resp.body["revision"] == 1
    for occ in resp.body["occurrences"]:
        assert occ["reservation"]["revision"] == 1


def test_series_amend_requires_token(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    anchor = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    series = ada.post("/series", json_body={
        "anchor_reference": anchor.body["reference"], "count": 2, "interval_weeks": 1,
    }, idempotency_key=new_key())
    sid = series.body["series_id"]
    resp = anon.post(f"/series/{sid}/amend", json_body={
        "expected_revision": 1, "from_index": 0, "local_time": "20:00",
    }, idempotency_key=new_key())
    assert resp.status == 401, resp


def test_series_amend_unknown_series_is_404(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon, "ada@example.com")
    resp = ada.post("/series/ser_nope/amend", json_body={
        "expected_revision": 1, "from_index": 0, "local_time": "20:00",
    }, idempotency_key=new_key())
    assert resp.status == 404, resp


def test_series_amend_another_owner_is_404(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon, "ada@example.com")
    bob = _login(anon, "bob@example.com")
    date = booking_date()
    anchor = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    series = ada.post("/series", json_body={
        "anchor_reference": anchor.body["reference"], "count": 2, "interval_weeks": 1,
    }, idempotency_key=new_key())
    sid = series.body["series_id"]
    resp = bob.post(f"/series/{sid}/amend", json_body={
        "expected_revision": 1, "from_index": 0, "local_time": "20:00",
    }, idempotency_key=new_key())
    assert resp.status == 404, resp


def test_series_amend_replay_returns_original_response(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    anchor = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    series = ada.post("/series", json_body={
        "anchor_reference": anchor.body["reference"], "count": 2, "interval_weeks": 1,
    }, idempotency_key=new_key())
    sid = series.body["series_id"]
    key = new_key()
    first = ada.post(f"/series/{sid}/amend", json_body={
        "expected_revision": 1, "from_index": 0, "local_time": "20:00",
    }, idempotency_key=key)
    assert first.status == 201
    replay = ada.post(f"/series/{sid}/amend", json_body={
        "expected_revision": 1, "from_index": 0, "local_time": "20:00",
    }, idempotency_key=key)
    assert replay.status == 200 and replay.body == first.body


# ---------------------------------------------------------------------------
# Export / import preserves closures and plans
# ---------------------------------------------------------------------------

def test_export_import_round_trip_preserves_closures(reset, anon):
    reset(_managed_fixture())
    mgr = _login(anon, "mgr@example.com")
    ada = _login(anon, "ada@example.com")
    date = booking_date()
    booked = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    assert booked.status == 201
    preview = mgr.post("/restaurants/r_anker/replans", json_body={
        "table_id": "t_2", "from": _instant(date, "18:00"), "to": _instant(date, "23:00"),
    }, idempotency_key=new_key())
    applied = mgr.post(f"/restaurants/r_anker/replans/{preview.body['plan_id']}/apply",
                        json_body={}, idempotency_key=new_key())
    assert applied.status == 201

    snapshot = ada.get("/_test/export")
    assert snapshot.status == 200
    reset_resp = ada.post("/_test/reset", json_body={"users": [], "restaurants": [], "reservations": []})
    assert reset_resp.status == 204
    restored = ada.post("/_test/import", json_body=snapshot.body)
    assert restored.status == 204

    blocked = _book(ada, "r_anker", "t_2", date, "19:00", 4)
    assert blocked.status == 409 and blocked.error_code == "table_unavailable", blocked

    reread = ada.get(f"/reservations/{booked.body['reference']}")
    assert reread.status == 200
    assert reread.body["table_ids"] != ["t_2"]
    history = ada.get(f"/reservations/{booked.body['reference']}/history")
    assert history.body["entries"][-1]["event"] == "reassigned"
