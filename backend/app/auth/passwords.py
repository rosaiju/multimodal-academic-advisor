"""Password hashing.

`hashlib.scrypt` rather than a new dependency. scrypt is memory-hard, it is in the
standard library, and one fewer third-party package in an auth path is worth more
than the ergonomics of passlib for a project this size.

A stored hash is self-describing - "scrypt$n$r$p$salt$key" - so the cost
parameters can be raised later without invalidating existing accounts: the
verifier reads them back out of the stored string rather than assuming today's
values.

*** NO LLM CODE. Nothing here is a judgement call a model should make. ***
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

#: Cost parameters. n is the memory/CPU cost; 2**14 with r=8 is roughly 16 MB per
#: hash, which is deliberate - it is what makes an offline guessing attack on a
#: leaked file expensive.
_N = 2**14
_R = 8
_P = 1
_SALT_BYTES = 16
_KEY_LEN = 32

_ALGORITHM = "scrypt"

#: The shortest password this system will store. Length beats composition rules:
#: a long passphrase is stronger and easier to remember than "P@ssw0rd!".
MIN_PASSWORD_LENGTH = 10


class WeakPassword(ValueError):
    """Raised when a password is too short to store."""


def hash_password(password: str) -> str:
    """Hash a password for storage. The salt is generated here, never supplied."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters. "
            "A passphrase of a few words is both stronger and easier to remember "
            "than a short password with punctuation in it."
        )
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_KEY_LEN)
    return f"{_ALGORITHM}${_N}${_R}${_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """True when `password` produced `stored`. False for anything malformed.

    Never raises on a bad stored value. A corrupt hash means "this password does
    not match", not a 500 that tells an attacker something went wrong.
    """
    try:
        algorithm, n, r, p, salt_hex, key_hex = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError, MemoryError):
        return False
    # Constant time: a short-circuiting == leaks how much of the hash matched.
    return hmac.compare_digest(candidate, expected)
