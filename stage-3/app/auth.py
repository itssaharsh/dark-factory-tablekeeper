"""Password hashing (scrypt, stdlib hashlib) and bearer tokens."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets

_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32

# local@domain: non-empty, no '@' or whitespace on either side.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


def is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value))


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N,
                             r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, salt.hex(), digest.hex())


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n),
                                r=int(r), p=int(p), dklen=len(expected))
    return hmac.compare_digest(candidate, expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(10)}"


_REF_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def new_reference(taken: set[str]) -> str:
    while True:
        ref = "".join(secrets.choice(_REF_ALPHABET) for _ in range(8))
        if ref not in taken:
            return ref
