"""Endpoint implementations. Each returns (status, json_body)."""
from __future__ import annotations

import datetime as dt

from . import auth, errors, idempotency, timeutil, validation
from .store import Store, User

MAX_MOVES = 8
MIN_SERIES_COUNT, MAX_SERIES_COUNT = 2, 12
MIN_INTERVAL_WEEKS, MAX_INTERVAL_WEEKS = 1, 4


# -- authentication -----------------------------------------------------

def authenticate(store: Store, headers) -> User:
    value = headers.get("Authorization")
    if not value:
        raise errors.unauthenticated("missing bearer token")
    parts = value.split(None, 1)
    if len(parts) != 2 or parts[0] != "Bearer" or not parts[1]:
        raise errors.unauthenticated("malformed Authorization header")
    user = store.user_for_token(parts[1])
    if user is None:
        raise errors.unauthenticated("unknown bearer token")
    return user


def authenticate_optional(store: Store, headers) -> User | None:
    """For endpoints that fold the "no token" case into their owner-only 404 (stage-3 history/decision/series)."""
    try:
        return authenticate(store, headers)
    except errors.ApiError:
        return None


def signup(store: Store, body: dict) -> tuple[int, dict]:
    email = validation.string_field(body, "email")
    password = validation.string_field(body, "password")
    display_name = validation.string_field(body, "display_name")
    if not auth.is_valid_email(email):
        raise errors.validation_failed("email must be of the form local@domain")
    if len(password) < 8:
        raise errors.validation_failed("password must be at least 8 characters")
    user, token = store.signup(email, password, display_name)
    return 201, {"user_id": user.id, "display_name": user.display_name, "token": token}


def login(store: Store, body: dict) -> tuple[int, dict]:
    email = validation.string_field(body, "email")
    password = validation.string_field(body, "password")
    user, token = store.login(email, password)
    return 200, {"user_id": user.id, "display_name": user.display_name, "token": token}


# -- restaurants / policies / availability ------------------------------

def list_restaurants(store: Store) -> tuple[int, dict]:
    return 200, {"restaurants": [r.to_public_json() for r in store.list_restaurants()]}


def get_restaurant(store: Store, restaurant_id: str) -> tuple[int, dict]:
    restaurant = store.get_restaurant(restaurant_id)
    if restaurant is None:
        raise errors.not_found("unknown restaurant")
    return 200, restaurant.to_detail_json()


def list_policies(store: Store, restaurant_id: str) -> tuple[int, dict]:
    return 200, {"policies": store.list_policies(restaurant_id)}


def publish_policy(store: Store, user: User, headers, restaurant_id: str, body: dict) -> tuple[int, dict]:
    key = validation.idempotency_key(headers)
    path = f"/restaurants/{restaurant_id}/policies"

    def compute() -> tuple[int, dict]:
        policy = store.publish_policy(user, restaurant_id, body)
        return 201, policy.to_json()

    return idempotency.run(store, user, key, "POST", path, body, compute)


def availability(store: Store, params: dict) -> tuple[int, dict]:
    restaurant_id = validation.query_str(params, "restaurant_id")
    date_str = validation.query_str(params, "date")
    party_size = validation.query_int(params, "party_size")
    if party_size < 1:
        raise errors.validation_failed("party_size must be at least 1")
    explain = validation.explain_query(params)
    date = timeutil.parse_date(date_str)
    if date is None:
        raise errors.validation_failed("date must be YYYY-MM-DD")
    restaurant = store.get_restaurant(restaurant_id)
    if restaurant is None:
        raise errors.not_found("unknown restaurant")
    slots = store.availability(restaurant, date, party_size, explain=explain)
    return 200, {
        "restaurant_id": restaurant.id,
        "date": date.isoformat(),
        "timezone": restaurant.timezone,
        "slots": slots,
    }


# -- reservations ---------------------------------------------------------

def _reservation_fields(body: dict, *, require_all: bool) -> dict:
    """Validate the ordinary create fields that are present in `body`."""
    out = {}
    if require_all or "restaurant_id" in body:
        out["restaurant_id"] = validation.string_field(body, "restaurant_id", required=require_all)
    if require_all or "table_id" in body or "table_ids" in body:
        out["table_ids"] = validation.table_ids_field(body, required=require_all)
    if require_all or "starts_at_local" in body:
        out["starts_at_local"] = validation.string_field(body, "starts_at_local", required=require_all)
    if require_all or "party_size" in body:
        out["party_size"] = validation.party_size_field(body, required=require_all)
    return {k: v for k, v in out.items() if v is not None}


def _raw_amendment_fields(body: dict) -> dict:
    """Extract the optional amendment fields present in `body`, unvalidated.

    Validation is deferred to the store, which must check a stale `expected_revision`
    before validating these fields (stage-3 §Policies: stale_revision "before
    cutoff/validation") -- so this layer must not reject a bad party_size etc. first.
    """
    raw = {}
    if "table_id" in body and body["table_id"] is not None:
        raw["table_id"] = body["table_id"]
    if "table_ids" in body and body["table_ids"] is not None:
        raw["table_ids"] = body["table_ids"]
    if "starts_at_local" in body and body["starts_at_local"] is not None:
        raw["starts_at_local"] = body["starts_at_local"]
    if "party_size" in body and body["party_size"] is not None:
        raw["party_size"] = body["party_size"]
    return raw


def create_reservation(store: Store, user: User, headers, body: dict) -> tuple[int, dict]:
    key = validation.idempotency_key(headers)

    def compute() -> tuple[int, dict]:
        fields = _reservation_fields(body, require_all=True)
        reservation, restaurant = store.create_reservation(
            user, fields["restaurant_id"], fields["table_ids"],
            fields["starts_at_local"], fields["party_size"])
        return 201, reservation.to_json(restaurant.zone())

    return idempotency.run(store, user, key, "POST", "/reservations", body, compute)


def list_reservations(store: Store, user: User) -> tuple[int, dict]:
    out = []
    for r in store.user_reservations(user):
        restaurant = store.get_restaurant(r.restaurant_id)
        tz = restaurant.zone() if restaurant else dt.timezone.utc
        out.append(r.to_json(tz))
    return 200, {"reservations": out}


def get_reservation(store: Store, user: User, reference: str) -> tuple[int, dict]:
    reservation = store.get_owned_reservation(user, reference)
    restaurant = store.get_restaurant(reservation.restaurant_id)
    return 200, reservation.to_json(restaurant.zone())


def get_history(store: Store, viewer: User | None, reference: str) -> tuple[int, dict]:
    reservation = store.get_history(viewer, reference)
    return 200, {"reference": reservation.reference, "entries": list(reservation.history)}


def get_decision(store: Store, viewer: User | None, reference: str) -> tuple[int, dict]:
    reservation = store.get_decision(viewer, reference)
    return 200, {
        "reference": reservation.reference,
        "revision": reservation.revision,
        "accepted_terms": dict(reservation.accepted_terms),
    }


def cancel_reservation(store: Store, user: User, reference: str) -> tuple[int, dict]:
    reservation, restaurant = store.cancel_reservation(user, reference)
    return 200, reservation.to_json(restaurant.zone())


def patch_reservation(store: Store, user: User, reference: str, body: dict) -> tuple[int, dict]:
    expected_revision = validation.expected_revision_field(body)
    raw_changes = _raw_amendment_fields(body)
    reservation, restaurant = store.amend_reservation(user, reference, raw_changes, expected_revision)
    return 200, reservation.to_json(restaurant.zone())


# -- reservation moves ----------------------------------------------------

def _validate_moves_shape(body: dict) -> list[dict]:
    """Validate the `moves` array's own shape; leave each item's resulting fields raw.

    Field validation (table_id/table_ids/starts_at_local/party_size) is deferred to the
    store, which must check each item's `expected_revision` against its current revision
    before validating those fields (same precedence rule as a single PATCH).
    """
    moves = body.get("moves")
    if not isinstance(moves, list) or not (1 <= len(moves) <= MAX_MOVES):
        raise errors.validation_failed("moves must hold 1..8 items")
    seen = set()
    cleaned = []
    for item in moves:
        if not isinstance(item, dict):
            raise errors.validation_failed("each move must be an object")
        reference = item.get("reference")
        if not isinstance(reference, str) or not reference:
            raise errors.validation_failed("each move needs a string reference")
        if reference in seen:
            raise errors.validation_failed("move references must be distinct")
        seen.add(reference)
        move = {"reference": reference, "raw": _raw_amendment_fields(item)}
        expected_revision = validation.expected_revision_field(item)
        if expected_revision is not None:
            move["expected_revision"] = expected_revision
        cleaned.append(move)
    return cleaned


def reservation_moves(store: Store, user: User, headers, body: dict) -> tuple[int, dict]:
    key = validation.idempotency_key(headers)

    def compute() -> tuple[int, dict]:
        moves = _validate_moves_shape(body)
        reservations, restaurant = store.reservation_moves(user, moves)
        tz = restaurant.zone()
        return 201, {"reservations": [r.to_json(tz) for r in reservations]}

    return idempotency.run(store, user, key, "POST", "/reservation-moves", body, compute)


# -- recurring series ----------------------------------------------------

def create_series(store: Store, user: User, headers, body: dict) -> tuple[int, dict]:
    key = validation.idempotency_key(headers)

    def compute() -> tuple[int, dict]:
        anchor_reference = validation.string_field(body, "anchor_reference")
        count = validation.bounded_int_field(body, "count", MIN_SERIES_COUNT, MAX_SERIES_COUNT)
        interval_weeks = validation.bounded_int_field(
            body, "interval_weeks", MIN_INTERVAL_WEEKS, MAX_INTERVAL_WEEKS)
        series = store.create_series(user, anchor_reference, count, interval_weeks)
        restaurant = store.get_restaurant(series.restaurant_id)
        return 201, store.series_json(series, restaurant.zone())

    return idempotency.run(store, user, key, "POST", "/series", body, compute)


def get_series(store: Store, viewer: User | None, series_id: str) -> tuple[int, dict]:
    series = store.get_series(viewer, series_id)
    restaurant = store.get_restaurant(series.restaurant_id)
    return 200, store.series_json(series, restaurant.zone())


def amend_series(store: Store, user: User, headers, series_id: str, body: dict) -> tuple[int, dict]:
    key = validation.idempotency_key(headers)
    path = f"/series/{series_id}/amend"

    def compute() -> tuple[int, dict]:
        expected_revision = validation.required_positive_int_field(body, "expected_revision")
        raw_from_index = body.get("from_index")
        raw_local_time = body.get("local_time")
        series = store.amend_series(user, series_id, expected_revision, raw_from_index, raw_local_time)
        restaurant = store.get_restaurant(series.restaurant_id)
        return 201, store.series_json(series, restaurant.zone())

    return idempotency.run(store, user, key, "POST", path, body, compute)


# -- seating replans --------------------------------------------------------

def create_replan(store: Store, user: User, headers, restaurant_id: str, body: dict) -> tuple[int, dict]:
    key = validation.idempotency_key(headers)
    path = f"/restaurants/{restaurant_id}/replans"

    def compute() -> tuple[int, dict]:
        plan, moved_count, unused_seats = store.create_replan(user, restaurant_id, body)
        restaurant = store.get_restaurant(restaurant_id)
        return 201, {
            "plan_id": plan.id,
            "restaurant_revision": restaurant.revision,
            "closure": {"table_id": plan.table_id, "from": plan.from_str, "to": plan.to_str},
            "assignments": [dict(a) for a in plan.assignments],
            "moved_count": moved_count,
            "unused_seats": unused_seats,
        }

    return idempotency.run(store, user, key, "POST", path, body, compute)


def apply_replan(store: Store, user: User, headers, restaurant_id: str, plan_id: str,
                  body: dict) -> tuple[int, dict]:
    key = validation.idempotency_key(headers)
    path = f"/restaurants/{restaurant_id}/replans/{plan_id}/apply"

    def compute() -> tuple[int, dict]:
        plan, restaurant, reservations = store.apply_replan(user, restaurant_id, plan_id)
        tz = restaurant.zone()
        return 201, {
            "plan_id": plan.id,
            "restaurant_revision": restaurant.revision,
            "reservations": [r.to_json(tz) for r in reservations],
        }

    return idempotency.run(store, user, key, "POST", path, body, compute)


# -- test control -----------------------------------------------------------

def test_reset(store: Store, body: dict) -> tuple[int, dict | None]:
    store.reset(body if isinstance(body, dict) else {})
    return 204, None


def test_export(store: Store) -> tuple[int, dict]:
    return 200, {"track": "tablekeeper", "format_version": 1, "state": store.export_state()}


def test_import(store: Store, body: dict) -> tuple[int, dict | None]:
    if body.get("track") != "tablekeeper" or body.get("format_version") != 1:
        raise errors.validation_failed("unexpected track or format_version")
    state = body.get("state")
    if not isinstance(state, dict):
        raise errors.validation_failed("state is required")
    store.import_state(state)
    return 204, None
