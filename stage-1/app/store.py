"""In-memory data store: restaurants, tables, users, reservations.

Every write and every read that must see a consistent snapshot runs while the
caller holds `Store.lock` (a single process-wide `threading.RLock`). Handlers
acquire it once per request, which trivially gives us: no two confirmed
reservations ever overlap a table even under concurrent requests, and an
idempotency key resolves to exactly one outcome under concurrent replay --
the whole request, start to finish, runs as one critical section.
"""
from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from . import auth, errors, timeutil


@dataclass
class Table:
    id: str
    label: str
    capacity: int

    def to_json(self) -> dict:
        return {"id": self.id, "label": self.label, "capacity": self.capacity}


@dataclass
class Restaurant:
    id: str
    name: str
    timezone: str
    slot_minutes: int
    reservation_duration_minutes: int
    cancellation_cutoff_minutes: int
    opening_hours: list
    tables: list

    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def table(self, table_id: str) -> Table | None:
        for t in self.tables:
            if t.id == table_id:
                return t
        return None

    def windows_for(self, weekday: str) -> list:
        return [w for w in self.opening_hours if w["weekday"] == weekday]

    def to_public_json(self) -> dict:
        return {"id": self.id, "name": self.name, "timezone": self.timezone}

    def to_detail_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "timezone": self.timezone,
            "slot_minutes": self.slot_minutes,
            "reservation_duration_minutes": self.reservation_duration_minutes,
            "cancellation_cutoff_minutes": self.cancellation_cutoff_minutes,
            "opening_hours": [dict(w) for w in self.opening_hours],
            "tables": [t.to_json() for t in self.tables],
        }


@dataclass
class User:
    id: str
    email: str
    password_hash: str
    display_name: str


@dataclass
class Reservation:
    id: str
    reference: str
    user_id: str
    restaurant_id: str
    table_id: str
    party_size: int
    status: str
    starts_at_local: str
    starts_at: dt.datetime
    ends_at: dt.datetime
    created_at: dt.datetime

    def to_json(self, tz: ZoneInfo) -> dict:
        return {
            "reservation_id": self.id,
            "reference": self.reference,
            "restaurant_id": self.restaurant_id,
            "table_id": self.table_id,
            "party_size": self.party_size,
            "status": self.status,
            "starts_at_local": self.starts_at_local,
            "starts_at": timeutil.format_rfc3339(self.starts_at, tz),
            "ends_at": timeutil.format_rfc3339(self.ends_at, tz),
            "created_at": self.created_at.isoformat(timespec="seconds"),
        }


def _resolve_or_fallback(naive: dt.datetime, tz: ZoneInfo) -> dt.datetime:
    resolved = timeutil.resolve_local(naive, tz)
    if resolved is not None:
        return resolved
    return naive.replace(tzinfo=tz, fold=0).astimezone(dt.timezone.utc)


class Store:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._reset_locked()

    # -- lifecycle -----------------------------------------------------

    def _reset_locked(self) -> None:
        self.users: dict[str, User] = {}
        self.email_to_user: dict[str, str] = {}
        self.tokens: dict[str, str] = {}
        self.restaurants: dict[str, Restaurant] = {}
        self.reservations: dict[str, Reservation] = {}
        self.reference_to_id: dict[str, str] = {}
        self.idempotency: dict[tuple, dict] = {}

    def reset(self, fixture: dict) -> None:
        with self.lock:
            self._reset_locked()
            for u in fixture.get("users") or []:
                user = User(
                    id=str(u["id"]),
                    email=u["email"],
                    password_hash=auth.hash_password(u["password"]),
                    display_name=u.get("display_name", ""),
                )
                self.users[user.id] = user
                self.email_to_user[user.email] = user.id
            for r in fixture.get("restaurants") or []:
                tables = [Table(id=str(t["id"]), label=t.get("label", t["id"]),
                                 capacity=int(t["capacity"])) for t in r.get("tables") or []]
                restaurant = Restaurant(
                    id=str(r["id"]),
                    name=r.get("name", ""),
                    timezone=r["timezone"],
                    slot_minutes=int(r["slot_minutes"]),
                    reservation_duration_minutes=int(r["reservation_duration_minutes"]),
                    cancellation_cutoff_minutes=int(r["cancellation_cutoff_minutes"]),
                    opening_hours=[dict(w) for w in r.get("opening_hours") or []],
                    tables=tables,
                )
                self.restaurants[restaurant.id] = restaurant
            for res in fixture.get("reservations") or []:
                restaurant = self.restaurants.get(res["restaurant_id"])
                if restaurant is None:
                    continue
                naive = timeutil.parse_local(res["starts_at_local"])
                start_utc = _resolve_or_fallback(naive, restaurant.zone())
                end_utc = start_utc + dt.timedelta(minutes=restaurant.reservation_duration_minutes)
                reservation = Reservation(
                    id=str(res["id"]),
                    reference=str(res["reference"]),
                    user_id=str(res["user_id"]),
                    restaurant_id=restaurant.id,
                    table_id=res["table_id"],
                    party_size=int(res["party_size"]),
                    status=res.get("status", "confirmed"),
                    starts_at_local=res["starts_at_local"],
                    starts_at=start_utc,
                    ends_at=end_utc,
                    created_at=dt.datetime.now(dt.timezone.utc),
                )
                self.reservations[reservation.id] = reservation
                self.reference_to_id[reservation.reference] = reservation.id

    # -- export / import -------------------------------------------------

    def export_state(self) -> dict:
        with self.lock:
            return {
                "users": {
                    uid: {"id": u.id, "email": u.email, "password_hash": u.password_hash,
                          "display_name": u.display_name}
                    for uid, u in self.users.items()
                },
                "tokens": dict(self.tokens),
                "restaurants": {
                    rid: {
                        "id": r.id, "name": r.name, "timezone": r.timezone,
                        "slot_minutes": r.slot_minutes,
                        "reservation_duration_minutes": r.reservation_duration_minutes,
                        "cancellation_cutoff_minutes": r.cancellation_cutoff_minutes,
                        "opening_hours": [dict(w) for w in r.opening_hours],
                        "tables": [t.to_json() for t in r.tables],
                    }
                    for rid, r in self.restaurants.items()
                },
                "reservations": {
                    rid: {
                        "id": r.id, "reference": r.reference, "user_id": r.user_id,
                        "restaurant_id": r.restaurant_id, "table_id": r.table_id,
                        "party_size": r.party_size, "status": r.status,
                        "starts_at_local": r.starts_at_local,
                        "starts_at": r.starts_at.isoformat(),
                        "ends_at": r.ends_at.isoformat(),
                        "created_at": r.created_at.isoformat(),
                    }
                    for rid, r in self.reservations.items()
                },
                "idempotency": [
                    {"user_id": k[0], "key": k[1], "method": k[2], "path": k[3],
                     "status": v["status"], "body": v["body"], "response": v["response"]}
                    for k, v in self.idempotency.items()
                ],
            }

    def import_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            raise errors.validation_failed("state must be an object")
        try:
            users = {}
            email_to_user = {}
            for uid, u in (state.get("users") or {}).items():
                users[uid] = User(id=u["id"], email=u["email"], password_hash=u["password_hash"],
                                   display_name=u.get("display_name", ""))
                email_to_user[u["email"]] = u["id"]
            tokens = dict(state.get("tokens") or {})
            restaurants = {}
            for rid, r in (state.get("restaurants") or {}).items():
                tables = [Table(id=t["id"], label=t.get("label", t["id"]), capacity=int(t["capacity"]))
                          for t in r.get("tables") or []]
                restaurants[rid] = Restaurant(
                    id=r["id"], name=r.get("name", ""), timezone=r["timezone"],
                    slot_minutes=int(r["slot_minutes"]),
                    reservation_duration_minutes=int(r["reservation_duration_minutes"]),
                    cancellation_cutoff_minutes=int(r["cancellation_cutoff_minutes"]),
                    opening_hours=[dict(w) for w in r.get("opening_hours") or []],
                    tables=tables,
                )
            reservations = {}
            reference_to_id = {}
            for rid, r in (state.get("reservations") or {}).items():
                reservation = Reservation(
                    id=r["id"], reference=r["reference"], user_id=r["user_id"],
                    restaurant_id=r["restaurant_id"], table_id=r["table_id"],
                    party_size=int(r["party_size"]), status=r["status"],
                    starts_at_local=r["starts_at_local"],
                    starts_at=dt.datetime.fromisoformat(r["starts_at"]),
                    ends_at=dt.datetime.fromisoformat(r["ends_at"]),
                    created_at=dt.datetime.fromisoformat(r["created_at"]),
                )
                reservations[rid] = reservation
                reference_to_id[reservation.reference] = reservation.id
            idempotency = {}
            for rec in state.get("idempotency") or []:
                key = (rec["user_id"], rec["key"], rec["method"], rec["path"])
                idempotency[key] = {"body": rec["body"], "status": rec["status"], "response": rec["response"]}
        except (KeyError, TypeError, ValueError) as exc:
            raise errors.validation_failed(f"invalid state: {exc}") from None

        with self.lock:
            self.users = users
            self.email_to_user = email_to_user
            self.tokens = tokens
            self.restaurants = restaurants
            self.reservations = reservations
            self.reference_to_id = reference_to_id
            self.idempotency = idempotency

    # -- auth -------------------------------------------------------------

    def signup(self, email: str, password: str, display_name: str) -> tuple[User, str]:
        with self.lock:
            if email in self.email_to_user:
                raise errors.email_taken()
            user = User(id=auth.new_id("u"), email=email,
                        password_hash=auth.hash_password(password), display_name=display_name)
            self.users[user.id] = user
            self.email_to_user[email] = user.id
            token = auth.new_token()
            self.tokens[token] = user.id
            return user, token

    def login(self, email: str, password: str) -> tuple[User, str]:
        with self.lock:
            uid = self.email_to_user.get(email)
            user = self.users.get(uid) if uid else None
            if user is None or not auth.verify_password(password, user.password_hash):
                raise errors.unauthenticated("wrong email or password")
            token = auth.new_token()
            self.tokens[token] = user.id
            return user, token

    def user_for_token(self, token: str) -> User | None:
        with self.lock:
            uid = self.tokens.get(token)
            return self.users.get(uid) if uid else None

    # -- restaurants / availability ----------------------------------------

    def list_restaurants(self) -> list:
        with self.lock:
            return list(self.restaurants.values())

    def get_restaurant(self, rid: str) -> Restaurant | None:
        with self.lock:
            return self.restaurants.get(rid)

    def availability(self, restaurant: Restaurant, date: dt.date, party_size: int) -> list[dict]:
        with self.lock:
            weekday = timeutil.weekday_key(date)
            windows = restaurant.windows_for(weekday)
            starts: list[str] = []
            seen = set()
            for window in windows:
                open_m = timeutil.minutes_of(window["opens"])
                close_m = timeutil.minutes_of(window["closes"])
                t = open_m
                while t + restaurant.reservation_duration_minutes <= close_m:
                    hhmm = timeutil.hhmm_of(t)
                    if hhmm not in seen:
                        seen.add(hhmm)
                        starts.append(hhmm)
                    t += restaurant.slot_minutes
            starts.sort()

            slots = []
            tz = restaurant.zone()
            for hhmm in starts:
                naive = dt.datetime.combine(date, dt.time.fromisoformat(hhmm))
                start_utc = timeutil.resolve_local(naive, tz)
                if start_utc is None:
                    continue  # the skipped hour never appears in availability
                end_utc = start_utc + dt.timedelta(minutes=restaurant.reservation_duration_minutes)
                available = []
                for table in restaurant.tables:
                    if table.capacity < party_size:
                        continue
                    if self._overlapping(table.id, start_utc, end_utc) is None:
                        available.append(table.id)
                slots.append({
                    "starts_at_local": f"{date.isoformat()}T{hhmm}",
                    "starts_at": timeutil.format_rfc3339(start_utc, tz),
                    "available_table_ids": available,
                })
            return slots

    # -- reservation core ---------------------------------------------------

    def _overlapping(self, table_id: str, start_utc: dt.datetime, end_utc: dt.datetime,
                      exclude_ids: frozenset = frozenset()) -> Reservation | None:
        for r in self.reservations.values():
            if r.id in exclude_ids or r.table_id != table_id or r.status != "confirmed":
                continue
            if r.starts_at < end_utc and start_utc < r.ends_at:
                return r
        return None

    def _resolve_and_validate(self, restaurant: Restaurant, table_id: str,
                               starts_at_local: str, party_size: int):
        table = restaurant.table(table_id)
        if table is None:
            raise errors.not_found("unknown table")
        naive = timeutil.parse_local(starts_at_local)
        if naive is None:
            raise errors.validation_failed("starts_at_local must be YYYY-MM-DDTHH:MM")
        start_utc = timeutil.resolve_local(naive, restaurant.zone())
        if start_utc is None:
            raise errors.invalid_local_time()

        weekday = timeutil.weekday_key(naive.date())
        day_minutes = naive.hour * 60 + naive.minute
        windows = restaurant.windows_for(weekday)
        containing = [w for w in windows
                      if timeutil.minutes_of(w["opens"]) <= day_minutes < timeutil.minutes_of(w["closes"])]
        if not containing:
            raise errors.outside_opening_hours()
        aligned = [w for w in containing
                   if (day_minutes - timeutil.minutes_of(w["opens"])) % restaurant.slot_minutes == 0]
        if not aligned:
            raise errors.not_on_slot_grid()
        fits = [w for w in aligned
                if day_minutes + restaurant.reservation_duration_minutes <= timeutil.minutes_of(w["closes"])]
        if not fits:
            raise errors.outside_opening_hours()

        if party_size > table.capacity:
            raise errors.party_exceeds_capacity()

        end_utc = start_utc + dt.timedelta(minutes=restaurant.reservation_duration_minutes)
        local_str = naive.strftime("%Y-%m-%dT%H:%M")
        return table, start_utc, end_utc, local_str

    def create_reservation(self, user: User, restaurant_id: str, table_id: str,
                            starts_at_local: str, party_size: int):
        with self.lock:
            restaurant = self.restaurants.get(restaurant_id)
            if restaurant is None:
                raise errors.not_found("unknown restaurant")
            table, start_utc, end_utc, local_str = self._resolve_and_validate(
                restaurant, table_id, starts_at_local, party_size)
            if self._overlapping(table.id, start_utc, end_utc) is not None:
                raise errors.table_unavailable()
            reservation = Reservation(
                id=auth.new_id("res"),
                reference=auth.new_reference(set(self.reference_to_id)),
                user_id=user.id,
                restaurant_id=restaurant.id,
                table_id=table.id,
                party_size=party_size,
                status="confirmed",
                starts_at_local=local_str,
                starts_at=start_utc,
                ends_at=end_utc,
                created_at=dt.datetime.now(dt.timezone.utc),
            )
            self.reservations[reservation.id] = reservation
            self.reference_to_id[reservation.reference] = reservation.id
            return reservation, restaurant

    def user_reservations(self, user: User) -> list[Reservation]:
        with self.lock:
            mine = [r for r in self.reservations.values() if r.user_id == user.id]
            mine.sort(key=lambda r: r.starts_at, reverse=True)
            return mine

    def _find_owned(self, user: User, reference: str) -> Reservation:
        rid = self.reference_to_id.get(reference)
        reservation = self.reservations.get(rid) if rid else None
        if reservation is None or reservation.user_id != user.id:
            raise errors.not_found("unknown reservation")
        return reservation

    def get_owned_reservation(self, user: User, reference: str) -> Reservation:
        with self.lock:
            return self._find_owned(user, reference)

    def _check_cutoff(self, reservation: Reservation, restaurant: Restaurant) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        cutoff_at = reservation.starts_at - dt.timedelta(minutes=restaurant.cancellation_cutoff_minutes)
        if now >= cutoff_at:
            raise errors.cutoff_passed()

    def cancel_reservation(self, user: User, reference: str):
        with self.lock:
            reservation = self._find_owned(user, reference)
            restaurant = self.restaurants[reservation.restaurant_id]
            if reservation.status == "cancelled":
                return reservation, restaurant
            self._check_cutoff(reservation, restaurant)
            reservation.status = "cancelled"
            return reservation, restaurant

    def amend_reservation(self, user: User, reference: str, changes: dict):
        with self.lock:
            reservation = self._find_owned(user, reference)
            restaurant = self.restaurants[reservation.restaurant_id]
            if reservation.status == "cancelled":
                raise errors.reservation_cancelled()
            self._check_cutoff(reservation, restaurant)
            table_id = changes.get("table_id", reservation.table_id)
            starts_at_local = changes.get("starts_at_local", reservation.starts_at_local)
            party_size = changes.get("party_size", reservation.party_size)
            table, start_utc, end_utc, local_str = self._resolve_and_validate(
                restaurant, table_id, starts_at_local, party_size)
            conflict = self._overlapping(table.id, start_utc, end_utc, exclude_ids=frozenset({reservation.id}))
            if conflict is not None:
                raise errors.table_unavailable()
            reservation.table_id = table.id
            reservation.starts_at_local = local_str
            reservation.starts_at = start_utc
            reservation.ends_at = end_utc
            reservation.party_size = party_size
            return reservation, restaurant

    def reservation_moves(self, user: User, moves: list[dict]):
        with self.lock:
            resolved = [(m, self._find_owned(user, m["reference"])) for m in moves]
            restaurant_ids = {r.restaurant_id for _, r in resolved}
            if len(restaurant_ids) > 1:
                raise errors.validation_failed("all bookings in a move must share one restaurant")
            restaurant = self.restaurants[next(iter(restaurant_ids))]

            planned = []
            for m, reservation in resolved:
                if reservation.status == "cancelled":
                    raise errors.reservation_cancelled()
                self._check_cutoff(reservation, restaurant)
                table_id = m.get("table_id", reservation.table_id)
                starts_at_local = m.get("starts_at_local", reservation.starts_at_local)
                party_size = m.get("party_size", reservation.party_size)
                table, start_utc, end_utc, local_str = self._resolve_and_validate(
                    restaurant, table_id, starts_at_local, party_size)
                planned.append((reservation, table.id, start_utc, end_utc, local_str, party_size))

            moved_ids = frozenset(r.id for r, *_ in planned)
            for i, (_, table_id, start_utc, end_utc, _local, _party) in enumerate(planned):
                for j, (_, other_table, other_start, other_end, _l2, _p2) in enumerate(planned):
                    if i != j and other_table == table_id and other_start < end_utc and start_utc < other_end:
                        raise errors.table_unavailable()
                if self._overlapping(table_id, start_utc, end_utc, exclude_ids=moved_ids) is not None:
                    raise errors.table_unavailable()

            for reservation, table_id, start_utc, end_utc, local_str, party_size in planned:
                reservation.table_id = table_id
                reservation.starts_at = start_utc
                reservation.ends_at = end_utc
                reservation.starts_at_local = local_str
                reservation.party_size = party_size
            return [reservation for reservation, *_ in planned], restaurant

    # -- idempotency ----------------------------------------------------

    def idempotency_lookup(self, user: User, key: str, method: str, path: str):
        with self.lock:
            return self.idempotency.get((user.id, key, method, path))

    def idempotency_store(self, user: User, key: str, method: str, path: str,
                           body, status: int, response) -> None:
        with self.lock:
            self.idempotency[(user.id, key, method, path)] = {
                "body": body, "status": status, "response": response}
