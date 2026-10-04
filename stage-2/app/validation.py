"""Request-body and query-parameter validation shared across endpoints (spec §5)."""
from __future__ import annotations

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
