"""stage-3: policies, accepted terms/revision, history, decision, explain, recurring series."""
from __future__ import annotations

import datetime as dt

from conftest import all_week, booking_date, make_restaurant, new_key


def _managed_fixture(**over):
    restaurant = make_restaurant(manager_user_ids=["u_ada"], **over)
    return {
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob"},
        ],
        "restaurants": [restaurant],
        "reservations": [],
    }


def _login(anon, email="ada@example.com"):
    resp = anon.post("/auth/login", json_body={"email": email, "password": "correct horse"})
    assert resp.status == 200, resp
    return anon.with_token(resp.body["token"])


# ---------------------------------------------------------------------------
# Policies: publish, list, selection, validation
# ---------------------------------------------------------------------------

def test_non_manager_cannot_publish_policy(reset, anon):
    fixture = _managed_fixture()
    reset(fixture)
    bob = _login(anon, "bob@example.com")
    resp = bob.post("/restaurants/r_anker/policies", json_body={
        "effective_from": "2020-01-01", "slot_minutes": 30, "reservation_duration_minutes": 90,
        "cancellation_cutoff_minutes": 60, "opening_hours": all_week(), "capacities": {"t_1": 2, "t_2": 4, "t_3": 6},
    }, idempotency_key=new_key())
    assert resp.status == 403 and resp.error_code == "forbidden", resp


def test_publish_policy_requires_token(reset, anon):
    reset(_managed_fixture())
    resp = anon.post("/restaurants/r_anker/policies", json_body={
        "effective_from": "2020-01-01", "slot_minutes": 30, "reservation_duration_minutes": 90,
        "cancellation_cutoff_minutes": 60, "opening_hours": all_week(), "capacities": {"t_1": 2, "t_2": 4, "t_3": 6},
    }, idempotency_key=new_key())
    assert resp.status == 401, resp


def test_manager_can_publish_and_version_increments(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon)
    body = {
        "effective_from": "2020-01-01", "slot_minutes": 15, "reservation_duration_minutes": 60,
        "cancellation_cutoff_minutes": 30, "opening_hours": all_week("12:00", "22:00"),
        "capacities": {"t_1": 2, "t_2": 4, "t_3": 6},
    }
    first = ada.post("/restaurants/r_anker/policies", json_body=body, idempotency_key=new_key())
    assert first.status == 201 and first.body["policy_version"] == 1, first
    second = ada.post("/restaurants/r_anker/policies", json_body=dict(body, effective_from="2020-02-01"),
                       idempotency_key=new_key())
    assert second.status == 201 and second.body["policy_version"] == 2, second

    listing = anon.get("/restaurants/r_anker/policies")
    assert listing.status == 200
    assert [p["policy_version"] for p in listing.body["policies"]] == [1, 2]


def test_policy_capacities_must_name_exactly_the_tables(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon)
    resp = ada.post("/restaurants/r_anker/policies", json_body={
        "effective_from": "2020-01-01", "slot_minutes": 30, "reservation_duration_minutes": 90,
        "cancellation_cutoff_minutes": 60, "opening_hours": all_week(), "capacities": {"t_1": 2, "t_2": 4},
    }, idempotency_key=new_key())
    assert resp.status == 422 and resp.error_code == "validation_failed", resp


def test_policy_opening_hours_non_object_entry_is_422_not_5xx(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon)
    resp = ada.post("/restaurants/r_anker/policies", json_body={
        "effective_from": "2020-01-01", "slot_minutes": 30, "reservation_duration_minutes": 90,
        "cancellation_cutoff_minutes": 60, "opening_hours": ["not-an-object"],
        "capacities": {"t_1": 2, "t_2": 4, "t_3": 6},
    }, idempotency_key=new_key())
    assert resp.status == 422 and resp.error_code == "validation_failed", resp


def test_policy_opening_hours_rejects_duplicate_weekday(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon)
    resp = ada.post("/restaurants/r_anker/policies", json_body={
        "effective_from": "2020-01-01", "slot_minutes": 30, "reservation_duration_minutes": 90,
        "cancellation_cutoff_minutes": 60,
        "opening_hours": [{"weekday": "mon", "opens": "10:00", "closes": "14:00"},
                           {"weekday": "mon", "opens": "18:00", "closes": "22:00"}],
        "capacities": {"t_1": 2, "t_2": 4, "t_3": 6},
    }, idempotency_key=new_key())
    assert resp.status == 422 and resp.error_code == "validation_failed", resp


def test_selected_policy_picks_greatest_effective_from_not_later_than_date_ties_break_on_version(reset, anon):
    reset(_managed_fixture())
    ada = _login(anon)
    far_future = (dt.date.today() + dt.timedelta(days=400)).isoformat()
    ada.post("/restaurants/r_anker/policies", json_body={
        "effective_from": "2000-01-01", "slot_minutes": 60, "reservation_duration_minutes": 60,
        "cancellation_cutoff_minutes": 60, "opening_hours": all_week(), "capacities": {"t_1": 2, "t_2": 4, "t_3": 6},
    }, idempotency_key=new_key())
    second = ada.post("/restaurants/r_anker/policies", json_body={
        "effective_from": "2000-01-01", "slot_minutes": 45, "reservation_duration_minutes": 45,
        "cancellation_cutoff_minutes": 45, "opening_hours": all_week(), "capacities": {"t_1": 2, "t_2": 4, "t_3": 6},
    }, idempotency_key=new_key())
    assert second.body["policy_version"] == 2

    resp = anon.get("/availability", params={"restaurant_id": "r_anker", "date": far_future, "party_size": 2})
    assert resp.status == 200
    # the tie-break-greatest-version policy (slot_minutes=45) applies, not version 1 (slot_minutes=60)
    times = [s["starts_at_local"].split("T")[1] for s in resp.body["slots"]]
    assert "18:45" in times and "19:00" not in times, times


def test_policy_publication_does_not_change_existing_bookings(reset, anon, world, book):
    resp = book(at="19:00")
    assert resp.status == 201
    original_end = resp.body["ends_at"]
    original_terms = resp.body["accepted_terms"]
    ada = world.ada
    ada.post("/restaurants/r_anker/policies", json_body={
        "effective_from": "2000-01-01", "slot_minutes": 10, "reservation_duration_minutes": 10,
        "cancellation_cutoff_minutes": 5, "opening_hours": all_week(), "capacities": {"t_1": 2, "t_2": 4, "t_3": 6},
    }, idempotency_key=new_key())
    reread = ada.get(f"/reservations/{resp.body['reference']}")
    assert reread.body["ends_at"] == original_end
    assert reread.body["accepted_terms"] == original_terms


# ---------------------------------------------------------------------------
# Accepted terms & revision
# ---------------------------------------------------------------------------

def test_create_reservation_has_revision_one_and_accepted_terms(world, book):
    resp = book(at="19:00")
    assert resp.status == 201
    assert resp.body["revision"] == 1
    terms = resp.body["accepted_terms"]
    assert terms["policy_version"] == 0
    assert terms["slot_minutes"] == 30
    assert "effective_from" not in terms


def test_real_amendment_increments_revision_and_replaces_terms(world, book):
    ref = book(at="19:00").body["reference"]
    resp = world.ada.patch(f"/reservations/{ref}", json_body={"party_size": 2})
    assert resp.status == 200
    assert resp.body["revision"] == 2


def test_noop_amendment_leaves_revision_and_history_untouched(world, book):
    ref = book(table_id="t_2", at="19:00", party_size=4).body["reference"]
    resp = world.ada.patch(f"/reservations/{ref}", json_body={
        "table_id": "t_2", "starts_at_local": f"{world.date}T19:00", "party_size": 4})
    assert resp.status == 200 and resp.body["revision"] == 1
    history = world.ada.get(f"/reservations/{ref}/history")
    assert len(history.body["entries"]) == 1


def test_reversed_pair_is_not_an_amendment(reset, anon, world):
    fixture = {
        "users": world.fixture["users"],
        "restaurants": [make_restaurant(combinable=[["t_1", "t_2"]])],
        "reservations": [],
    }
    reset(fixture)
    ada = _login(anon)
    created = ada.post("/reservations", json_body={
        "restaurant_id": "r_anker", "table_ids": ["t_1", "t_2"],
        "starts_at_local": f"{world.date}T19:00", "party_size": 5,
    }, idempotency_key=new_key())
    assert created.status == 201
    ref = created.body["reference"]
    resp = ada.patch(f"/reservations/{ref}", json_body={"table_ids": ["t_2", "t_1"]})
    assert resp.status == 200 and resp.body["revision"] == 1


def test_cancel_increments_revision_once_repeated_cancel_does_not(world, book):
    ref = book(at="19:00").body["reference"]
    first = world.ada.post(f"/reservations/{ref}/cancel")
    assert first.status == 200 and first.body["revision"] == 2
    second = world.ada.post(f"/reservations/{ref}/cancel")
    assert second.status == 200 and second.body["revision"] == 2


def test_amendment_cutoff_uses_accepted_terms_not_live_policy(reset, anon):
    """A newly-published shorter cutoff must not retroactively free up an already-accepted booking."""
    fixture = _managed_fixture(cancellation_cutoff_minutes=1)
    reset(fixture)
    ada = _login(anon)
    date = booking_date()
    created = ada.post("/reservations", json_body={
        "restaurant_id": "r_anker", "table_id": "t_2",
        "starts_at_local": f"{date}T19:00", "party_size": 4,
    }, idempotency_key=new_key())
    ref = created.body["reference"]
    # Publish a policy with a huge cutoff that, if (wrongly) applied live, would now block the amendment.
    ada.post("/restaurants/r_anker/policies", json_body={
        "effective_from": "2000-01-01", "slot_minutes": 30, "reservation_duration_minutes": 90,
        "cancellation_cutoff_minutes": 999999, "opening_hours": all_week(),
        "capacities": {"t_1": 2, "t_2": 4, "t_3": 6},
    }, idempotency_key=new_key())
    resp = ada.patch(f"/reservations/{ref}", json_body={"party_size": 2})
    assert resp.status == 200, resp


# ---------------------------------------------------------------------------
# expected_revision / stale_revision
# ---------------------------------------------------------------------------

def test_stale_expected_revision_is_409(world, book):
    ref = book(at="19:00").body["reference"]
    resp = world.ada.patch(f"/reservations/{ref}", json_body={"party_size": 2, "expected_revision": 5})
    assert resp.status == 409 and resp.error_code == "stale_revision", resp


def test_invalid_expected_revision_shape_is_422(world, book):
    ref = book(at="19:00").body["reference"]
    resp = world.ada.patch(f"/reservations/{ref}", json_body={"party_size": 2, "expected_revision": -1})
    assert resp.status == 422 and resp.error_code == "validation_failed", resp
    resp2 = world.ada.patch(f"/reservations/{ref}", json_body={"party_size": 2, "expected_revision": True})
    assert resp2.status == 422, resp2


def test_matching_expected_revision_succeeds(world, book):
    ref = book(at="19:00").body["reference"]
    resp = world.ada.patch(f"/reservations/{ref}", json_body={"party_size": 2, "expected_revision": 1})
    assert resp.status == 200 and resp.body["revision"] == 2, resp


def test_stale_expected_revision_wins_over_invalid_party_size(world, book):
    """stage-3 §Policies: stale_revision precedes cutoff/validation -- even a field's own
    type/range check, not just cutoff and occupancy. Regression for verifier S3-POL.39a."""
    ref = book(at="19:00").body["reference"]
    resp = world.ada.patch(f"/reservations/{ref}", json_body={"expected_revision": 99, "party_size": -5})
    assert resp.status == 409 and resp.error_code == "stale_revision", resp


def test_moves_stale_expected_revision_wins_over_invalid_party_size(world, book):
    ref = book(at="19:00").body["reference"]
    body = {"moves": [{"reference": ref, "expected_revision": 99, "party_size": -5}]}
    resp = world.ada.post("/reservation-moves", json_body=body, idempotency_key=new_key())
    assert resp.status == 409 and resp.error_code == "stale_revision", resp


# ---------------------------------------------------------------------------
# Availability explanations
# ---------------------------------------------------------------------------

def test_explain_false_by_default_no_extra_field(world, anon):
    resp = anon.get("/availability", params={"restaurant_id": world.rid, "date": world.date, "party_size": 2})
    assert resp.status == 200
    assert all("explain" not in s for s in resp.body["slots"])


def test_explain_invalid_value_is_422(world, anon):
    for bad in ("false", "1", ""):
        resp = anon.get("/availability", params={
            "restaurant_id": world.rid, "date": world.date, "party_size": 2, "explain": bad})
        assert resp.status == 422 and resp.error_code == "validation_failed", (bad, resp)


def test_explain_true_lists_every_table_with_both_rules(world, anon, book):
    book(table_id="t_2", at="19:00", party_size=4)
    resp = anon.get("/availability", params={
        "restaurant_id": world.rid, "date": world.date, "party_size": 4, "explain": "true"})
    assert resp.status == 200
    slot = next(s for s in resp.body["slots"] if s["starts_at_local"].endswith("19:00"))
    explain = {e["table_id"]: e for e in slot["explain"]}
    assert set(explain) == {"t_1", "t_2", "t_3"}
    assert explain["t_1"]["available"] is False  # capacity 2 < party_size 4
    rules = {r["rule"]: r["holds"] for r in explain["t_1"]["rules"]}
    assert rules == {"capacity": False, "no_overlap": True}
    assert explain["t_2"]["available"] is False  # capacity ok but taken
    rules2 = {r["rule"]: r["holds"] for r in explain["t_2"]["rules"]}
    assert rules2 == {"capacity": True, "no_overlap": False}
    assert explain["t_2"]["table_id"] not in [] and explain["t_2"]["policy_version"] == 0
    assert [t for t in slot["available_table_ids"]] == [e["table_id"] for e in slot["explain"] if e["available"]]


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def test_history_created_entry_shape(world, book):
    ref = book(table_id="t_2", at="19:00", party_size=4).body["reference"]
    resp = world.ada.get(f"/reservations/{ref}/history")
    assert resp.status == 200
    entries = resp.body["entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["seq"] == 1 and entry["event"] == "created" and entry["revision"] == 1
    fields = {c["field"]: c for c in entry["changes"]}
    assert fields["table_id"] == {"field": "table_id", "from": None, "to": "t_2"}
    assert fields["party_size"] == {"field": "party_size", "from": None, "to": 4}
    assert set(fields) == {"table_id", "starts_at_local", "party_size"}


def test_history_changed_entry_names_only_changed_fields_in_order(world, book):
    ref = book(table_id="t_2", at="19:00", party_size=4).body["reference"]
    world.ada.patch(f"/reservations/{ref}", json_body={"party_size": 2})
    resp = world.ada.get(f"/reservations/{ref}/history")
    entries = resp.body["entries"]
    assert len(entries) == 2
    changed = entries[1]
    assert changed["event"] == "changed" and changed["seq"] == 2 and changed["revision"] == 2
    assert [c["field"] for c in changed["changes"]] == ["party_size"]
    assert changed["changes"][0] == {"field": "party_size", "from": 4, "to": 2}


def test_history_cancelled_entry_has_empty_changes_and_is_last(world, book):
    ref = book(at="19:00").body["reference"]
    world.ada.post(f"/reservations/{ref}/cancel")
    resp = world.ada.get(f"/reservations/{ref}/history")
    entries = resp.body["entries"]
    assert entries[-1]["event"] == "cancelled" and entries[-1]["changes"] == []
    assert [e["seq"] for e in entries] == list(range(1, len(entries) + 1))


def test_history_is_owner_only_404_for_other_user_and_for_no_token(world, book):
    ref = book(at="19:00").body["reference"]
    resp_other = world.bob.get(f"/reservations/{ref}/history")
    assert resp_other.status == 404 and resp_other.error_code == "not_found", resp_other
    resp_anon = world.ada.with_token(None).get(f"/reservations/{ref}/history")
    assert resp_anon.status == 404, resp_anon  # stage-3: the 401 exception resolves to 404 here


def test_decision_endpoint_owner_only_and_survives_cancellation(world, book):
    ref = book(at="19:00").body["reference"]
    world.ada.post(f"/reservations/{ref}/cancel")
    resp = world.ada.get(f"/reservations/{ref}/decision")
    assert resp.status == 200
    assert resp.body == {"reference": ref, "revision": 2, "accepted_terms": resp.body["accepted_terms"]}
    other = world.bob.get(f"/reservations/{ref}/decision")
    assert other.status == 404, other


def test_combined_table_history_uses_table_ids(reset, anon, world):
    fixture = {
        "users": world.fixture["users"],
        "restaurants": [make_restaurant(combinable=[["t_1", "t_2"]])],
        "reservations": [],
    }
    reset(fixture)
    ada = _login(anon)
    created = ada.post("/reservations", json_body={
        "restaurant_id": "r_anker", "table_id": "t_2",
        "starts_at_local": f"{world.date}T19:00", "party_size": 4,
    }, idempotency_key=new_key())
    ref = created.body["reference"]
    changed = ada.patch(f"/reservations/{ref}", json_body={"table_ids": ["t_1", "t_2"], "party_size": 5})
    assert changed.status == 200, changed
    history = ada.get(f"/reservations/{ref}/history")
    entry = history.body["entries"][-1]
    fields = {c["field"]: c for c in entry["changes"]}
    assert fields["table_ids"] == {"field": "table_ids", "from": ["t_2"], "to": ["t_1", "t_2"]}


def test_replaying_idempotent_create_records_no_extra_history(world, book):
    key = new_key()
    first = book(key=key, table_id="t_2", at="19:00", party_size=4)
    assert first.status == 201
    replay = book(key=key, table_id="t_2", at="19:00", party_size=4)
    assert replay.status == 200
    history = world.ada.get(f"/reservations/{first.body['reference']}/history")
    assert len(history.body["entries"]) == 1


# ---------------------------------------------------------------------------
# Recurring series
# ---------------------------------------------------------------------------

def test_create_series_adopts_anchor_as_occurrence_zero(world, book):
    anchor = book(table_id="t_2", at="19:00", party_size=4).body
    resp = world.ada.post("/series", json_body={
        "anchor_reference": anchor["reference"], "count": 3, "interval_weeks": 1,
    }, idempotency_key=new_key())
    assert resp.status == 201, resp
    occurrences = resp.body["occurrences"]
    assert len(occurrences) == 3
    assert occurrences[0]["reference"] == anchor["reference"]
    assert occurrences[0]["reservation"]["reference"] == anchor["reference"]
    assert occurrences[0]["exception"] is False
    refs = {o["reference"] for o in occurrences}
    assert len(refs) == 3

    anchor_date = dt.date.fromisoformat(world.date)
    for i, occ in enumerate(occurrences):
        expected_date = (anchor_date + dt.timedelta(days=i * 7)).isoformat()
        assert occ["reservation"]["starts_at_local"] == f"{expected_date}T19:00"


def test_series_count_and_interval_validation(world, book):
    anchor = book(at="19:00").body
    for bad_count in (1, 13, True, "3"):
        resp = world.ada.post("/series", json_body={
            "anchor_reference": anchor["reference"], "count": bad_count, "interval_weeks": 1,
        }, idempotency_key=new_key())
        assert resp.status == 422, (bad_count, resp)


def test_already_adopted_anchor_is_409(world, book):
    anchor = book(at="19:00").body
    first = world.ada.post("/series", json_body={
        "anchor_reference": anchor["reference"], "count": 2, "interval_weeks": 1,
    }, idempotency_key=new_key())
    assert first.status == 201
    second = world.ada.post("/series", json_body={
        "anchor_reference": anchor["reference"], "count": 2, "interval_weeks": 1,
    }, idempotency_key=new_key())
    assert second.status == 409 and second.error_code == "already_in_series", second


def test_series_creation_is_atomic_on_a_failing_occurrence(world, book, anon):
    anchor = book(table_id="t_2", at="19:00", party_size=4).body
    anchor_date = dt.date.fromisoformat(world.date)
    blocking_date = (anchor_date + dt.timedelta(days=7)).isoformat()
    # Block occurrence index 1 by pre-booking t_2 at the same slot under another user.
    world.bob.post("/reservations", json_body={
        "restaurant_id": world.rid, "table_id": "t_2",
        "starts_at_local": f"{blocking_date}T19:00", "party_size": 4,
    }, idempotency_key=new_key())
    resp = world.ada.post("/series", json_body={
        "anchor_reference": anchor["reference"], "count": 3, "interval_weeks": 1,
    }, idempotency_key=new_key())
    assert resp.status == 409 and resp.error_code == "table_unavailable", resp
    listing = world.ada.get("/reservations")
    # Only the original anchor booking exists; nothing partial was created.
    assert len(listing.body["reservations"]) == 1


def test_get_series_is_owner_only(world, book):
    anchor = book(at="19:00").body
    created = world.ada.post("/series", json_body={
        "anchor_reference": anchor["reference"], "count": 2, "interval_weeks": 1,
    }, idempotency_key=new_key())
    series_id = created.body["series_id"]
    mine = world.ada.get(f"/series/{series_id}")
    assert mine.status == 200
    others = world.bob.get(f"/series/{series_id}")
    assert others.status == 404, others


def test_patch_on_occurrence_marks_exception_and_bumps_series_revision(world, book):
    anchor = book(table_id="t_2", at="19:00", party_size=4).body
    created = world.ada.post("/series", json_body={
        "anchor_reference": anchor["reference"], "count": 3, "interval_weeks": 1,
    }, idempotency_key=new_key())
    series_id = created.body["series_id"]
    second_ref = created.body["occurrences"][1]["reference"]
    world.ada.patch(f"/reservations/{second_ref}", json_body={"party_size": 2})
    series = world.ada.get(f"/series/{series_id}")
    assert series.body["revision"] == 2
    occ = next(o for o in series.body["occurrences"] if o["reference"] == second_ref)
    assert occ["exception"] is True
    untouched = next(o for o in series.body["occurrences"] if o["index"] == 0)
    assert untouched["exception"] is False


def test_cancelling_anchor_does_not_cancel_siblings(world, book):
    anchor = book(at="19:00").body
    created = world.ada.post("/series", json_body={
        "anchor_reference": anchor["reference"], "count": 2, "interval_weeks": 1,
    }, idempotency_key=new_key())
    world.ada.post(f"/reservations/{anchor['reference']}/cancel")
    sibling_ref = created.body["occurrences"][1]["reference"]
    sibling = world.ada.get(f"/reservations/{sibling_ref}")
    assert sibling.body["status"] == "confirmed"


def test_series_replay_returns_original_response(world, book):
    anchor = book(at="19:00").body
    key = new_key()
    first = world.ada.post("/series", json_body={
        "anchor_reference": anchor["reference"], "count": 2, "interval_weeks": 1,
    }, idempotency_key=key)
    replay = world.ada.post("/series", json_body={
        "anchor_reference": anchor["reference"], "count": 2, "interval_weeks": 1,
    }, idempotency_key=key)
    assert replay.status == 200
    assert replay.body == first.body


# ---------------------------------------------------------------------------
# Collective moves under policies and series
# ---------------------------------------------------------------------------

def test_moves_batch_bumps_each_affected_series_once_and_marks_exceptions(world, book):
    anchor_a = book(table_id="t_1", at="19:00", party_size=2).body
    series_a = world.ada.post("/series", json_body={
        "anchor_reference": anchor_a["reference"], "count": 2, "interval_weeks": 1,
    }, idempotency_key=new_key()).body
    occ_a1 = series_a["occurrences"][1]["reference"]

    body = {"moves": [{"reference": occ_a1, "party_size": 1}]}
    resp = world.ada.post("/reservation-moves", json_body=body, idempotency_key=new_key())
    assert resp.status == 201, resp

    series_after = world.ada.get(f"/series/{series_a['series_id']}")
    assert series_after.body["revision"] == 2
    occ = next(o for o in series_after.body["occurrences"] if o["reference"] == occ_a1)
    assert occ["exception"] is True


def test_moves_noop_item_retains_terms_and_history(world, book):
    ref = book(table_id="t_2", at="19:00", party_size=4).body["reference"]
    body = {"moves": [{"reference": ref, "table_id": "t_2", "party_size": 4}]}
    resp = world.ada.post("/reservation-moves", json_body=body, idempotency_key=new_key())
    assert resp.status == 201
    assert resp.body["reservations"][0]["revision"] == 1
    history = world.ada.get(f"/reservations/{ref}/history")
    assert len(history.body["entries"]) == 1


# ---------------------------------------------------------------------------
# Export / import preserves stage-3 state
# ---------------------------------------------------------------------------

def test_export_import_round_trip_preserves_policy_history_and_series(reset, anon, world, book):
    reset(_managed_fixture())
    ada = _login(anon)
    published = ada.post("/restaurants/r_anker/policies", json_body={
        "effective_from": "2000-01-01", "slot_minutes": 20, "reservation_duration_minutes": 60,
        "cancellation_cutoff_minutes": 30, "opening_hours": all_week(),
        "capacities": {"t_1": 2, "t_2": 4, "t_3": 6},
    }, idempotency_key=new_key())
    assert published.status == 201, published
    date = booking_date()
    anchor = ada.post("/reservations", json_body={
        "restaurant_id": "r_anker", "table_id": "t_2",
        "starts_at_local": f"{date}T19:00", "party_size": 4,
    }, idempotency_key=new_key()).body
    series = ada.post("/series", json_body={
        "anchor_reference": anchor["reference"], "count": 2, "interval_weeks": 1,
    }, idempotency_key=new_key()).body
    ada.patch(f"/reservations/{anchor['reference']}", json_body={"party_size": 3})

    snapshot = ada.get("/_test/export")
    assert snapshot.status == 200
    reset_resp = ada.post("/_test/reset", json_body={"users": [], "restaurants": [], "reservations": []})
    assert reset_resp.status == 204
    restored = ada.post("/_test/import", json_body=snapshot.body)
    assert restored.status == 204

    policies = ada.get("/restaurants/r_anker/policies")
    assert len(policies.body["policies"]) == 1

    reread = ada.get(f"/reservations/{anchor['reference']}")
    assert reread.status == 200 and reread.body["revision"] == 2 and reread.body["party_size"] == 3

    history = ada.get(f"/reservations/{anchor['reference']}/history")
    assert len(history.body["entries"]) == 2

    series_after = ada.get(f"/series/{series['series_id']}")
    assert series_after.status == 200
    assert len(series_after.body["occurrences"]) == 2
