"""§1 overlap safety and §2 concurrency budget, exercised with real threads."""
from __future__ import annotations

import concurrent.futures
from collections import Counter

from conftest import new_key


def test_ten_racing_bookings_for_one_slot_yield_exactly_one_winner(reset, anon, world):
    users = [{"id": f"u_race_{i}", "email": f"race{i}@example.com",
              "password": "correct horse", "display_name": "R"} for i in range(10)]
    reset({**world.fixture, "users": world.fixture["users"] + users})
    clients = []
    for u in users:
        token = anon.post("/auth/login", json_body={
            "email": u["email"], "password": "correct horse"}).body["token"]
        clients.append(anon.with_token(token))

    body = {"restaurant_id": world.rid, "table_id": "t_2",
            "starts_at_local": f"{world.date}T19:00", "party_size": 4}

    def attempt(i):
        return clients[i].post("/reservations", json_body=body, idempotency_key=new_key())

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, range(10)))

    counts = Counter(r.status for r in results)
    assert counts[201] == 1, counts
    assert counts[409] == 9, counts
    assert 500 not in counts


def test_fifty_concurrent_mixed_requests_never_5xx(world, book, anon):
    book(table_id="t_1", at="19:00", party_size=2)

    def task(i):
        if i % 3 == 0:
            return anon.get("/availability", params={
                "restaurant_id": world.rid, "date": world.date, "party_size": 2})
        if i % 3 == 1:
            return world.ada.get("/reservations")
        return anon.get("/restaurants")

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(task, range(50)))

    assert all(r.status < 500 for r in results)


def test_concurrent_cancel_and_rebook_is_consistent(world, book):
    created = book(table_id="t_2", at="19:00", party_size=4).body
    ref = created["reference"]

    def cancel():
        return world.ada.post(f"/reservations/{ref}/cancel")

    def rebook():
        return world.ada.post("/reservations", json_body={
            "restaurant_id": world.rid, "table_id": "t_2",
            "starts_at_local": f"{world.date}T19:00", "party_size": 4},
            idempotency_key=new_key())

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        cancel_future = pool.submit(cancel)
        rebook_future = pool.submit(rebook)
        cancel_result = cancel_future.result()
        rebook_result = rebook_future.result()

    assert cancel_result.status == 200
    # the rebook either lands on the now-free table, or sees it still taken -- never a 5xx
    assert rebook_result.status in (201, 409)
