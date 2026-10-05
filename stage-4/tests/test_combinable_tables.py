"""stage-2 §Combined tables: declared pairs, available_options, table_ids."""
from __future__ import annotations

from conftest import new_key


def _reset_combinable(reset):
    fixture = {
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob"},
        ],
        "restaurants": [{
            "id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin",
            "slot_minutes": 30, "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
            "opening_hours": [{"weekday": d, "opens": "18:00", "closes": "23:00"}
                               for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")],
            "tables": [{"id": "t_1", "label": "1", "capacity": 2},
                       {"id": "t_2", "label": "2", "capacity": 4},
                       {"id": "t_3", "label": "3", "capacity": 6}],
            "combinable": [["t_1", "t_2"], ["t_2", "t_3"]],
        }],
        "reservations": [],
    }
    reset(fixture)
    return fixture


def test_available_options_lists_singles_then_pairs_in_combinable_order(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    date = world.date
    body = anon.get("/availability", params={
        "restaurant_id": rid, "date": date, "party_size": 6}).body
    slot = body["slots"][0]
    assert slot["available_table_ids"] == ["t_3"]
    assert slot["available_options"] == [
        {"table_ids": ["t_3"], "capacity": 6},
        {"table_ids": ["t_1", "t_2"], "capacity": 6},
        {"table_ids": ["t_2", "t_3"], "capacity": 10},
    ]


def test_available_options_excludes_undersized_pairs(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    body = anon.get("/availability", params={
        "restaurant_id": rid, "date": world.date, "party_size": 7}).body
    slot = body["slots"][0]
    # only t_2+t_3 (capacity 10) is big enough; t_1+t_2 (capacity 6) is not
    assert slot["available_options"] == [{"table_ids": ["t_2", "t_3"], "capacity": 10}]


def _login(anon, email):
    return anon.post("/auth/login", json_body={"email": email, "password": "correct horse"}).body["token"]


def test_booking_a_pair_returns_table_ids_without_table_id(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    resp = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_ids": ["t_2", "t_1"],
        "starts_at_local": f"{world.date}T19:00", "party_size": 6}, idempotency_key=new_key())
    assert resp.status == 201
    assert resp.body["table_ids"] == ["t_1", "t_2"], "canonical (combinable) order"
    assert "table_id" not in resp.body


def test_booking_a_single_table_still_returns_table_id(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    resp = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_id": "t_3",
        "starts_at_local": f"{world.date}T19:00", "party_size": 2}, idempotency_key=new_key())
    assert resp.status == 201
    assert resp.body["table_ids"] == ["t_3"]
    assert resp.body["table_id"] == "t_3"


def test_undeclared_pair_is_combination_not_allowed(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    resp = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_ids": ["t_1", "t_3"],
        "starts_at_local": f"{world.date}T19:00", "party_size": 2}, idempotency_key=new_key())
    assert resp.status == 422
    assert resp.error_code == "combination_not_allowed"


def test_combining_is_not_transitive(reset, anon, world):
    """[t_1,t_2] and [t_2,t_3] declared does not make {t_1,t_3} bookable."""
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    resp = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_ids": ["t_3", "t_1"],
        "starts_at_local": f"{world.date}T19:00", "party_size": 2}, idempotency_key=new_key())
    assert resp.status == 422
    assert resp.error_code == "combination_not_allowed"


def test_more_than_two_tables_is_combination_not_allowed(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    resp = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_ids": ["t_1", "t_2", "t_3"],
        "starts_at_local": f"{world.date}T19:00", "party_size": 2}, idempotency_key=new_key())
    assert resp.status == 422
    assert resp.error_code == "combination_not_allowed"


def test_duplicate_table_id_in_set_is_validation_failed(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    resp = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_ids": ["t_1", "t_1"],
        "starts_at_local": f"{world.date}T19:00", "party_size": 2}, idempotency_key=new_key())
    assert resp.status == 422
    assert resp.error_code == "validation_failed"


def test_both_table_id_and_table_ids_is_validation_failed(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    resp = client.request("POST", "/reservations", json_body={
        "restaurant_id": rid, "table_id": "t_1", "table_ids": ["t_1"],
        "starts_at_local": f"{world.date}T19:00", "party_size": 2}, idempotency_key=new_key())
    assert resp.status == 422
    assert resp.error_code == "validation_failed"


def test_party_size_vs_summed_capacity(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    ok = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_ids": ["t_1", "t_2"],  # capacities 2+4=6
        "starts_at_local": f"{world.date}T19:00", "party_size": 6}, idempotency_key=new_key())
    assert ok.status == 201
    too_big = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_ids": ["t_1", "t_2"],
        "starts_at_local": f"{world.date}T20:30", "party_size": 7}, idempotency_key=new_key())
    assert too_big.status == 422
    assert too_big.error_code == "party_exceeds_capacity"


def test_single_table_within_a_booked_pair_is_unavailable(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    assert client.post("/reservations", json_body={
        "restaurant_id": rid, "table_ids": ["t_1", "t_2"],
        "starts_at_local": f"{world.date}T19:00", "party_size": 4}, idempotency_key=new_key()).status == 201
    conflict = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_id": "t_2",
        "starts_at_local": f"{world.date}T19:00", "party_size": 2}, idempotency_key=new_key())
    assert conflict.status == 409
    assert conflict.error_code == "table_unavailable"


def test_pair_overlapping_a_booked_single_is_unavailable(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    assert client.post("/reservations", json_body={
        "restaurant_id": rid, "table_id": "t_2",
        "starts_at_local": f"{world.date}T19:00", "party_size": 2}, idempotency_key=new_key()).status == 201
    conflict = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_ids": ["t_1", "t_2"],
        "starts_at_local": f"{world.date}T19:00", "party_size": 4}, idempotency_key=new_key())
    assert conflict.status == 409
    assert conflict.error_code == "table_unavailable"


def test_cancel_frees_every_table_in_the_set(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    booked = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_ids": ["t_1", "t_2"],
        "starts_at_local": f"{world.date}T19:00", "party_size": 4}, idempotency_key=new_key()).body
    client.post(f"/reservations/{booked['reference']}/cancel")
    body = anon.get("/availability", params={
        "restaurant_id": rid, "date": world.date, "party_size": 2}).body
    slot = next(s for s in body["slots"] if s["starts_at_local"].endswith("19:00"))
    assert slot["available_table_ids"] == ["t_1", "t_2", "t_3"]


def test_patch_can_change_to_a_combination(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    booked = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_id": "t_1",
        "starts_at_local": f"{world.date}T19:00", "party_size": 2}, idempotency_key=new_key()).body
    resp = client.patch(f"/reservations/{booked['reference']}", json_body={
        "table_ids": ["t_2", "t_3"], "party_size": 10})
    assert resp.status == 200
    assert resp.body["table_ids"] == ["t_2", "t_3"]
    assert "table_id" not in resp.body


def test_moves_accept_table_ids_per_move(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    a = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_id": "t_1",
        "starts_at_local": f"{world.date}T19:00", "party_size": 2}, idempotency_key=new_key()).body
    resp = client.post("/reservation-moves", json_body={
        "moves": [{"reference": a["reference"], "table_ids": ["t_1", "t_2"], "party_size": 6}]},
        idempotency_key=new_key())
    assert resp.status == 201
    assert resp.body["reservations"][0]["table_ids"] == ["t_1", "t_2"]


def test_moves_reject_overlap_among_resulting_combined_bookings(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    a = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_id": "t_1",
        "starts_at_local": f"{world.date}T19:00", "party_size": 2}, idempotency_key=new_key()).body
    b = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_id": "t_3",
        "starts_at_local": f"{world.date}T19:00", "party_size": 2}, idempotency_key=new_key()).body
    # move a onto [t_2,t_3] -- overlaps b (t_3) which is also in this batch
    resp = client.post("/reservation-moves", json_body={"moves": [
        {"reference": a["reference"], "table_ids": ["t_2", "t_3"], "party_size": 6},
    ]}, idempotency_key=new_key())
    assert resp.status == 409
    assert resp.error_code == "table_unavailable"


def test_fixture_may_seed_reservation_with_table_ids(reset, anon):
    reset({
        "users": [{"id": "u_a", "email": "a@example.com", "password": "correct horse", "display_name": "A"}],
        "restaurants": [{
            "id": "r_x", "name": "X", "timezone": "UTC", "slot_minutes": 30,
            "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
            "opening_hours": [{"weekday": d, "opens": "00:00", "closes": "23:00"}
                               for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")],
            "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 2}],
            "combinable": [["t_1", "t_2"]],
        }],
        "reservations": [{
            "id": "res_seed", "reference": "SEEDREF1", "user_id": "u_a",
            "restaurant_id": "r_x", "table_ids": ["t_1", "t_2"],
            "starts_at_local": "2026-01-05T10:00", "party_size": 4,
        }],
    })
    token = anon.post("/auth/login", json_body={
        "email": "a@example.com", "password": "correct horse"}).body["token"]
    client = anon.with_token(token)
    fetched = client.get("/reservations/SEEDREF1")
    assert fetched.status == 200
    assert fetched.body["table_ids"] == ["t_1", "t_2"]


def test_import_accepts_a_stage_1_shaped_export(reset, anon):
    """A stage-1 export has no `combinable` and a singular `table_id` per reservation."""
    stage1_state = {
        "users": {"u_ada": {"id": "u_ada", "email": "ada@example.com",
                             "password_hash": "scrypt$16384$8$1$00$00", "display_name": "Ada"}},
        "tokens": {"tok-ada": "u_ada"},
        "restaurants": {"r_anker": {
            "id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin",
            "slot_minutes": 30, "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
            "opening_hours": [{"weekday": "mon", "opens": "18:00", "closes": "23:00"}],
            "tables": [{"id": "t_1", "label": "1", "capacity": 2}],
            # no "combinable" key at all -- this is the stage-1 shape
        }},
        "reservations": {"res_1": {
            "id": "res_1", "reference": "OLDREF01", "user_id": "u_ada",
            "restaurant_id": "r_anker", "table_id": "t_1",  # singular, no table_ids
            "party_size": 2, "status": "confirmed", "starts_at_local": "2026-01-05T19:00",
            "starts_at": "2026-01-05T18:00:00+00:00", "ends_at": "2026-01-05T19:30:00+00:00",
            "created_at": "2026-01-01T00:00:00+00:00",
        }},
        "idempotency": [],
    }
    reset({"users": [], "restaurants": [], "reservations": []})
    resp = anon.post("/_test/import", json_body={
        "track": "tablekeeper", "format_version": 1, "state": stage1_state})
    assert resp.status == 204
    fetched = anon.with_token("tok-ada").get("/reservations/OLDREF01")
    assert fetched.status == 200
    assert fetched.body["table_ids"] == ["t_1"]
    assert fetched.body["table_id"] == "t_1"
    detail = anon.get("/restaurants/r_anker").body
    assert detail["combinable"] == []


def test_export_import_preserves_combinable_and_table_ids(reset, anon, world):
    fixture = _reset_combinable(reset)
    rid = fixture["restaurants"][0]["id"]
    client = anon.with_token(_login(anon, "ada@example.com"))
    booked = client.post("/reservations", json_body={
        "restaurant_id": rid, "table_ids": ["t_1", "t_2"],
        "starts_at_local": f"{world.date}T19:00", "party_size": 4}, idempotency_key=new_key()).body
    snapshot = anon.get("/_test/export").body
    reset({"users": [], "restaurants": [], "reservations": []})
    restored = anon.post("/_test/import", json_body=snapshot)
    assert restored.status == 204
    refetched = client.get(f"/reservations/{booked['reference']}")
    assert refetched.body == booked
    detail = anon.get(f"/restaurants/{rid}").body
    assert detail["combinable"] == [["t_1", "t_2"], ["t_2", "t_3"]]
