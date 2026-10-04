"""Idempotency-key resolution shared by `POST /reservations` and `POST /reservation-moves`."""
from __future__ import annotations

from typing import Callable

from . import errors, store as store_module


def run(store: "store_module.Store", user, key: str, method: str, path: str,
        body, compute: Callable[[], tuple[int, dict]]) -> tuple[int, dict]:
    """Resolve replay/reuse-conflict/first-use, then run `compute` for a first use.

    Must be called while already holding `store.lock` for the whole request so a
    concurrent identical request cannot slip in between the lookup and the store.
    """
    with store.lock:
        existing = store.idempotency_lookup(user, key, method, path)
        if existing is not None:
            if existing["body"] == body:
                return 200, existing["response"]
            raise errors.idempotency_key_reuse()
        status, response = compute()
        if status < 400:
            store.idempotency_store(user, key, method, path, body, status, response)
        return status, response
