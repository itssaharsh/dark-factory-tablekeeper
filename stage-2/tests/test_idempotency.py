"""§7 idempotency, shared by POST /reservations and POST /reservation-moves."""
from __future__ import annotations

import concurrent.futures

from conftest import new_key


def test_first_use_is_201_replay_is_200_with_same_body(world, book):
    key = new_key()
    first = book(table_id="t_2", at="19:00", key=key)
    assert first.status == 201
    replay = book(table_id="t_2", at="19:00", key=key)
    assert replay.status == 200
    assert replay.body == first.body


def test_same_key_different_body_is_409(world, book):
    key = new_key()
    assert book(table_id="t_2", at="19:00", key=key).status == 201
    resp = book(table_id="t_3", at="19:00", key=key)
    assert resp.status == 409
    assert resp.error_code == "idempotency_key_reuse"


def test_key_reused_after_4xx_failure_is_a_first_use(world, book):
    key = new_key()
    failed = book(table_id="t_1", party_size=99, key=key)  # exceeds capacity -> 422
    assert failed.status == 422
    retried = book(table_id="t_1", party_size=2, key=key)
    assert retried.status == 201, "the key must be reusable after a failed first attempt"


def test_idempotency_key_scoped_to_user(world, book):
    key = "shared-key"
    resp_ada = book(client=world.ada, table_id="t_1", at="19:00", party_size=2, key=key)
    resp_bob = book(client=world.bob, table_id="t_2", at="19:00", party_size=2, key=key)
    assert resp_ada.status == 201
    assert resp_bob.status == 201, "two different users may reuse the same key independently"


def test_same_key_different_path_is_a_different_request(world, book):
    key = "cross-path-key"
    created = book(table_id="t_2", at="19:00", key=key)
    assert created.status == 201
    ref = created.body["reference"]
    moved = world.ada.post("/reservation-moves", json_body={
        "moves": [{"reference": ref, "table_id": "t_3"}]}, idempotency_key=key)
    assert moved.status == 201, "same key string on a different path must succeed as a new request"


def test_idempotency_key_too_long_is_422(world, book):
    resp = book(table_id="t_2", at="19:00", key="x" * 256)
    assert resp.status == 422
    assert resp.error_code == "validation_failed"


def test_idempotency_resolved_before_field_validation(world, book):
    """A used key with a different (and otherwise-invalid) body is still a conflict, not a field error."""
    key = new_key()
    assert book(table_id="t_2", at="19:00", key=key).status == 201
    resp = book(party_size="not-a-number", key=key)
    assert resp.status == 409
    assert resp.error_code == "idempotency_key_reuse"


def test_concurrent_identical_requests_commit_exactly_once(world):
    key = new_key()
    body = {"restaurant_id": world.rid, "table_id": "t_2",
            "starts_at_local": f"{world.date}T19:00", "party_size": 4}

    def send(_):
        return world.ada.post("/reservations", json_body=body, idempotency_key=key)

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(send, range(12)))

    statuses = [r.status for r in results]
    assert statuses.count(201) == 1, statuses
    assert statuses.count(200) == 11, statuses
    first = results[0].body
    assert all(r.body == first for r in results), "every response body must be identical"


def test_replay_survives_cancellation(world, book):
    key = new_key()
    created = book(table_id="t_2", at="19:00", key=key)
    ref = created.body["reference"]
    world.ada.post(f"/reservations/{ref}/cancel")
    replay = book(table_id="t_2", at="19:00", key=key)
    assert replay.status == 200
    assert replay.body == created.body, "a replay returns the original response even after cancellation"
