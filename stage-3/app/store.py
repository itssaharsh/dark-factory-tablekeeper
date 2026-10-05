"""In-memory data store: restaurants, tables, policies, users, reservations, series.

Every write and every read that must see a consistent snapshot runs while the
caller holds `Store.lock` (a single process-wide `threading.RLock`). Handlers
acquire it once per request, which trivially gives us: no two confirmed
reservations ever overlap a table even under concurrent requests, and an
idempotency key resolves to exactly one outcome under concurrent replay --
the whole request, start to finish, runs as one critical section.

Policies (stage-3 "Policies and accepted terms") are versioned, immutable and
dated. Policy 0 is synthesized from the fixture's own restaurant fields at
reset/import time and always exists; published policies (version >= 1) are
kept in publication order in `Restaurant.policies`. `Restaurant.policy_for_date`
picks the one that applies to a given local calendar date. A reservation's
`accepted_terms` is a frozen snapshot of the policy that applied when it was
created or last amended -- cutoff checks and history always read that
snapshot, never the restaurant's live configuration, so publishing a new
policy never reaches back into an already-accepted booking.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from . import auth, errors, timeutil, validation

ID_MAX_LEN = 64
_REFERENCE_RE = re.compile(r"^[A-Z0-9]{6,12}$")
_HHMM_RE = re.compile(r"^([0-1][0-9]|2[0-3]):[0-5][0-9]$")
_WEEKDAYS = frozenset(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))


def _checked_id(value, label: str) -> str:
    text = str(value)
    if not text or len(text) > ID_MAX_LEN:
        raise errors.validation_failed(f"{label} must be 1 to {ID_MAX_LEN} characters")
    return text


def _checked_opening_hours(entries, *, forbid_duplicate_weekdays: bool = False) -> list:
    checked = []
    seen_weekdays: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise errors.validation_failed("each opening_hours entry must be an object")
        weekday = entry.get("weekday")
        opens, closes = entry.get("opens"), entry.get("closes")
        if weekday not in _WEEKDAYS:
            raise errors.validation_failed("opening_hours weekday must be one of mon..sun")
        if not isinstance(opens, str) or not isinstance(closes, str) \
                or not _HHMM_RE.match(opens) or not _HHMM_RE.match(closes):
            raise errors.validation_failed("opening_hours opens/closes must be HH:MM")
        if timeutil.minutes_of(closes) <= timeutil.minutes_of(opens):
            raise errors.validation_failed(
                "opening_hours closes must be later than opens on the same local day")
        if forbid_duplicate_weekdays:
            if weekday in seen_weekdays:
                raise errors.validation_failed("a policy's opening_hours must not repeat a weekday")
            seen_weekdays.add(weekday)
        checked.append({"weekday": weekday, "opens": opens, "closes": closes})
    return checked


def _checked_combinable(entries, table_ids: set) -> list:
    checked = []
    for pair in entries:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or pair[0] == pair[1] or pair[0] not in table_ids or pair[1] not in table_ids):
            raise errors.validation_failed(
                "combinable entries must be a pair of distinct table ids of that restaurant")
        checked.append([pair[0], pair[1]])
    return checked


def _seeded_table_ids(res: dict) -> list:
    table_ids = res.get("table_ids")
    if table_ids is not None:
        if not isinstance(table_ids, list) or not table_ids:
            raise errors.validation_failed("seeded reservation table_ids must be a non-empty list")
        return list(table_ids)
    if "table_id" in res and res["table_id"] is not None:
        return [res["table_id"]]
    raise errors.validation_failed("seeded reservation needs table_id or table_ids")


def _creation_changes(table_ids: list, starts_at_local: str, party_size: int) -> list:
    changes = []
    if len(table_ids) == 1:
        changes.append({"field": "table_id", "from": None, "to": table_ids[0]})
    else:
        changes.append({"field": "table_ids", "from": None, "to": list(table_ids)})
    changes.append({"field": "starts_at_local", "from": None, "to": starts_at_local})
    changes.append({"field": "party_size", "from": None, "to": party_size})
    return changes


def _table_change(old_ids: list, new_ids: list) -> dict | None:
    """A reversed/reordered pair names the same set and is not a change on its own."""
    if set(old_ids) == set(new_ids):
        return None
    if len(old_ids) == 1 and len(new_ids) == 1:
        return {"field": "table_id", "from": old_ids[0], "to": new_ids[0]}
    return {"field": "table_ids", "from": list(old_ids), "to": list(new_ids)}


def _history_entry(seq: int, at_utc: dt.datetime, event: str, changes: list,
                    revision: int, accepted_terms: dict, tz: ZoneInfo) -> dict:
    return {
        "seq": seq,
        "at": timeutil.format_rfc3339(at_utc, tz),
        "event": event,
        "changes": changes,
        "revision": revision,
        "accepted_terms": dict(accepted_terms),
    }


@dataclass
class Table:
    id: str
    label: str
    capacity: int

    def to_json(self) -> dict:
        return {"id": self.id, "label": self.label, "capacity": self.capacity}


@dataclass
class Policy:
    effective_from: str  # "" for policy 0 -- sorts before every real YYYY-MM-DD
    policy_version: int
    slot_minutes: int
    reservation_duration_minutes: int
    cancellation_cutoff_minutes: int
    opening_hours: list
    capacities: dict  # table_id -> int

    def windows_for(self, weekday: str) -> list:
        return [w for w in self.opening_hours if w["weekday"] == weekday]

    def terms_json(self) -> dict:
        return {
            "policy_version": self.policy_version,
            "slot_minutes": self.slot_minutes,
            "reservation_duration_minutes": self.reservation_duration_minutes,
            "cancellation_cutoff_minutes": self.cancellation_cutoff_minutes,
            "opening_hours": [dict(w) for w in self.opening_hours],
            "capacities": dict(self.capacities),
        }

    def to_json(self) -> dict:
        body = {"effective_from": self.effective_from}
        body.update(self.terms_json())
        return body


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
    combinable: list  # list of [table_id, table_id] pairs, in declared order
    manager_user_ids: list
    policy0: Policy
    policies: list  # published Policy objects, version >= 1, in publication order
    revision: int = 0

    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def table(self, table_id: str) -> Table | None:
        for t in self.tables:
            if t.id == table_id:
                return t
        return None

    def combination_for(self, table_ids: set) -> list[str] | None:
        """The declared pair matching this unordered set, in `combinable` order."""
        for pair in self.combinable:
            if set(pair) == table_ids:
                return list(pair)
        return None

    def is_manager(self, user_id: str) -> bool:
        return user_id in self.manager_user_ids

    def policy_for_date(self, date: dt.date) -> Policy:
        """Greatest `effective_from` not later than `date`; ties pick the greatest version."""
        date_str = date.isoformat()
        best = self.policy0
        for p in self.policies:
            if p.effective_from <= date_str \
                    and (p.effective_from, p.policy_version) >= (best.effective_from, best.policy_version):
                best = p
        return best

    def to_public_json(self) -> dict:
        return {"id": self.id, "name": self.name, "timezone": self.timezone}

    def to_detail_json(self) -> dict:
        """The restaurant's original fixture configuration -- policies never change this."""
        return {
            "id": self.id,
            "name": self.name,
            "timezone": self.timezone,
            "slot_minutes": self.slot_minutes,
            "reservation_duration_minutes": self.reservation_duration_minutes,
            "cancellation_cutoff_minutes": self.cancellation_cutoff_minutes,
            "opening_hours": [dict(w) for w in self.opening_hours],
            "tables": [t.to_json() for t in self.tables],
            "combinable": [list(p) for p in self.combinable],
        }


@dataclass
class User:
    id: str
    email: str
    password_hash: str
    display_name: str


@dataclass
class Series:
    id: str
    user_id: str
    restaurant_id: str
    interval_weeks: int
    revision: int
    occurrences: list  # [{"index": int, "reservation_id": str, "exception": bool}, ...]


@dataclass
class Reservation:
    id: str
    reference: str
    user_id: str
    restaurant_id: str
    table_ids: list
    party_size: int
    status: str
    starts_at_local: str
    starts_at: dt.datetime
    ends_at: dt.datetime
    created_at: dt.datetime
    revision: int
    accepted_terms: dict
    history: list = field(default_factory=list)
    series_id: str | None = None
    series_index: int | None = None

    def to_json(self, tz: ZoneInfo) -> dict:
        body = {
            "reservation_id": self.id,
            "reference": self.reference,
            "restaurant_id": self.restaurant_id,
            "table_ids": list(self.table_ids),
            "party_size": self.party_size,
            "status": self.status,
            "starts_at_local": self.starts_at_local,
            "starts_at": timeutil.format_rfc3339(self.starts_at, tz),
            "ends_at": timeutil.format_rfc3339(self.ends_at, tz),
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "revision": self.revision,
            "accepted_terms": dict(self.accepted_terms),
        }
        if len(self.table_ids) == 1:
            body["table_id"] = self.table_ids[0]
        return body


def _resolve_or_fallback(naive: dt.datetime, tz: ZoneInfo) -> dt.datetime:
    resolved = timeutil.resolve_local(naive, tz)
    if resolved is not None:
        return resolved
    return naive.replace(tzinfo=tz, fold=0).astimezone(dt.timezone.utc)


def _policy0_from_fixture(r: dict, tables: list) -> Policy:
    return Policy(
        effective_from="", policy_version=0,
        slot_minutes=int(r["slot_minutes"]),
        reservation_duration_minutes=int(r["reservation_duration_minutes"]),
        cancellation_cutoff_minutes=int(r["cancellation_cutoff_minutes"]),
        opening_hours=_checked_opening_hours(r.get("opening_hours") or []),
        capacities={t.id: t.capacity for t in tables},
    )


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
        self.series: dict[str, Series] = {}

    def reset(self, fixture: dict) -> None:
        # Validate and build the whole new state before touching `self`, so a
        # bad fixture (e.g. an id over the 64-character limit) leaves the
        # current state untouched instead of applying a partial reset.
        users: dict[str, User] = {}
        email_to_user: dict[str, str] = {}
        for u in fixture.get("users") or []:
            uid = _checked_id(u["id"], "user id")
            user = User(id=uid, email=u["email"], password_hash=auth.hash_password(u["password"]),
                        display_name=u.get("display_name", ""))
            users[user.id] = user
            email_to_user[user.email] = user.id

        restaurants: dict[str, Restaurant] = {}
        for r in fixture.get("restaurants") or []:
            rid = _checked_id(r["id"], "restaurant id")
            tables = [Table(id=_checked_id(t["id"], "table id"), label=t.get("label", t["id"]),
                             capacity=int(t["capacity"])) for t in r.get("tables") or []]
            policy0 = _policy0_from_fixture(r, tables)
            restaurant = Restaurant(
                id=rid,
                name=r.get("name", ""),
                timezone=r["timezone"],
                slot_minutes=policy0.slot_minutes,
                reservation_duration_minutes=policy0.reservation_duration_minutes,
                cancellation_cutoff_minutes=policy0.cancellation_cutoff_minutes,
                opening_hours=policy0.opening_hours,
                tables=tables,
                combinable=_checked_combinable(r.get("combinable") or [], {t.id for t in tables}),
                manager_user_ids=[str(x) for x in r.get("manager_user_ids") or []],
                policy0=policy0,
                policies=[],
            )
            restaurants[restaurant.id] = restaurant

        reservations: dict[str, Reservation] = {}
        reference_to_id: dict[str, str] = {}
        for res in fixture.get("reservations") or []:
            restaurant = restaurants.get(res["restaurant_id"])
            if restaurant is None:
                continue
            reservation_id = _checked_id(res["id"], "reservation id")
            reference = str(res["reference"])
            if not _REFERENCE_RE.match(reference):
                raise errors.validation_failed(
                    "reservation reference must be 6 to 12 characters of A-Z0-9")
            user_id = _checked_id(res["user_id"], "user id")
            naive = timeutil.parse_local(res["starts_at_local"])
            start_utc = _resolve_or_fallback(naive, restaurant.zone())
            end_utc = start_utc + dt.timedelta(minutes=restaurant.policy0.reservation_duration_minutes)
            table_ids = _seeded_table_ids(res)
            party_size = int(res["party_size"])
            accepted_terms = restaurant.policy0.terms_json()
            created_at = dt.datetime.now(dt.timezone.utc)
            reservation = Reservation(
                id=reservation_id,
                reference=reference,
                user_id=user_id,
                restaurant_id=restaurant.id,
                table_ids=table_ids,
                party_size=party_size,
                status=res.get("status", "confirmed"),
                starts_at_local=res["starts_at_local"],
                starts_at=start_utc,
                ends_at=end_utc,
                created_at=created_at,
                revision=1,
                accepted_terms=accepted_terms,
                history=[],
            )
            reservation.history.append(_history_entry(
                1, created_at, "created", _creation_changes(table_ids, res["starts_at_local"], party_size),
                1, accepted_terms, restaurant.zone()))
            reservations[reservation.id] = reservation
            reference_to_id[reservation.reference] = reservation.id

        with self.lock:
            self._reset_locked()
            self.users = users
            self.email_to_user = email_to_user
            self.restaurants = restaurants
            self.reservations = reservations
            self.reference_to_id = reference_to_id

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
                        "combinable": [list(p) for p in r.combinable],
                        "manager_user_ids": list(r.manager_user_ids),
                        "policies": [p.to_json() for p in r.policies],
                        "revision": r.revision,
                    }
                    for rid, r in self.restaurants.items()
                },
                "reservations": {
                    rid: {
                        "id": r.id, "reference": r.reference, "user_id": r.user_id,
                        "restaurant_id": r.restaurant_id, "table_ids": list(r.table_ids),
                        "party_size": r.party_size, "status": r.status,
                        "starts_at_local": r.starts_at_local,
                        "starts_at": r.starts_at.isoformat(),
                        "ends_at": r.ends_at.isoformat(),
                        "created_at": r.created_at.isoformat(),
                        "revision": r.revision,
                        "accepted_terms": dict(r.accepted_terms),
                        "history": [dict(e) for e in r.history],
                        "series_id": r.series_id,
                        "series_index": r.series_index,
                    }
                    for rid, r in self.reservations.items()
                },
                "series": {
                    sid: {
                        "id": s.id, "user_id": s.user_id, "restaurant_id": s.restaurant_id,
                        "interval_weeks": s.interval_weeks, "revision": s.revision,
                        "occurrences": [dict(o) for o in s.occurrences],
                    }
                    for sid, s in self.series.items()
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
                policy0 = Policy(
                    effective_from="", policy_version=0,
                    slot_minutes=int(r["slot_minutes"]),
                    reservation_duration_minutes=int(r["reservation_duration_minutes"]),
                    cancellation_cutoff_minutes=int(r["cancellation_cutoff_minutes"]),
                    opening_hours=[dict(w) for w in r.get("opening_hours") or []],
                    capacities={t.id: t.capacity for t in tables},
                )
                policies = []
                for p in r.get("policies") or []:
                    policies.append(Policy(
                        effective_from=p["effective_from"], policy_version=int(p["policy_version"]),
                        slot_minutes=int(p["slot_minutes"]),
                        reservation_duration_minutes=int(p["reservation_duration_minutes"]),
                        cancellation_cutoff_minutes=int(p["cancellation_cutoff_minutes"]),
                        opening_hours=[dict(w) for w in p.get("opening_hours") or []],
                        capacities=dict(p.get("capacities") or {}),
                    ))
                # a stage-1/2 export has no "combinable", "manager_user_ids" or "policies" field
                restaurants[rid] = Restaurant(
                    id=r["id"], name=r.get("name", ""), timezone=r["timezone"],
                    slot_minutes=policy0.slot_minutes,
                    reservation_duration_minutes=policy0.reservation_duration_minutes,
                    cancellation_cutoff_minutes=policy0.cancellation_cutoff_minutes,
                    opening_hours=policy0.opening_hours,
                    tables=tables,
                    combinable=[list(p) for p in r.get("combinable") or []],
                    manager_user_ids=list(r.get("manager_user_ids") or []),
                    policy0=policy0, policies=policies,
                    revision=int(r.get("revision", 0)),
                )
            reservations = {}
            reference_to_id = {}
            for rid, r in (state.get("reservations") or {}).items():
                # a stage-1 export has "table_id" (singular) instead of "table_ids"
                table_ids = r.get("table_ids") or [r["table_id"]]
                restaurant = restaurants.get(r["restaurant_id"])
                accepted_terms = r.get("accepted_terms")
                if accepted_terms is None:
                    accepted_terms = restaurant.policy0.terms_json() if restaurant else {}
                revision = int(r.get("revision") or 1)
                history = r.get("history")
                if history is None:
                    tz = restaurant.zone() if restaurant else dt.timezone.utc
                    history = [_history_entry(
                        1, dt.datetime.fromisoformat(r["created_at"]), "created",
                        _creation_changes(list(table_ids), r["starts_at_local"], int(r["party_size"])),
                        1, accepted_terms, tz)]
                reservation = Reservation(
                    id=r["id"], reference=r["reference"], user_id=r["user_id"],
                    restaurant_id=r["restaurant_id"], table_ids=list(table_ids),
                    party_size=int(r["party_size"]), status=r["status"],
                    starts_at_local=r["starts_at_local"],
                    starts_at=dt.datetime.fromisoformat(r["starts_at"]),
                    ends_at=dt.datetime.fromisoformat(r["ends_at"]),
                    created_at=dt.datetime.fromisoformat(r["created_at"]),
                    revision=revision, accepted_terms=accepted_terms, history=list(history),
                    series_id=r.get("series_id"), series_index=r.get("series_index"),
                )
                reservations[rid] = reservation
                reference_to_id[reservation.reference] = reservation.id
            series = {}
            for sid, s in (state.get("series") or {}).items():
                series[sid] = Series(
                    id=s["id"], user_id=s["user_id"], restaurant_id=s["restaurant_id"],
                    interval_weeks=int(s["interval_weeks"]), revision=int(s["revision"]),
                    occurrences=[dict(o) for o in s.get("occurrences") or []],
                )
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
            self.series = series

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

    # -- restaurants / policies / availability ------------------------------

    def list_restaurants(self) -> list:
        with self.lock:
            return list(self.restaurants.values())

    def get_restaurant(self, rid: str) -> Restaurant | None:
        with self.lock:
            return self.restaurants.get(rid)

    def _checked_policy_capacities(self, restaurant: Restaurant, value) -> dict:
        if not isinstance(value, dict):
            raise errors.validation_failed("capacities must be an object")
        table_ids = {t.id for t in restaurant.tables}
        if set(value.keys()) != table_ids:
            raise errors.validation_failed("capacities must name exactly the restaurant's table ids")
        out = {}
        for tid, cap in value.items():
            if isinstance(cap, bool) or not isinstance(cap, int) or not (1 <= cap <= 100):
                raise errors.validation_failed("capacities must be integers 1..100")
            out[tid] = cap
        return out

    def _checked_policy_body(self, restaurant: Restaurant, body: dict) -> dict:
        effective_from = body.get("effective_from")
        if not isinstance(effective_from, str) or timeutil.parse_date(effective_from) is None:
            raise errors.validation_failed("effective_from must be an actual YYYY-MM-DD date")

        def _int_in(name: str, lo: int, hi: int) -> int:
            v = body.get(name)
            if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi):
                raise errors.validation_failed(f"{name} must be an integer {lo}..{hi}")
            return v

        slot_minutes = _int_in("slot_minutes", 1, 1440)
        duration = _int_in("reservation_duration_minutes", 1, 1440)
        cutoff = _int_in("cancellation_cutoff_minutes", 0, 10080)
        opening_hours_raw = body.get("opening_hours")
        if not isinstance(opening_hours_raw, list):
            raise errors.validation_failed("opening_hours must be a list")
        opening_hours = _checked_opening_hours(opening_hours_raw, forbid_duplicate_weekdays=True)
        capacities = self._checked_policy_capacities(restaurant, body.get("capacities"))
        return dict(effective_from=effective_from, slot_minutes=slot_minutes,
                    reservation_duration_minutes=duration, cancellation_cutoff_minutes=cutoff,
                    opening_hours=opening_hours, capacities=capacities)

    def publish_policy(self, user: User, restaurant_id: str, body: dict):
        with self.lock:
            restaurant = self.restaurants.get(restaurant_id)
            if restaurant is None:
                raise errors.not_found("unknown restaurant")
            if not restaurant.is_manager(user.id):
                raise errors.forbidden("only a manager may publish policies for this restaurant")
            fields = self._checked_policy_body(restaurant, body)
            version = len(restaurant.policies) + 1
            policy = Policy(
                effective_from=fields["effective_from"], policy_version=version,
                slot_minutes=fields["slot_minutes"],
                reservation_duration_minutes=fields["reservation_duration_minutes"],
                cancellation_cutoff_minutes=fields["cancellation_cutoff_minutes"],
                opening_hours=fields["opening_hours"], capacities=fields["capacities"],
            )
            restaurant.policies.append(policy)
            restaurant.revision += 1
            return policy

    def list_policies(self, restaurant_id: str) -> list:
        with self.lock:
            restaurant = self.restaurants.get(restaurant_id)
            if restaurant is None:
                raise errors.not_found("unknown restaurant")
            return [p.to_json() for p in restaurant.policies]

    def availability(self, restaurant: Restaurant, date: dt.date, party_size: int,
                      *, explain: bool = False) -> list[dict]:
        with self.lock:
            policy = restaurant.policy_for_date(date)
            weekday = timeutil.weekday_key(date)
            windows = policy.windows_for(weekday)
            starts: list[str] = []
            seen = set()
            for window in windows:
                open_m = timeutil.minutes_of(window["opens"])
                close_m = timeutil.minutes_of(window["closes"])
                t = open_m
                while t + policy.reservation_duration_minutes <= close_m:
                    hhmm = timeutil.hhmm_of(t)
                    if hhmm not in seen:
                        seen.add(hhmm)
                        starts.append(hhmm)
                    t += policy.slot_minutes
            starts.sort()

            slots = []
            tz = restaurant.zone()
            for hhmm in starts:
                naive = dt.datetime.combine(date, dt.time.fromisoformat(hhmm))
                start_utc = timeutil.resolve_local(naive, tz)
                if start_utc is None:
                    continue  # the skipped hour never appears in availability
                end_utc = start_utc + dt.timedelta(minutes=policy.reservation_duration_minutes)
                available = []
                options = []
                explain_list = []
                for table in restaurant.tables:
                    capacity = policy.capacities.get(table.id, table.capacity)
                    capacity_holds = capacity >= party_size
                    no_overlap_holds = self._overlapping(
                        restaurant.id, [table.id], start_utc, end_utc) is None
                    is_available = capacity_holds and no_overlap_holds
                    if is_available:
                        available.append(table.id)
                        options.append({"table_ids": [table.id], "capacity": capacity})
                    if explain:
                        explain_list.append({
                            "table_id": table.id,
                            "policy_version": policy.policy_version,
                            "available": is_available,
                            "rules": [
                                {"rule": "capacity", "holds": capacity_holds},
                                {"rule": "no_overlap", "holds": no_overlap_holds},
                            ],
                        })
                for pair in restaurant.combinable:
                    tables = [restaurant.table(tid) for tid in pair]
                    capacity = sum(policy.capacities.get(t.id, t.capacity) for t in tables)
                    if capacity < party_size:
                        continue
                    if self._overlapping(restaurant.id, pair, start_utc, end_utc) is None:
                        options.append({"table_ids": list(pair), "capacity": capacity})
                slot = {
                    "starts_at_local": f"{date.isoformat()}T{hhmm}",
                    "starts_at": timeutil.format_rfc3339(start_utc, tz),
                    "available_table_ids": available,
                    "available_options": options,
                }
                if explain:
                    slot["explain"] = explain_list
                slots.append(slot)
            return slots

    # -- reservation core ---------------------------------------------------

    def _overlapping(self, restaurant_id: str, table_ids, start_utc: dt.datetime,
                      end_utc: dt.datetime, exclude_ids: frozenset = frozenset()) -> Reservation | None:
        # Table ids are only unique within a restaurant, so both must match:
        # two restaurants may each have their own "t_2". A reservation blocks
        # a candidate set if it holds any table the candidate set wants.
        wanted = set(table_ids)
        for r in self.reservations.values():
            if (r.id in exclude_ids or r.restaurant_id != restaurant_id
                    or r.status != "confirmed" or wanted.isdisjoint(r.table_ids)):
                continue
            if r.starts_at < end_utc and start_utc < r.ends_at:
                return r
        return None

    def _resolve_and_validate(self, restaurant: Restaurant, table_ids: list,
                               starts_at_local: str, party_size: int):
        if len(table_ids) > 2:
            raise errors.combination_not_allowed()
        tables = []
        for tid in table_ids:
            table = restaurant.table(tid)
            if table is None:
                raise errors.not_found("unknown table")
            tables.append(table)
        if len(tables) == 2:
            canonical = restaurant.combination_for({tables[0].id, tables[1].id})
            if canonical is None:
                raise errors.combination_not_allowed()
            tables = [restaurant.table(tid) for tid in canonical]

        naive = timeutil.parse_local(starts_at_local)
        if naive is None:
            raise errors.validation_failed("starts_at_local must be YYYY-MM-DDTHH:MM")
        start_utc = timeutil.resolve_local(naive, restaurant.zone())
        if start_utc is None:
            raise errors.invalid_local_time()

        policy = restaurant.policy_for_date(naive.date())
        weekday = timeutil.weekday_key(naive.date())
        day_minutes = naive.hour * 60 + naive.minute
        windows = policy.windows_for(weekday)
        containing = [w for w in windows
                      if timeutil.minutes_of(w["opens"]) <= day_minutes < timeutil.minutes_of(w["closes"])]
        if not containing:
            raise errors.outside_opening_hours()
        aligned = [w for w in containing
                   if (day_minutes - timeutil.minutes_of(w["opens"])) % policy.slot_minutes == 0]
        if not aligned:
            raise errors.not_on_slot_grid()
        fits = [w for w in aligned
                if day_minutes + policy.reservation_duration_minutes <= timeutil.minutes_of(w["closes"])]
        if not fits:
            raise errors.outside_opening_hours()

        capacity = sum(policy.capacities.get(t.id, t.capacity) for t in tables)
        if party_size > capacity:
            raise errors.party_exceeds_capacity()

        end_utc = start_utc + dt.timedelta(minutes=policy.reservation_duration_minutes)
        local_str = naive.strftime("%Y-%m-%dT%H:%M")
        resolved_ids = [t.id for t in tables]
        return resolved_ids, start_utc, end_utc, local_str, policy

    def create_reservation(self, user: User, restaurant_id: str, table_ids: list,
                            starts_at_local: str, party_size: int):
        with self.lock:
            restaurant = self.restaurants.get(restaurant_id)
            if restaurant is None:
                raise errors.not_found("unknown restaurant")
            resolved_ids, start_utc, end_utc, local_str, policy = self._resolve_and_validate(
                restaurant, table_ids, starts_at_local, party_size)
            if self._overlapping(restaurant.id, resolved_ids, start_utc, end_utc) is not None:
                raise errors.table_unavailable()
            reservation = self._new_reservation(
                user, restaurant, resolved_ids, start_utc, end_utc, local_str, party_size, policy)
            return reservation, restaurant

    def _new_reservation(self, user: User, restaurant: Restaurant, resolved_ids: list,
                          start_utc: dt.datetime, end_utc: dt.datetime, local_str: str,
                          party_size: int, policy: Policy) -> Reservation:
        accepted_terms = policy.terms_json()
        now = dt.datetime.now(dt.timezone.utc)
        reservation = Reservation(
            id=auth.new_id("res"),
            reference=auth.new_reference(set(self.reference_to_id)),
            user_id=user.id,
            restaurant_id=restaurant.id,
            table_ids=resolved_ids,
            party_size=party_size,
            status="confirmed",
            starts_at_local=local_str,
            starts_at=start_utc,
            ends_at=end_utc,
            created_at=now,
            revision=1,
            accepted_terms=accepted_terms,
            history=[],
        )
        reservation.history.append(_history_entry(
            1, now, "created", _creation_changes(resolved_ids, local_str, party_size),
            1, accepted_terms, restaurant.zone()))
        self.reservations[reservation.id] = reservation
        self.reference_to_id[reservation.reference] = reservation.id
        return reservation

    def user_reservations(self, user: User) -> list[Reservation]:
        with self.lock:
            mine = [r for r in self.reservations.values() if r.user_id == user.id]
            mine.sort(key=lambda r: r.starts_at, reverse=True)
            return mine

    def _find_visible(self, viewer: User | None, reference: str) -> Reservation:
        rid = self.reference_to_id.get(reference)
        reservation = self.reservations.get(rid) if rid else None
        if reservation is None or viewer is None or reservation.user_id != viewer.id:
            raise errors.not_found("unknown reservation")
        return reservation

    def _find_owned(self, user: User, reference: str) -> Reservation:
        return self._find_visible(user, reference)

    def get_owned_reservation(self, user: User, reference: str) -> Reservation:
        with self.lock:
            return self._find_owned(user, reference)

    def get_history(self, viewer: User | None, reference: str) -> Reservation:
        with self.lock:
            return self._find_visible(viewer, reference)

    def get_decision(self, viewer: User | None, reference: str) -> Reservation:
        with self.lock:
            return self._find_visible(viewer, reference)

    def _check_cutoff(self, reservation: Reservation) -> None:
        """Always checked against the reservation's own accepted terms, never a live policy."""
        now = dt.datetime.now(dt.timezone.utc)
        cutoff_minutes = reservation.accepted_terms["cancellation_cutoff_minutes"]
        cutoff_at = reservation.starts_at - dt.timedelta(minutes=cutoff_minutes)
        if now >= cutoff_at:
            raise errors.cutoff_passed()

    def cancel_reservation(self, user: User, reference: str):
        with self.lock:
            reservation = self._find_owned(user, reference)
            restaurant = self.restaurants[reservation.restaurant_id]
            if reservation.status == "cancelled":
                return reservation, restaurant
            self._check_cutoff(reservation)
            reservation.status = "cancelled"
            reservation.revision += 1
            now = dt.datetime.now(dt.timezone.utc)
            reservation.history.append(_history_entry(
                len(reservation.history) + 1, now, "cancelled", [],
                reservation.revision, reservation.accepted_terms, restaurant.zone()))
            if reservation.series_id:
                series = self.series.get(reservation.series_id)
                if series is not None:
                    series.revision += 1
            return reservation, restaurant

    @staticmethod
    def _is_noop(reservation: Reservation, raw_table_ids: list, raw_starts_at_local: str,
                 raw_party_size: int) -> bool:
        return (set(raw_table_ids) == set(reservation.table_ids)
                and raw_starts_at_local == reservation.starts_at_local
                and raw_party_size == reservation.party_size)

    def _apply_amendment(self, reservation: Reservation, restaurant: Restaurant, resolved_ids: list,
                          start_utc: dt.datetime, end_utc: dt.datetime, local_str: str,
                          party_size: int, policy: Policy, now: dt.datetime) -> None:
        table_change = _table_change(reservation.table_ids, resolved_ids)
        starts_change = None
        if local_str != reservation.starts_at_local:
            starts_change = {"field": "starts_at_local", "from": reservation.starts_at_local, "to": local_str}
        party_change = None
        if party_size != reservation.party_size:
            party_change = {"field": "party_size", "from": reservation.party_size, "to": party_size}
        changes_list = [c for c in (table_change, starts_change, party_change) if c is not None]

        reservation.table_ids = resolved_ids
        reservation.starts_at_local = local_str
        reservation.starts_at = start_utc
        reservation.ends_at = end_utc
        reservation.party_size = party_size
        reservation.accepted_terms = policy.terms_json()
        reservation.revision += 1
        reservation.history.append(_history_entry(
            len(reservation.history) + 1, now, "changed", changes_list,
            reservation.revision, reservation.accepted_terms, restaurant.zone()))
        if reservation.series_id:
            series = self.series.get(reservation.series_id)
            if series is not None:
                for occ in series.occurrences:
                    if occ["reservation_id"] == reservation.id:
                        occ["exception"] = True
                        break

    def amend_reservation(self, user: User, reference: str, raw_fields: dict,
                           expected_revision: int | None = None):
        with self.lock:
            reservation = self._find_owned(user, reference)
            restaurant = self.restaurants[reservation.restaurant_id]
            if reservation.status == "cancelled":
                raise errors.reservation_cancelled()
            if expected_revision is not None and expected_revision != reservation.revision:
                raise errors.stale_revision()

            # Only now -- after the stale-revision gate -- validate the resulting fields
            # themselves (stage-3 §Policies: stale_revision precedes cutoff/validation).
            changes = validation.parse_patch_changes(raw_fields)
            raw_table_ids = changes.get("table_ids", reservation.table_ids)
            raw_starts_at_local = changes.get("starts_at_local", reservation.starts_at_local)
            raw_party_size = changes.get("party_size", reservation.party_size)
            if self._is_noop(reservation, raw_table_ids, raw_starts_at_local, raw_party_size):
                return reservation, restaurant

            self._check_cutoff(reservation)
            resolved_ids, start_utc, end_utc, local_str, policy = self._resolve_and_validate(
                restaurant, raw_table_ids, raw_starts_at_local, raw_party_size)
            conflict = self._overlapping(restaurant.id, resolved_ids, start_utc, end_utc,
                                          exclude_ids=frozenset({reservation.id}))
            if conflict is not None:
                raise errors.table_unavailable()

            now = dt.datetime.now(dt.timezone.utc)
            self._apply_amendment(reservation, restaurant, resolved_ids, start_utc, end_utc,
                                   local_str, raw_party_size, policy, now)
            if reservation.series_id:
                series = self.series.get(reservation.series_id)
                if series is not None:
                    series.revision += 1
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
                expected_revision = m.get("expected_revision")
                if expected_revision is not None and expected_revision != reservation.revision:
                    raise errors.stale_revision()
                # Only now -- after the stale-revision gate -- validate this item's fields.
                changes = validation.parse_patch_changes(m.get("raw", {}))
                raw_table_ids = changes.get("table_ids", reservation.table_ids)
                raw_starts_at_local = changes.get("starts_at_local", reservation.starts_at_local)
                raw_party_size = changes.get("party_size", reservation.party_size)
                if self._is_noop(reservation, raw_table_ids, raw_starts_at_local, raw_party_size):
                    planned.append({"reservation": reservation, "noop": True})
                    continue
                self._check_cutoff(reservation)
                resolved_ids, start_utc, end_utc, local_str, policy = self._resolve_and_validate(
                    restaurant, raw_table_ids, raw_starts_at_local, raw_party_size)
                planned.append({
                    "reservation": reservation, "noop": False,
                    "table_ids": resolved_ids, "start_utc": start_utc, "end_utc": end_utc,
                    "local_str": local_str, "party_size": raw_party_size, "policy": policy,
                })

            moved_ids = frozenset(p["reservation"].id for p in planned)
            changed = [p for p in planned if not p["noop"]]
            for i, a in enumerate(changed):
                for j, b in enumerate(changed):
                    if (i != j and not set(a["table_ids"]).isdisjoint(b["table_ids"])
                            and a["start_utc"] < b["end_utc"] and b["start_utc"] < a["end_utc"]):
                        raise errors.table_unavailable()
                if self._overlapping(restaurant.id, a["table_ids"], a["start_utc"], a["end_utc"],
                                      exclude_ids=moved_ids) is not None:
                    raise errors.table_unavailable()

            now = dt.datetime.now(dt.timezone.utc)
            touched_series = set()
            for p in changed:
                reservation = p["reservation"]
                self._apply_amendment(reservation, restaurant, p["table_ids"], p["start_utc"],
                                       p["end_utc"], p["local_str"], p["party_size"], p["policy"], now)
                if reservation.series_id:
                    touched_series.add(reservation.series_id)
            for sid in touched_series:
                self.series[sid].revision += 1
            if changed:
                restaurant.revision += 1

            return [p["reservation"] for p in planned], restaurant

    # -- recurring series -----------------------------------------------

    def create_series(self, user: User, anchor_reference: str, count: int, interval_weeks: int):
        with self.lock:
            anchor = self._find_owned(user, anchor_reference)
            if anchor.status == "cancelled":
                raise errors.reservation_cancelled()
            if anchor.series_id is not None:
                raise errors.already_in_series()
            self._check_cutoff(anchor)
            restaurant = self.restaurants[anchor.restaurant_id]
            anchor_naive = timeutil.parse_local(anchor.starts_at_local)
            hhmm = anchor_naive.strftime("%H:%M")

            planned = []
            for i in range(1, count):
                occ_date = anchor_naive.date() + dt.timedelta(days=i * interval_weeks * 7)
                occ_local = f"{occ_date.isoformat()}T{hhmm}"
                resolved_ids, start_utc, end_utc, local_str, policy = self._resolve_and_validate(
                    restaurant, anchor.table_ids, occ_local, anchor.party_size)
                if self._overlapping(restaurant.id, resolved_ids, start_utc, end_utc) is not None:
                    raise errors.table_unavailable()
                planned.append((resolved_ids, start_utc, end_utc, local_str, policy))

            series_id = auth.new_id("ser")
            anchor.series_id = series_id
            anchor.series_index = 0
            occurrences = [{"index": 0, "reservation_id": anchor.id, "exception": False}]
            for i, (resolved_ids, start_utc, end_utc, local_str, policy) in enumerate(planned, start=1):
                reservation = self._new_reservation(
                    user, restaurant, resolved_ids, start_utc, end_utc, local_str,
                    anchor.party_size, policy)
                reservation.series_id = series_id
                reservation.series_index = i
                occurrences.append({"index": i, "reservation_id": reservation.id, "exception": False})

            series = Series(id=series_id, user_id=user.id, restaurant_id=restaurant.id,
                             interval_weeks=interval_weeks, revision=1, occurrences=occurrences)
            self.series[series_id] = series
            restaurant.revision += 1
            return series

    def get_series(self, viewer: User | None, series_id: str) -> Series:
        with self.lock:
            series = self.series.get(series_id)
            if series is None or viewer is None or series.user_id != viewer.id:
                raise errors.not_found("unknown series")
            return series

    def series_json(self, series: Series, tz: ZoneInfo) -> dict:
        with self.lock:
            occurrences = []
            for occ in series.occurrences:
                reservation = self.reservations[occ["reservation_id"]]
                occurrences.append({
                    "index": occ["index"],
                    "reference": reservation.reference,
                    "exception": occ["exception"],
                    "reservation": reservation.to_json(tz),
                })
            return {
                "series_id": series.id,
                "revision": series.revision,
                "interval_weeks": series.interval_weeks,
                "occurrences": occurrences,
            }

    # -- idempotency ----------------------------------------------------

    def idempotency_lookup(self, user: User, key: str, method: str, path: str):
        with self.lock:
            return self.idempotency.get((user.id, key, method, path))

    def idempotency_store(self, user: User, key: str, method: str, path: str,
                           body, status: int, response) -> None:
        with self.lock:
            self.idempotency[(user.id, key, method, path)] = {
                "body": body, "status": status, "response": response}
