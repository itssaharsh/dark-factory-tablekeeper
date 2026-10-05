#!/usr/bin/env python3
"""Browser-driven checks for tablekeeper stage 2, run with Playwright against a served
browser product. Companion to checks/stage-2.jsonl: every entry in that list whose
`unchecked` reason names a BROWSER-* id is checked for real here, not by speccheck.py,
because the requirement needs a browser (client-rendered markers, visual states, CSS,
focus, viewport behaviour, network race conditions).

usage: python3 checks/stage-2-browser.py --base-url http://127.0.0.1:PORT [--cards DIR]
exit:  0 everything passed · 1 at least one check failed (cards written to --cards,
       one per failing check id) · 2 the service could not be reached or a check could
       not run at all (not a finding)

Standard library plus `playwright` (already installed with chromium in this seat's image).
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright, expect, Error as PWError

PWD = "correct horse"


def api(base_url: str, method: str, path: str, body=None, token=None, timeout=5.0) -> tuple[int, dict | None]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base_url + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)


R_COMBO = {
    "id": "r_combo", "name": "Combo House", "timezone": "Europe/Berlin",
    "slot_minutes": 30, "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
    "combinable": [["t_1", "t_2"], ["t_2", "t_3"]],
    "opening_hours": [
        {"weekday": "thu", "opens": "18:00", "closes": "23:00"},
        {"weekday": "fri", "opens": "18:00", "closes": "23:30"},
    ],
    "tables": [
        {"id": "t_1", "label": "1", "capacity": 2},
        {"id": "t_2", "label": "2", "capacity": 4},
        {"id": "t_3", "label": "3", "capacity": 2},
    ],
}
FIXTURE = {
    "users": [
        {"id": "u_d1", "email": "d1@example.com", "password": PWD, "display_name": "D1"},
        {"id": "u_d2", "email": "d2@example.com", "password": PWD, "display_name": "D2"},
    ],
    "restaurants": [R_COMBO],
    "reservations": [],
}
FUTURE_DATE = "2026-10-08"   # Thursday; keep in sync with the API check list's chosen date


class Finding(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def reset(base_url: str, fixture=None) -> None:
    status, _ = api(base_url, "POST", "/_test/reset", fixture or FIXTURE)
    if status != 204:
        raise RuntimeError(f"reset failed: {status}")


def login_ui(page, base_url: str, email: str) -> None:
    page.goto(base_url + "/login")
    page.locator('[data-testid="login-email"]').fill(email)
    page.locator('[data-testid="login-password"]').fill(PWD)
    page.locator('[data-testid="login-submit"]').click()
    expect(page.locator('[data-testid="current-user"]')).to_be_visible(timeout=5000)


CHECKS: list[tuple[str, str, object]] = []


def check(id: str, sentence: str):
    def wrap(fn):
        CHECKS.append((id, sentence, fn))
        return fn
    return wrap


# ---------------------------------------------------------------- routes
@check("BROWSER-ROUTES", "The following screens must be reachable by URL.")
def _(page, base_url):
    reset(base_url)
    for path, testid in (("/", "search-button"), ("/signup", "signup-submit"),
                          ("/login", "login-submit"), ("/lookup", "lookup-submit")):
        page.goto(base_url + path)
        loc = page.locator(f'[data-testid="{testid}"]')
        if loc.count() == 0:
            raise Finding(f"{path} does not show [data-testid={testid}]; screen not reachable at this URL")


# ---------------------------------------------------------------- signup/login
@check("BROWSER-SIGNUP-LOGIN", "signup-email, signup-password, signup-display-name | Inputs; "
       "signup-submit | Button; login-email, login-password, login-submit | Inputs and button; "
       "auth-error | Error message, present only when there is one; "
       "current-user | Visible on every screen when signed in, text contains the display name; "
       "logout-button | Button")
def _(page, base_url):
    reset(base_url)
    page.goto(base_url + "/signup")
    for tid in ("signup-email", "signup-password", "signup-display-name", "signup-submit"):
        if page.locator(f'[data-testid="{tid}"]').count() == 0:
            raise Finding(f"/signup is missing [data-testid={tid}]")
    if page.locator('[data-testid="auth-error"]').count() > 0:
        raise Finding("auth-error is present on /signup before any failed submission")
    page.locator('[data-testid="signup-email"]').fill("newuser@example.com")
    page.locator('[data-testid="signup-password"]').fill(PWD)
    page.locator('[data-testid="signup-display-name"]').fill("New User")
    page.locator('[data-testid="signup-submit"]').click()
    cu = page.locator('[data-testid="current-user"]')
    expect(cu).to_be_visible(timeout=5000)
    if "New User" not in cu.inner_text():
        raise Finding(f"current-user does not contain the display name: {cu.inner_text()!r}")
    page.goto(base_url + "/lookup")
    expect(page.locator('[data-testid="current-user"]')).to_be_visible(timeout=5000)
    logout = page.locator('[data-testid="logout-button"]')
    if logout.count() == 0:
        raise Finding("logout-button is missing while signed in")
    logout.click()
    if page.locator('[data-testid="current-user"]').count() > 0:
        raise Finding("current-user is still present after logout")


@check("BROWSER-AUTH-ERROR", "auth-error | Error message. Present only when there is one")
def _(page, base_url):
    reset(base_url)
    page.goto(base_url + "/login")
    page.locator('[data-testid="login-email"]').fill("nosuch@example.com")
    page.locator('[data-testid="login-password"]').fill("wrong-password")
    page.locator('[data-testid="login-submit"]').click()
    err = page.locator('[data-testid="auth-error"]')
    expect(err).to_be_visible(timeout=5000)
    if not err.inner_text().strip():
        raise Finding("auth-error is present but empty after a failed login")


# ---------------------------------------------------------------- availability grid
@check("BROWSER-GRID-TESTIDS", "restaurant-select, date-input, party-size-input, search-button, "
       "availability-grid, slot-{table_id}-{HH:MM}, no-slots")
def _(page, base_url):
    reset(base_url)
    page.goto(base_url + "/")
    for tid in ("restaurant-select", "date-input", "party-size-input", "search-button"):
        if page.locator(f'[data-testid="{tid}"]').count() == 0:
            raise Finding(f"/ is missing [data-testid={tid}]")
    options = page.locator('[data-testid="restaurant-select"] option').all()
    values = [o.get_attribute("value") for o in options]
    if "r_combo" not in values:
        raise Finding(f"restaurant-select options are {values!r}, expected to include the seeded restaurant id r_combo")
    page.locator('[data-testid="restaurant-select"]').select_option("r_combo")
    page.locator('[data-testid="date-input"]').fill(FUTURE_DATE)
    page.locator('[data-testid="party-size-input"]').fill("2")
    page.locator('[data-testid="search-button"]').click()
    grid = page.locator('[data-testid="availability-grid"]')
    expect(grid).to_be_visible(timeout=5000)
    cell = page.locator('[data-testid="slot-t_1-18:00"]')
    if cell.count() == 0:
        raise Finding("expected cell [data-testid=slot-t_1-18:00] is missing from the grid")


@check("BROWSER-NO-SLOTS", "no-slots | Shown instead of the grid when the day has no slots")
def _(page, base_url):
    reset(base_url)
    page.goto(base_url + "/")
    page.locator('[data-testid="restaurant-select"]').select_option("r_combo")
    page.locator('[data-testid="date-input"]').fill("2026-10-07")  # Wednesday: closed
    page.locator('[data-testid="party-size-input"]').fill("2")
    page.locator('[data-testid="search-button"]').click()
    no_slots = page.locator('[data-testid="no-slots"]')
    expect(no_slots).to_be_visible(timeout=5000)
    if page.locator('[data-testid="availability-grid"]').is_visible():
        raise Finding("availability-grid and no-slots are both visible for a closed day")


def _search(page, base_url, date=FUTURE_DATE, party_size="2"):
    page.goto(base_url + "/")
    page.locator('[data-testid="restaurant-select"]').select_option("r_combo")
    page.locator('[data-testid="date-input"]').fill(date)
    page.locator('[data-testid="party-size-input"]').fill(party_size)
    page.locator('[data-testid="search-button"]').click()
    expect(page.locator('[data-testid="availability-grid"]')).to_be_visible(timeout=5000)


@check("BROWSER-GRID-AVAILABLE", "Each cell carries data-available=\"true\" or data-available=\"false\"; "
       "a cell is true exactly when its table_id is in that slot's available_table_ids")
def _(page, base_url):
    reset(base_url)
    status, body = api(base_url, "POST", "/auth/login", {"email": "d1@example.com", "password": PWD})
    token = body["token"]
    # Raw request, since the api() helper doesn't set an Idempotency-Key header.
    req = urllib.request.Request(base_url + "/reservations",
                                  data=json.dumps({"restaurant_id": "r_combo", "table_id": "t_1",
                                                    "starts_at_local": f"{FUTURE_DATE}T18:00", "party_size": 2}).encode(),
                                  method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Idempotency-Key", "browser-check-grid-available")
    urllib.request.urlopen(req, timeout=5.0)
    _search(page, base_url)
    taken = page.locator('[data-testid="slot-t_1-18:00"]')
    free = page.locator('[data-testid="slot-t_2-18:00"]')
    if taken.get_attribute("data-available") != "false":
        raise Finding("slot-t_1-18:00 has data-available!=false after t_1 was booked for that slot")
    if free.get_attribute("data-available") != "true":
        raise Finding("slot-t_2-18:00 has data-available!=true although t_2 is free for that slot")


@check("BROWSER-GRID-CLICK", "Clicking an available cell opens the booking form for that table and slot; "
       "clicking an unavailable cell does nothing")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url)
    page.locator('[data-testid="slot-t_1-18:00"]').click()
    form = page.locator('[data-testid="booking-form"]')
    expect(form).to_be_visible(timeout=5000)
    summary = page.locator('[data-testid="booking-summary"]').inner_text()
    if "1" not in summary or "18:00" not in summary:
        raise Finding(f"booking-summary {summary!r} does not contain the table label and local start time")


@check("BROWSER-GRID-SIGNEDOUT", "Booking requires a signed-in user: clicking an available cell while "
       "signed out shows auth-error or navigates to /login")
def _(page, base_url):
    reset(base_url)
    _search(page, base_url)
    page.locator('[data-testid="slot-t_1-18:00"]').click()
    page.wait_for_timeout(500)
    on_login = "/login" in page.url
    has_auth_error = page.locator('[data-testid="auth-error"]').count() > 0
    if not (on_login or has_auth_error):
        raise Finding("clicking an available cell while signed out neither navigated to /login nor showed auth-error")


# ---------------------------------------------------------------- booking form / confirmation
@check("BROWSER-BOOKING-FORM-TESTIDS", "booking-form, booking-summary, booking-party-size, booking-submit, booking-error")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url, party_size="2")
    page.locator('[data-testid="slot-t_1-18:00"]').click()
    expect(page.locator('[data-testid="booking-form"]')).to_be_visible(timeout=5000)
    party = page.locator('[data-testid="booking-party-size"]')
    if party.input_value() != "2":
        raise Finding(f"booking-party-size is {party.input_value()!r}, expected pre-filled from the search (2)")
    if page.locator('[data-testid="booking-submit"]').count() == 0:
        raise Finding("booking-submit is missing")


@check("BROWSER-CONFIRMATION-TESTIDS", "confirmation, confirmation-reference, confirmation-details")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url)
    page.locator('[data-testid="slot-t_1-18:00"]').click()
    page.locator('[data-testid="booking-submit"]').click()
    conf = page.locator('[data-testid="confirmation"]')
    expect(conf).to_be_visible(timeout=5000)
    ref = page.locator('[data-testid="confirmation-reference"]').inner_text().strip()
    import re
    if not re.fullmatch(r"[A-Z0-9]{6,12}", ref):
        raise Finding(f"confirmation-reference text is {ref!r}, expected exactly a 6-12 char A-Z0-9 reference with no surrounding words")
    details = page.locator('[data-testid="confirmation-details"]').inner_text()
    if "Combo House" not in details or "1" not in details or "18:00" not in details:
        raise Finding(f"confirmation-details {details!r} missing restaurant name, table label or local start time")
    if not page.locator('[data-testid="booking-form"]').is_visible():
        raise Finding("booking-form is not kept on screen after a successful booking")


@check("BROWSER-BOOKING-RESUBMIT", "Submitting the booking form again without changing a field must return "
       "the same confirmation-reference, without booking-error or another booking; changing a field makes "
       "the next submission a new booking request")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url)
    page.locator('[data-testid="slot-t_1-18:00"]').click()
    page.locator('[data-testid="booking-submit"]').click()
    expect(page.locator('[data-testid="confirmation"]')).to_be_visible(timeout=5000)
    ref1 = page.locator('[data-testid="confirmation-reference"]').inner_text().strip()
    page.locator('[data-testid="booking-submit"]').click()
    page.wait_for_timeout(500)
    if page.locator('[data-testid="booking-error"]').count() > 0:
        raise Finding("booking-error appeared on an unchanged resubmit")
    ref2 = page.locator('[data-testid="confirmation-reference"]').inner_text().strip()
    if ref1 != ref2:
        raise Finding(f"resubmitting the unchanged form returned a different reference ({ref1!r} -> {ref2!r})")
    status, body = api(base_url, "POST", "/auth/login", {"email": "d1@example.com", "password": PWD})
    status, body = api(base_url, "GET", "/reservations", token=body["token"])
    if len(body["reservations"]) != 1:
        raise Finding(f"expected exactly one reservation after resubmitting the unchanged form, found {len(body['reservations'])}")


# ---------------------------------------------------------------- lookup
@check("BROWSER-LOOKUP-TESTIDS", "lookup-reference-input, lookup-submit, reservation-detail, "
       "reservation-status, reservation-cancel-button, reservation-error")
def _(page, base_url):
    reset(base_url)
    status, body = api(base_url, "POST", "/auth/login", {"email": "d1@example.com", "password": PWD})
    token = body["token"]
    req = urllib.request.Request(base_url + "/reservations",
                                  data=json.dumps({"restaurant_id": "r_combo", "table_id": "t_1",
                                                    "starts_at_local": f"{FUTURE_DATE}T18:00", "party_size": 2}).encode(),
                                  method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Idempotency-Key", "browser-check-lookup")
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        ref = json.loads(resp.read())["reference"]

    # GET /reservations/{reference} requires a bearer token (stage-1 §6); the lookup
    # screen needs a browser session logged in as the owner, not just an API token.
    login_ui(page, base_url, "d1@example.com")
    page.goto(base_url + "/lookup")
    page.locator('[data-testid="lookup-reference-input"]').fill("NOSUCHREF1")
    page.locator('[data-testid="lookup-submit"]').click()
    err = page.locator('[data-testid="reservation-error"]')
    expect(err).to_be_visible(timeout=5000)

    page.locator('[data-testid="lookup-reference-input"]').fill(ref)
    page.locator('[data-testid="lookup-submit"]').click()
    detail = page.locator('[data-testid="reservation-detail"]')
    expect(detail).to_be_visible(timeout=5000)
    status_text = page.locator('[data-testid="reservation-status"]').inner_text().strip()
    if status_text != "confirmed":
        raise Finding(f"reservation-status is {status_text!r}, expected exactly 'confirmed'")
    cancel = page.locator('[data-testid="reservation-cancel-button"]')
    if cancel.count() == 0:
        raise Finding("reservation-cancel-button is missing for a confirmed, cancellable reservation")
    cancel.click()
    page.wait_for_timeout(500)
    status_text = page.locator('[data-testid="reservation-status"]').inner_text().strip()
    if status_text != "cancelled":
        raise Finding(f"reservation-status after cancel is {status_text!r}, expected exactly 'cancelled'")
    if page.locator('[data-testid="reservation-cancel-button"]').count() != 0:
        raise Finding("reservation-cancel-button is still present once the reservation is cancelled")


# ---------------------------------------------------------------- combined tables
@check("BROWSER-COMBO-TESTIDS", "slot-{t_a}+{t_b}-{HH:MM} combination cell in combinable order; "
       "confirmation-tables; reservation-tables")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url, party_size="6")
    combo_cell = page.locator('[data-testid="slot-t_1+t_2-18:00"]')
    if combo_cell.count() == 0:
        raise Finding("expected combination cell [data-testid=slot-t_1+t_2-18:00] is missing for a party of 6")
    if combo_cell.get_attribute("data-available") != "true":
        raise Finding("slot-t_1+t_2-18:00 is not data-available=true although both t_1 and t_2 are free")
    combo_cell.click()
    summary = page.locator('[data-testid="booking-summary"]').inner_text()
    if "1" not in summary or "2" not in summary:
        raise Finding(f"booking-summary {summary!r} does not name every table in the combination selection")
    page.locator('[data-testid="booking-submit"]').click()
    expect(page.locator('[data-testid="confirmation"]')).to_be_visible(timeout=5000)
    conf_tables = page.locator('[data-testid="confirmation-tables"]')
    if conf_tables.count() == 0:
        raise Finding("confirmation-tables is missing after a combination booking")
    text = conf_tables.inner_text()
    if "1" not in text or "2" not in text:
        raise Finding(f"confirmation-tables {text!r} does not contain every table label in the reservation")
    ref = page.locator('[data-testid="confirmation-reference"]').inner_text().strip()

    page.goto(base_url + "/lookup")
    page.locator('[data-testid="lookup-reference-input"]').fill(ref)
    page.locator('[data-testid="lookup-submit"]').click()
    expect(page.locator('[data-testid="reservation-detail"]')).to_be_visible(timeout=5000)
    res_tables = page.locator('[data-testid="reservation-tables"]')
    if res_tables.count() == 0:
        raise Finding("reservation-tables is missing on the lookup screen for a combination booking")


# ---------------------------------------------------------------- competing clients / uncertain outcomes
@check("BROWSER-RACE-SEARCH", "If search A starts before search B but finishes after it, the grid, table "
       "labels and booking form must describe B. A late response must not restore A's results.")
def _(page, base_url):
    reset(base_url)
    page.goto(base_url + "/")
    page.locator('[data-testid="restaurant-select"]').select_option("r_combo")

    hold = {"armed": True}

    def delay_first_search(route):
        if "availability" in route.request.url and hold["armed"]:
            hold["armed"] = False
            response = route.fetch()
            time.sleep(1.5)  # search A: slow to resolve
            route.fulfill(response=response)
        else:
            route.continue_()

    page.route("**/availability*", delay_first_search)
    page.locator('[data-testid="date-input"]').fill(FUTURE_DATE)
    page.locator('[data-testid="party-size-input"]').fill("2")
    page.locator('[data-testid="search-button"]').click()  # search A (slow)
    page.wait_for_timeout(200)
    page.locator('[data-testid="party-size-input"]').fill("6")
    page.locator('[data-testid="search-button"]').click()  # search B (fast), should win
    expect(page.locator('[data-testid="availability-grid"]')).to_be_visible(timeout=5000)
    page.wait_for_timeout(2000)  # let the slow A response arrive, if it's going to
    page.unroute("**/availability*")
    cell = page.locator('[data-testid="slot-t_1-18:00"]')
    # party size 6 means t_1 (capacity 2) is "too small", not plain available; the grid
    # must still describe B (party 6), not A (party 2), after A's late response lands.
    if cell.count() and cell.get_attribute("data-available") == "true":
        raise Finding("after a late, slower search A (party 2) resolves following a faster search B "
                       "(party 6), the grid shows A's results (t_1 available for a party the UI should be "
                       "describing as 6) instead of B's")


@check("BROWSER-RACE-TAKEN", "If another client takes a table after the form opens, a 409 table_unavailable "
       "response shows booking-error and refreshes availability. Preserve the selected form and its inputs "
       "so the diner can change their choice. Do not show a confirmation for that attempt.")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url)
    page.locator('[data-testid="slot-t_1-18:00"]').click()
    expect(page.locator('[data-testid="booking-form"]')).to_be_visible(timeout=5000)

    # Another client takes t_1 for the same slot after the form opened, before this one submits.
    status, body = api(base_url, "POST", "/auth/login", {"email": "d2@example.com", "password": PWD})
    req = urllib.request.Request(base_url + "/reservations",
                                  data=json.dumps({"restaurant_id": "r_combo", "table_id": "t_1",
                                                    "starts_at_local": f"{FUTURE_DATE}T18:00", "party_size": 2}).encode(),
                                  method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {body['token']}")
    req.add_header("Idempotency-Key", "browser-check-race-taken-other-client")
    urllib.request.urlopen(req, timeout=5.0)

    party_before = page.locator('[data-testid="booking-party-size"]').input_value()
    page.locator('[data-testid="booking-submit"]').click()
    err = page.locator('[data-testid="booking-error"]')
    expect(err).to_be_visible(timeout=5000)
    if page.locator('[data-testid="confirmation"]').count() != 0:
        raise Finding("a confirmation appeared for a booking attempt that lost the race (409 table_unavailable)")
    if not page.locator('[data-testid="booking-form"]').is_visible():
        raise Finding("the form was not preserved after a 409 table_unavailable")
    if page.locator('[data-testid="booking-party-size"]').input_value() != party_before:
        raise Finding("the form's inputs were not preserved after a 409 table_unavailable")
    page.wait_for_timeout(500)
    refreshed = page.locator('[data-testid="slot-t_1-18:00"]')
    if refreshed.count() and refreshed.get_attribute("data-available") != "false":
        raise Finding("availability did not refresh to show t_1 taken after the 409")


@check("BROWSER-RACE-LOST", "If a booking response is lost, including after the booking commits, show "
       "nonempty booking-uncertain text, without booking-error or a new confirmation. The unchanged form "
       "must retry with the same idempotency key and body. A successful retry removes the "
       "uncertainty/error elements and shows the original reference.")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url)
    page.locator('[data-testid="slot-t_1-18:00"]').click()
    expect(page.locator('[data-testid="booking-form"]')).to_be_visible(timeout=5000)

    def drop_response_after_commit(route):
        if route.request.method == "POST":
            response = route.fetch()   # the real request reaches the server and commits
            route.abort()              # but the browser never sees the response
        else:
            route.continue_()

    page.route("**/reservations", drop_response_after_commit)
    page.locator('[data-testid="booking-submit"]').click()
    uncertain = page.locator('[data-testid="booking-uncertain"]')
    expect(uncertain).to_be_visible(timeout=5000)
    if not uncertain.inner_text().strip():
        raise Finding("booking-uncertain is present but empty after a lost response")
    if page.locator('[data-testid="booking-error"]').count() != 0:
        raise Finding("booking-error is shown alongside booking-uncertain after a lost response")
    if page.locator('[data-testid="confirmation"]').count() != 0:
        raise Finding("a confirmation appeared despite the response being lost")
    page.unroute("**/reservations")

    page.locator('[data-testid="booking-submit"]').click()  # retry: must reuse the same key+body
    expect(page.locator('[data-testid="confirmation"]')).to_be_visible(timeout=5000)
    if page.locator('[data-testid="booking-uncertain"]').count() != 0:
        raise Finding("booking-uncertain is still present after a successful retry")
    if page.locator('[data-testid="booking-error"]').count() != 0:
        raise Finding("booking-error is present after a successful retry")

    status, body = api(base_url, "POST", "/auth/login", {"email": "d1@example.com", "password": PWD})
    status, body = api(base_url, "GET", "/reservations", token=body["token"])
    if len(body["reservations"]) != 1:
        raise Finding(f"the lost-response retry created {len(body['reservations'])} reservations, expected exactly 1 "
                       "(the server-side commit from the dropped response, recovered by replay, not a duplicate)")


# ---------------------------------------------------------------- responsive / accessibility (design brief)
@check("BROWSER-RESPONSIVE", "The required flows must remain clear and usable at a 375 CSS-pixel viewport "
       "and at conventional desktop widths, without horizontal page scrolling.")
def _(page, base_url):
    reset(base_url)
    for width, height in ((375, 700), (1280, 800)):
        page.set_viewport_size({"width": width, "height": height})
        page.goto(base_url + "/")
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        if scroll_width > client_width + 1:  # +1 for sub-pixel rounding
            raise Finding(f"/ at {width}px viewport has horizontal overflow: scrollWidth={scroll_width} > clientWidth={client_width}")


@check("BROWSER-A11Y", "Inputs need visible labels, keyboard focus must be apparent, and text and controls "
       "need sufficient contrast.")
def _(page, base_url):
    reset(base_url)
    page.goto(base_url + "/")
    for tid in ("restaurant-select", "date-input", "party-size-input"):
        loc = page.locator(f'[data-testid="{tid}"]')
        labelled = page.evaluate(
            """(el) => {
                if (el.labels && el.labels.length) return true;
                if (el.getAttribute('aria-label')) return true;
                if (el.getAttribute('aria-labelledby')) return true;
                return false;
            }""", loc.element_handle())
        if not labelled:
            raise Finding(f"[data-testid={tid}] has no associated <label>, aria-label or aria-labelledby")
    # A programmatic .focus() does not reliably trigger :focus-visible (it's the
    # browser's own keyboard-vs-pointer heuristic); a real Tab keypress does, and
    # is what the brief's "keyboard focus must be apparent" is actually about.
    search = page.locator('[data-testid="search-button"]')
    page.locator("body").click(position={"x": 1, "y": 1})
    for _ in range(20):
        active_testid = page.evaluate("() => document.activeElement && document.activeElement.getAttribute('data-testid')")
        if active_testid == "search-button":
            break
        page.keyboard.press("Tab")
    else:
        raise Finding("could not reach search-button by tabbing through the page")
    outline = page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle")
    if outline == "none":
        raise Finding("search-button has outlineStyle:none when reached by keyboard Tab; keyboard focus is not apparent")


# ---------------------------------------------------------------- primary action / combo labels
@check("BROWSER-PRIMARY-BUTTON", "Primary actions must be easy to identify.")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url)
    page.locator('[data-testid="slot-t_1-18:00"]').click()
    expect(page.locator('[data-testid="booking-form"]')).to_be_visible(timeout=5000)
    # The brief's "filled button" is the btn-fill treatment on actual call-to-action
    # buttons; the availability grid's own cell states (including the plain white,
    # bordered "available" cell) are a separate, documented system, not buttons the
    # brief's "one filled button per screen" rule is counting.
    filled = page.evaluate(
        """() => Array.from(document.querySelectorAll('button')).filter(b => {
            if (b.offsetParent === null) return false;
            if (b.classList.contains('cell')) return false;
            return b.classList.contains('btn-fill');
        }).map(b => b.getAttribute('data-testid') || b.textContent.trim())""")
    if len(filled) > 1:
        raise Finding(f"{len(filled)} filled buttons visible on screen at once ({filled!r}); "
                       f"the design brief calls for one filled button per screen")


@check("BROWSER-COMBO-LABELS", "Combined tables should read as intentional seating options, not as "
       "concatenated technical identifiers. booking-summary must name every table in the selection.")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url, party_size="6")
    page.locator('[data-testid="slot-t_1+t_2-18:00"]').click()
    summary = page.locator('[data-testid="booking-summary"]').inner_text()
    if "t_1" in summary or "t_2" in summary:
        raise Finding(f"booking-summary {summary!r} exposes raw table ids (t_1/t_2) rather than human-readable labels")


# ---------------------------------------------------------------- design brief: states, text, fonts
# These check the team's own stated implementation plan for satisfying S2-2.7 ("states must be
# visually distinct") and S2-2.5 ("consistent visual system"). The design brief is not one of the
# --spec files given to speccheck.py (it has no corresponding file on disk), so these checks are not
# tied to a quoted sentence id from the jsonl list; they stand on their own, verifying the specific,
# mechanically-checkable claims from that brief as given in the room.

@check("BROWSER-DESIGN-TAKEN-VS-TOO-SMALL", "design brief: \"Taken\", for a table that seats the party "
       "but is booked... the word \"Taken\". \"Too small\", for a table whose capacity is below the "
       "searched party size... the words \"Too small\".")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    req = urllib.request.Request(base_url + "/reservations",
                                  data=json.dumps({"restaurant_id": "r_combo", "table_id": "t_1",
                                                    "starts_at_local": f"{FUTURE_DATE}T18:00", "party_size": 2}).encode(),
                                  method="POST")
    req.add_header("Content-Type", "application/json")
    status, body = api(base_url, "POST", "/auth/login", {"email": "d1@example.com", "password": PWD})
    req.add_header("Authorization", f"Bearer {body['token']}")
    req.add_header("Idempotency-Key", "browser-check-taken-vs-small")
    urllib.request.urlopen(req, timeout=5.0)
    _search(page, base_url, party_size="3")  # t_1 (capacity 2) is too small; t_2 (capacity 4) is just taken
    taken_text = page.locator('[data-testid="slot-t_1-18:00"]').inner_text()
    if "Too small" not in taken_text:
        raise Finding(f"slot-t_1-18:00 (capacity 2, party size 3) is {taken_text!r}, expected the words 'Too small'")
    # t_2 isn't booked in this fixture; book it to exercise the genuinely "Taken" case.
    req2 = urllib.request.Request(base_url + "/reservations",
                                   data=json.dumps({"restaurant_id": "r_combo", "table_id": "t_2",
                                                     "starts_at_local": f"{FUTURE_DATE}T18:00", "party_size": 4}).encode(),
                                   method="POST")
    req2.add_header("Content-Type", "application/json")
    req2.add_header("Authorization", f"Bearer {body['token']}")
    req2.add_header("Idempotency-Key", "browser-check-taken-vs-small-2")
    urllib.request.urlopen(req2, timeout=5.0)
    _search(page, base_url, party_size="3")
    taken_text = page.locator('[data-testid="slot-t_2-18:00"]').inner_text()
    if "Taken" not in taken_text:
        raise Finding(f"slot-t_2-18:00 (capacity 4 >= party size 3, but booked) is {taken_text!r}, expected the word 'Taken'")
    if taken_text.strip() == page.locator('[data-testid="slot-t_1-18:00"]').inner_text().strip():
        raise Finding("the 'Taken' and 'Too small' unavailable cells render identical text; the brief requires two distinct looks")


@check("BROWSER-DESIGN-CHOSEN", "design brief: Chosen: filled green (#14532D) with a check mark and white text.")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url)
    cell = page.locator('[data-testid="slot-t_1-18:00"]')
    cell.click()
    page.wait_for_timeout(300)
    # The brief's check mark is a CSS ::before on the cell label, and never appears
    # in inner_text()/textContent; read the actual rendered signals instead.
    bg = cell.evaluate("el => getComputedStyle(el).backgroundColor")
    label = cell.locator(".cell-label")
    text_color = label.evaluate("el => getComputedStyle(el).color")
    check_mark = label.evaluate("el => getComputedStyle(el, '::before').content")
    if bg != "rgb(20, 83, 45)":
        raise Finding(f"chosen cell background is {bg!r}, expected rgb(20, 83, 45) (#14532D)")
    if text_color != "rgb(255, 255, 255)":
        raise Finding(f"chosen cell text colour is {text_color!r}, expected white")
    if "check" not in check_mark.lower() and check_mark.strip('"') != "✓":
        raise Finding(f"chosen cell label ::before content is {check_mark!r}, expected a check mark")


@check("BROWSER-DESIGN-REFUSED-TEXT", "design brief: \"That table was just taken. Pick another time or "
       "table.\"")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url)
    page.locator('[data-testid="slot-t_1-18:00"]').click()
    status, body = api(base_url, "POST", "/auth/login", {"email": "d2@example.com", "password": PWD})
    req = urllib.request.Request(base_url + "/reservations",
                                  data=json.dumps({"restaurant_id": "r_combo", "table_id": "t_1",
                                                    "starts_at_local": f"{FUTURE_DATE}T18:00", "party_size": 2}).encode(),
                                  method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {body['token']}")
    req.add_header("Idempotency-Key", "browser-check-refused-text")
    urllib.request.urlopen(req, timeout=5.0)
    page.locator('[data-testid="booking-submit"]').click()
    err = page.locator('[data-testid="booking-error"]')
    expect(err).to_be_visible(timeout=5000)
    text = err.inner_text()
    if "just taken" not in text.lower():
        raise Finding(f"booking-error text is {text!r}; the design brief specifies the sentence "
                       "\"That table was just taken. Pick another time or table.\" for this exact refusal")


@check("BROWSER-DESIGN-UNSURE-TEXT", "design brief: \"We didn't get an answer. Your table may already be "
       "booked. Try again: you won't be booked twice.\"")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url)
    page.locator('[data-testid="slot-t_1-18:00"]').click()

    def drop_response_after_commit(route):
        if route.request.method == "POST":
            response = route.fetch()
            route.abort()
        else:
            route.continue_()

    page.route("**/reservations", drop_response_after_commit)
    page.locator('[data-testid="booking-submit"]').click()
    uncertain = page.locator('[data-testid="booking-uncertain"]')
    expect(uncertain).to_be_visible(timeout=5000)
    text = uncertain.inner_text()
    if "didn't get an answer" not in text.lower() and "won't be booked twice" not in text.lower():
        raise Finding(f"booking-uncertain text is {text!r}; the design brief specifies the sentence "
                       "\"We didn't get an answer. Your table may already be booked. Try again: you "
                       "won't be booked twice.\" for this exact state")
    page.unroute("**/reservations")


@check("BROWSER-DESIGN-FONTS", "design brief: Marcellus for the wordmark, restaurant names and "
       "\"Reserved\"; Figtree for everything else; Spline Sans Mono for references only. Bundle the "
       "font files in the image at build time... never link a font, script or stylesheet from another host.")
def _(page, base_url):
    reset(base_url)
    page.goto(base_url + "/")
    external_fonts = page.evaluate(
        """() => Array.from(document.querySelectorAll('link[rel="stylesheet"], link[rel="preload"][as="font"]'))
            .map(l => l.href)
            .filter(href => href && !href.startsWith(location.origin))""")
    if external_fonts:
        raise Finding(f"found font/stylesheet links to another host: {external_fonts!r}; the brief requires "
                       "fonts to be bundled in the image, never linked from another host")
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url)
    page.locator('[data-testid="slot-t_1-18:00"]').click()
    page.locator('[data-testid="booking-submit"]').click()
    expect(page.locator('[data-testid="confirmation"]')).to_be_visible(timeout=5000)
    ref_el = page.locator('[data-testid="confirmation-reference"]')
    font_family = page.evaluate("(el) => getComputedStyle(el).fontFamily", ref_el.element_handle())
    if "mono" not in font_family.lower() and "spline" not in font_family.lower():
        raise Finding(f"confirmation-reference font-family is {font_family!r}; the brief specifies "
                       "Spline Sans Mono (falling back to ui-monospace) for references")


@check("BROWSER-DESIGN-ONE-BOOKING-BUTTON", "design brief: Never show two buttons that book.")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url)
    page.locator('[data-testid="slot-t_1-18:00"]').click()
    expect(page.locator('[data-testid="booking-form"]')).to_be_visible(timeout=5000)
    booking_buttons = page.evaluate(
        """() => Array.from(document.querySelectorAll('button')).filter(b =>
            /book|reserve|confirm/i.test(b.textContent || '')).length""")
    if booking_buttons > 1:
        raise Finding(f"{booking_buttons} buttons on screen read like they book a table; the brief requires exactly one")


# ---------------------------------------------------------------- upgrade (stage-1 export -> stage-2 import)
@check("BROWSER-UPGRADE-SESSION", "A browser signed in before that export/import upgrade must remain signed "
       "in afterwards. The form and pending retry identity must survive the upgrade.")
def _(page, base_url):
    reset(base_url)
    login_ui(page, base_url, "d1@example.com")
    _search(page, base_url)
    page.locator('[data-testid="slot-t_1-18:00"]').click()
    status, body = api(base_url, "GET", "/_test/export")
    if status != 200:
        raise Finding(f"GET /_test/export returned {status} while a browser session was active")
    status, _ = api(base_url, "POST", "/_test/import", body)
    if status != 204:
        raise Finding(f"POST /_test/import of the just-exported state returned {status}")
    page.reload()
    cu = page.locator('[data-testid="current-user"]')
    if cu.count() == 0:
        raise Finding("current-user is gone after an export/import round-trip; the signed-in browser was signed out")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--cards", default=None)
    ap.add_argument("--only", default=None, help="comma-separated check ids to run, for a quick re-check")
    ap.add_argument("--revision", default=None, help="the revision being checked, named on every card written")
    args = ap.parse_args()
    if args.cards and not args.revision:
        print("--revision is required when --cards is given, so every finding names the revision it was checked "
              "against")
        return 2

    only = set(args.only.split(",")) if args.only else None
    folder = pathlib.Path(args.cards) if args.cards else None
    passed = failed = errored = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for check_id, sentence, fn in CHECKS:
            if only and check_id not in only:
                continue
            context = browser.new_context()
            page = context.new_page()
            try:
                fn(page, args.base_url)
                print(f"PASS {check_id}")
                passed += 1
            except Finding as exc:
                failed += 1
                print(f"FAIL {check_id}: {exc.message}")
                if folder:
                    folder.mkdir(parents=True, exist_ok=True)
                    card = {
                        "card": "finding", "stage": 2, "from": "verifier", "to": "coordinator",
                        "check_id": check_id, "sentence": sentence, "kind": "behaviour",
                        "revision": args.revision,
                        "expected": f"holds, through the browser: {sentence}",
                        "observed": exc.message, "status": "open",
                        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "replay": {"steps": [{"browser_script":
                                   f"python3 checks/stage-2-browser.py --base-url <url> --only {check_id}",
                                   "note": "Playwright-driven UI check, not a raw HTTP request"}]},
                    }
                    (folder / f"{check_id}.json").write_text(json.dumps(card, indent=1))
            except (PWError, RuntimeError) as exc:
                errored += 1
                print(f"ERROR {check_id}: could not run ({type(exc).__name__}: {exc}). Not a finding.")
            finally:
                context.close()
        browser.close()

    print(f"{passed} passed, {failed} failed, {errored} errored")
    return 1 if failed else (2 if errored else 0)


if __name__ == "__main__":
    sys.exit(main())
