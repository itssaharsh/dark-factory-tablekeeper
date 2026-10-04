"""§10 export/import."""
from __future__ import annotations

from conftest import new_key


def test_export_shape(world, anon):
    resp = anon.get("/_test/export")
    assert resp.status == 200
    assert resp.body["track"] == "tablekeeper"
    assert resp.body["format_version"] == 1
    assert isinstance(resp.body["state"], dict)


def test_import_requires_204(world, anon):
    snapshot = anon.get("/_test/export").body
    resp = anon.post("/_test/import", json_body=snapshot)
    assert resp.status == 204


def test_import_restores_a_booking_receipt(world, book, reset, anon):
    created = book(table_id="t_2", at="19:00", party_size=4).body
    snapshot = world.ada.get("/_test/export").body
    reset({"users": [], "restaurants": [], "reservations": []})
    assert world.ada.get(f"/reservations/{created['reference']}").status == 401

    restored = anon.post("/_test/import", json_body=snapshot)
    assert restored.status == 204
    fetched = world.ada.get(f"/reservations/{created['reference']}")
    assert fetched.status == 200
    assert fetched.body == created


def test_import_preserves_tokens_and_login(world, reset, anon):
    snapshot = anon.get("/_test/export").body
    reset({"users": [], "restaurants": [], "reservations": []})
    anon.post("/_test/import", json_body=snapshot)
    # ada's pre-existing token still works
    assert world.ada.get("/reservations").status == 200
    # and she can still log in with her original password
    login = anon.post("/auth/login", json_body={"email": "ada@example.com", "password": "correct horse"})
    assert login.status == 200


def test_import_preserves_idempotency_receipts(world, book, reset, anon):
    key = new_key()
    first = book(table_id="t_2", at="19:00", key=key)
    snapshot = world.ada.get("/_test/export").body
    reset({"users": [], "restaurants": [], "reservations": []})
    anon.post("/_test/import", json_body=snapshot)
    replay = book(table_id="t_2", at="19:00", key=key)
    assert replay.status == 200
    assert replay.body == first.body


def test_import_is_replacement_not_merge(world, book, reset, anon):
    created = book(table_id="t_2", at="19:00").body
    snapshot = world.ada.get("/_test/export").body
    # seed something extra that import must wipe out
    reset({
        "users": [{"id": "u_extra", "email": "extra@example.com",
                   "password": "correct horse", "display_name": "E"}],
        "restaurants": [], "reservations": [],
    })
    anon.post("/_test/import", json_body=snapshot)
    login = anon.post("/auth/login", json_body={"email": "extra@example.com", "password": "correct horse"})
    assert login.status == 401, "import must remove data that was not part of the export"
    fetched = world.ada.get(f"/reservations/{created['reference']}")
    assert fetched.status == 200


def test_repeated_import_does_not_duplicate(world, book, anon):
    book(table_id="t_2", at="19:00")
    snapshot = world.ada.get("/_test/export").body
    anon.post("/_test/import", json_body=snapshot)
    anon.post("/_test/import", json_body=snapshot)
    reservations = world.ada.get("/reservations").body["reservations"]
    assert len(reservations) == 1


def test_export_is_a_point_in_time_snapshot(world, book, anon):
    book(table_id="t_2", at="19:00")
    snapshot = world.ada.get("/_test/export").body
    book(table_id="t_1", at="19:00", party_size=2)
    assert len(snapshot["state"]["reservations"]) == 1


def test_reset_clears_imported_state(world, book, reset, anon):
    book(table_id="t_2", at="19:00")
    snapshot = world.ada.get("/_test/export").body
    anon.post("/_test/import", json_body=snapshot)
    reset({"users": [], "restaurants": [], "reservations": []})
    assert anon.get("/restaurants").body == {"restaurants": []}


def test_import_invalid_track_is_422_without_changing_state(world, anon):
    before = anon.get("/restaurants").body
    resp = anon.post("/_test/import", json_body={"track": "something-else", "format_version": 1, "state": {}})
    assert resp.status == 422
    assert anon.get("/restaurants").body == before


def test_import_invalid_version_is_422(world, anon):
    resp = anon.post("/_test/import", json_body={"track": "tablekeeper", "format_version": 2, "state": {}})
    assert resp.status == 422


def test_import_missing_state_is_422(world, anon):
    resp = anon.post("/_test/import", json_body={"track": "tablekeeper", "format_version": 1})
    assert resp.status == 422


def test_import_malformed_json_is_400(world, anon):
    resp = anon.raw("POST", "/_test/import", b"not json at all")
    assert resp.status == 400
