"""§6 authentication."""
from __future__ import annotations

import pytest


def test_signup_returns_usable_token(reset, anon):
    reset({"restaurants": [], "users": [], "reservations": []})
    resp = anon.post("/auth/signup", json_body={
        "email": "new@example.com", "password": "correct horse", "display_name": "New"})
    assert resp.status == 201
    assert resp.body["display_name"] == "New"
    assert resp.body["token"]
    assert resp.body["user_id"]

    me = anon.with_token(resp.body["token"]).get("/reservations")
    assert me.status == 200


def test_login_returns_usable_token(world):
    resp = world.ada.get("/reservations")
    assert resp.status == 200


def test_signup_duplicate_email_is_409(reset, anon):
    reset({"restaurants": [], "users": [], "reservations": []})
    body = {"email": "dup@example.com", "password": "correct horse", "display_name": "D"}
    assert anon.post("/auth/signup", json_body=body).status == 201
    resp = anon.post("/auth/signup", json_body=body)
    assert resp.status == 409
    assert resp.error_code == "email_taken"


@pytest.mark.parametrize("password", ["short", "1234567"])
def test_signup_short_password_is_422(reset, anon, password):
    reset({"restaurants": [], "users": [], "reservations": []})
    resp = anon.post("/auth/signup", json_body={
        "email": "shortpw@example.com", "password": password, "display_name": "D"})
    assert resp.status == 422
    assert resp.error_code == "validation_failed"


@pytest.mark.parametrize("email", ["no-at-sign", "", "a b@example.com"])
def test_signup_bad_email_is_422(reset, anon, email):
    reset({"restaurants": [], "users": [], "reservations": []})
    resp = anon.post("/auth/signup", json_body={
        "email": email, "password": "correct horse", "display_name": "D"})
    assert resp.status == 422
    assert resp.error_code == "validation_failed"


def test_login_wrong_password_is_401(world):
    resp = world.ada.request("POST", "/auth/login", json_body={
        "email": "ada@example.com", "password": "wrong password"})
    assert resp.status == 401
    assert resp.error_code == "unauthenticated"


def test_login_unknown_email_is_401(world):
    resp = world.ada.request("POST", "/auth/login", json_body={
        "email": "nobody@example.com", "password": "correct horse"})
    assert resp.status == 401
    assert resp.error_code == "unauthenticated"


def test_account_supports_concurrent_sessions(reset, anon):
    reset({"restaurants": [], "users": [
        {"id": "u_c", "email": "c@example.com", "password": "correct horse", "display_name": "C"}],
        "reservations": []})
    t1 = anon.post("/auth/login", json_body={"email": "c@example.com", "password": "correct horse"}).body["token"]
    t2 = anon.post("/auth/login", json_body={"email": "c@example.com", "password": "correct horse"}).body["token"]
    assert t1 != t2
    assert anon.with_token(t1).get("/reservations").status == 200
    assert anon.with_token(t2).get("/reservations").status == 200


def test_passwords_are_not_stored_in_plaintext(world, anon):
    export = anon.get("/_test/export").body
    dump = str(export)
    assert "correct horse" not in dump
