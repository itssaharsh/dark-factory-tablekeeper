"""The shared error shape (spec §5) and the exception used to raise it."""
from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message or code.replace("_", " ")

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


def malformed_request(message: str = "the request body could not be parsed") -> ApiError:
    return ApiError(400, "malformed_request", message)


def missing_idempotency_key() -> ApiError:
    return ApiError(400, "missing_idempotency_key", "Idempotency-Key header is required")


def unauthenticated(message: str = "missing or unknown bearer token") -> ApiError:
    return ApiError(401, "unauthenticated", message)


def forbidden(message: str = "not permitted to touch this resource") -> ApiError:
    return ApiError(403, "forbidden", message)


def not_found(message: str = "no such resource") -> ApiError:
    return ApiError(404, "not_found", message)


def idempotency_key_reuse() -> ApiError:
    return ApiError(409, "idempotency_key_reuse", "idempotency key already used with a different body")


def validation_failed(message: str = "a required field or value is invalid") -> ApiError:
    return ApiError(422, "validation_failed", message)


def table_unavailable() -> ApiError:
    return ApiError(409, "table_unavailable", "the table is already booked for an overlapping time")


def not_on_slot_grid() -> ApiError:
    return ApiError(422, "not_on_slot_grid", "starts_at_local is not on the booking slot grid")


def outside_opening_hours() -> ApiError:
    return ApiError(422, "outside_opening_hours", "the reservation falls outside opening hours")


def party_exceeds_capacity() -> ApiError:
    return ApiError(422, "party_exceeds_capacity", "party_size exceeds the table's capacity")


def invalid_local_time() -> ApiError:
    return ApiError(422, "invalid_local_time", "this local time does not exist at the restaurant")


def cutoff_passed() -> ApiError:
    return ApiError(409, "cutoff_passed", "the cancellation cutoff has passed")


def reservation_cancelled() -> ApiError:
    return ApiError(409, "reservation_cancelled", "the reservation is already cancelled")


def email_taken() -> ApiError:
    return ApiError(409, "email_taken", "this email is already registered")


def combination_not_allowed() -> ApiError:
    return ApiError(422, "combination_not_allowed", "that combination of tables is not allowed")


def stale_revision() -> ApiError:
    return ApiError(409, "stale_revision", "expected_revision does not match the current revision")


def already_in_series() -> ApiError:
    return ApiError(409, "already_in_series", "this reservation is already part of a series")
