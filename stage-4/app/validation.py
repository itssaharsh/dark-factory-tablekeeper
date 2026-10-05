"""Request-body and query-parameter validation shared across endpoints (spec §5)."""
from __future__ import annotations

import datetime as dt
import json
import re

from . import errors

_DIGITS_RE = re.compile(r"^[0-9]+$")


def parse_json_object(raw: bytes) -> dict:
    """Parse a request body that must be a JSON object. 400 malformed_request otherwise."""
    if not raw:
        raise errors.malformed_request("request body is empty")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise errors.malformed_request("request body is not valid JSON") from None
    if not isinstance(value, dict):
        raise errors.malformed_request("request body must be a JSON object")
    return value


def string_field(body: dict, name: str, *, required: bool = True) -> str | None:
    if name not in body or body[name] is None:
        if required:
            raise errors.validation_failed(f"{name} is required")
        return None
    value = body[name]
    if not isinstance(value, str):
        raise errors.malformed_request(f"{name} must be a string")
    return value


def party_size_field(body: dict, name: str = "party_size", *, required: bool = True) -> int | None:
    if name not in body or body[name] is None:
        if required:
            raise errors.validation_failed(f"{name} is required")
        return None
    value = body[name]
    # bool is a subclass of int in Python; the spec explicitly rejects it.
    if isinstance(value, bool) or not isinstance(value, int):
        raise errors.validation_failed(f"{name} must be an integer")
    if value < 1:
        raise errors.validation_failed(f"{name} must be at least 1")
    return value


def table_ids_field(body: dict, *, required: bool) -> list | None:
    """`table_id` (a set of one) or `table_ids`, never both (spec stage-2 §API)."""
    has_single = "table_id" in body and body["table_id"] is not None
    has_multi = "table_ids" in body and body["table_ids"] is not None
    if has_single and has_multi:
        raise errors.validation_failed("send either table_id or table_ids, not both")
    if has_single:
        value = body["table_id"]
        if not isinstance(value, str):
            raise errors.malformed_request("table_id must be a string")
        return [value]
    if has_multi:
        value = body["table_ids"]
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise errors.malformed_request("table_ids must be a list of strings")
        if not value:
            raise errors.validation_failed("table_ids must not be empty")
        if len(set(value)) != len(value):
            raise errors.validation_failed("table_ids must not contain duplicate table ids")
        return value
    if required:
        raise errors.validation_failed("table_id or table_ids is required")
    return None


def idempotency_key(headers) -> str:
    value = headers.get("Idempotency-Key")
    if value is None or value == "":
        raise errors.missing_idempotency_key()
    if len(value) > 255:
        raise errors.validation_failed("Idempotency-Key must be at most 255 characters")
    return value


def query_int(params: dict, name: str, *, required: bool = True) -> int | None:
    values = params.get(name)
    value = values[0] if values else None
    if value is None or value == "":
        if required:
            raise errors.validation_failed(f"{name} is required")
        return None
    if not _DIGITS_RE.match(value):
        raise errors.validation_failed(f"{name} must be plain decimal digits")
    return int(value)


def query_str(params: dict, name: str, *, required: bool = True) -> str | None:
    values = params.get(name)
    value = values[0] if values else None
    if value is None or value == "":
        if required:
            raise errors.validation_failed(f"{name} is required")
        return None
    return value


def explain_query(params: dict) -> bool:
    """`explain=true` is the only accepted value; anything else present, including the empty
    string, is 422 (stage-3 §availability). Absence of the parameter means "not requested"."""
    if "explain" not in params:
        return False
    values = params.get("explain")
    value = values[0] if values else ""
    if value != "true":
        raise errors.validation_failed("explain must be true if present")
    return True


def expected_revision_field(body: dict, *, name: str = "expected_revision") -> int | None:
    if name not in body or body[name] is None:
        return None
    value = body[name]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise errors.validation_failed(f"{name} must be a positive integer")
    return value


def parse_patch_changes(raw: dict) -> dict:
    """Validate the optional amendment fields already extracted from a request body.

    Deliberately separate from request parsing so callers (store.amend_reservation,
    store.reservation_moves) can run `expected_revision`'s stale-revision check first and
    only then validate table_id/table_ids/starts_at_local/party_size -- stage-3 §Policies:
    a stale `expected_revision` must win over the resulting fields' own validation.
    """
    out = {}
    if "table_id" in raw or "table_ids" in raw:
        table_ids = table_ids_field(raw, required=False)
        if table_ids is not None:
            out["table_ids"] = table_ids
    if "starts_at_local" in raw:
        out["starts_at_local"] = string_field(raw, "starts_at_local")
    if "party_size" in raw:
        out["party_size"] = party_size_field(raw)
    return out


def bounded_int_field(body: dict, name: str, lo: int, hi: int, *, required: bool = True) -> int | None:
    if name not in body or body[name] is None:
        if required:
            raise errors.validation_failed(f"{name} is required")
        return None
    value = body[name]
    if isinstance(value, bool) or not isinstance(value, int) or not (lo <= value <= hi):
        raise errors.validation_failed(f"{name} must be an integer {lo}..{hi}")
    return value


def required_positive_int_field(body: dict, name: str) -> int:
    """Like `expected_revision_field`, but the field itself is mandatory (stage-4 §series amend)."""
    if name not in body or body[name] is None:
        raise errors.validation_failed(f"{name} is required")
    value = body[name]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise errors.validation_failed(f"{name} must be a positive integer")
    return value


def rfc3339_offset_field(body: dict, name: str) -> str:
    """A string field that must parse as an aware (explicit-offset) ISO 8601 instant."""
    value = string_field(body, name, required=True)
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        raise errors.validation_failed(f"{name} must be RFC 3339 with an explicit offset") from None
    if parsed.tzinfo is None:
        raise errors.validation_failed(f"{name} must carry an explicit UTC offset")
    return value
