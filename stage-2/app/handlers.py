"""Endpoint implementations. Each returns (status, json_body)."""
from __future__ import annotations

import datetime as dt

from . import auth, errors, idempotency, timeutil, validation
from .store import Store, User

MAX_MOVES = 8


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


# -- restaurants / availability ------------------------------------------

def list_restaurants(store: Store) -> tuple[int, dict]:
    return 200, {"restaurants": [r.to_public_json() for r in store.list_restaurants()]}


def get_restaurant(store: Store, restaurant_id: str) -> tuple[int, dict]:
    restaurant = store.get_restaurant(restaurant_id)
    if restaurant is None:
        raise errors.not_found("unknown restaurant")
    return 200, restaurant.to_detail_json()


def availability(store: Store, params: dict) -> tuple[int, dict]:
    restaurant_id = validation.query_str(params, "restaurant_id")
    date_str = validation.query_str(params, "date")
    party_size = validation.query_int(params, "party_size")
    if party_size < 1:
        raise errors.validation_failed("party_size must be at least 1")
    date = timeutil.parse_date(date_str)
    if date is None:
        raise errors.validation_failed("date must be YYYY-MM-DD")
    restaurant = store.get_restaurant(restaurant_id)
    if restaurant is None:
        raise errors.not_found("unknown restaurant")
    slots = store.availability(restaurant, date, party_size)
    return 200, {
        "restaurant_id": restaurant.id,
        "date": date.isoformat(),
        "timezone": restaurant.timezone,
        "slots": slots,
    }


# -- reservations ---------------------------------------------------------

def _reservation_fields(body: dict, *, require_all: bool) -> dict:
    """Validate the ordinary create/amend fields that are present in `body`."""
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


def cancel_reservation(store: Store, user: User, reference: str) -> tuple[int, dict]:
    reservation, restaurant = store.cancel_reservation(user, reference)
    return 200, reservation.to_json(restaurant.zone())


def patch_reservation(store: Store, user: User, reference: str, body: dict) -> tuple[int, dict]:
    changes = _reservation_fields(body, require_all=False)
    reservation, restaurant = store.amend_reservation(user, reference, changes)
    return 200, reservation.to_json(restaurant.zone())


# -- reservation moves ----------------------------------------------------

def _validate_moves_shape(body: dict) -> list[dict]:
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
        move = {"reference": reference}
        table_ids = validation.table_ids_field(item, required=False)
        if table_ids is not None:
            move["table_ids"] = table_ids
        if "starts_at_local" in item and item["starts_at_local"] is not None:
            move["starts_at_local"] = validation.string_field(item, "starts_at_local")
        if "party_size" in item and item["party_size"] is not None:
            move["party_size"] = validation.party_size_field(item)
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
