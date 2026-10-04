"""Shared fixtures: start the real service once per session, talk HTTP to it."""
from __future__ import annotations

import datetime as dt
import pathlib
import secrets
import sys
import threading
import zoneinfo
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.server import build_server  # noqa: E402
from httpclient import Client  # noqa: E402

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@pytest.fixture(scope="session")
def _port():
    server = build_server(0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()


@pytest.fixture
def anon(_port):
    return Client(_port)


@pytest.fixture
def reset(anon):
    def _reset(fixture: dict) -> dict:
        resp = anon.post("/_test/reset", json_body=fixture)
        assert resp.status == 204, resp
        return fixture
    return _reset


def all_week(opens: str = "18:00", closes: str = "23:00") -> list[dict]:
    return [{"weekday": d, "opens": opens, "closes": closes} for d in WEEKDAYS]


def make_restaurant(rid: str = "r_anker", **over) -> dict:
    base = {
        "id": rid, "name": "Zum Anker", "timezone": "Europe/Berlin",
        "slot_minutes": 30, "reservation_duration_minutes": 90,
        "cancellation_cutoff_minutes": 120, "opening_hours": all_week(),
        "tables": [{"id": "t_1", "label": "1", "capacity": 2},
                   {"id": "t_2", "label": "2", "capacity": 4},
                   {"id": "t_3", "label": "3", "capacity": 6}],
    }
    base.update(over)
    return base


def booking_date(timezone: str = "Europe/Berlin", lead: int = 5) -> str:
    today = dt.datetime.now(zoneinfo.ZoneInfo(timezone)).date()
    return (today + dt.timedelta(days=lead)).isoformat()


def new_key() -> str:
    return secrets.token_hex(8)


@pytest.fixture
def world(reset, anon):
    restaurant = make_restaurant()
    fixture = {
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob"},
        ],
        "restaurants": [restaurant],
        "reservations": [],
    }
    reset(fixture)
    date = booking_date(restaurant["timezone"])
    ada_login = anon.post("/auth/login", json_body={"email": "ada@example.com", "password": "correct horse"})
    bob_login = anon.post("/auth/login", json_body={"email": "bob@example.com", "password": "correct horse"})
    return SimpleNamespace(
        fixture=fixture, restaurant=restaurant, rid=restaurant["id"],
        timezone=restaurant["timezone"], date=date,
        ada=anon.with_token(ada_login.body["token"]),
        bob=anon.with_token(bob_login.body["token"]),
    )


@pytest.fixture
def book(world):
    def _book(client=None, *, table_id="t_2", at="19:00", party_size=4, key=None,
              restaurant_id=None, starts_at_local=None, **extra):
        body = {
            "restaurant_id": restaurant_id or world.rid,
            "table_id": table_id,
            "starts_at_local": starts_at_local or f"{world.date}T{at}",
            "party_size": party_size,
        }
        body.update(extra)
        return (client or world.ada).post("/reservations", json_body=body,
                                           idempotency_key=key or new_key())
    return _book
