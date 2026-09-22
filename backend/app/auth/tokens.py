"""Signed access tokens.

JWT via PyJWT rather than a hand-rolled format. Signing a JSON blob with HMAC is
easy to write and easy to get subtly wrong - the `alg: none` and HS/RS confusion
families of bug exist because people wrote their own. PyJWT is small and the
algorithm is pinned on both encode and decode.

Stateless on purpose: there is no session table to keep in step with the record
files. The cost of that choice is that a token cannot be revoked before it
expires, which is why the lifetime is hours rather than weeks.

*** NO LLM CODE. ***
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

#: Pinned. Passed to decode as the ONLY acceptable algorithm, which is what shuts
#: out a token that arrives claiming `alg: none` or a different family.
ALGORITHM = "HS256"

#: Distinguishes our tokens from any other HS256 token that might be signed with
#: the same secret elsewhere. Checked on decode.
ISSUER = "advisor-ai"


class InvalidToken(Exception):
    """The token is missing, malformed, expired, or not signed by us.

    One exception for every failure. Telling a caller *which* of those went wrong
    helps an attacker far more than it helps a student.
    """


def issue_access_token(
    *,
    student_id: str,
    secret: str,
    ttl_minutes: int,
    now: datetime | None = None,
) -> str:
    """Sign a token identifying one account."""
    issued = now or datetime.now(UTC)
    payload = {
        # `sub` is the student_id, so every downstream ownership check compares
        # the same value the record store is keyed on.
        "sub": student_id,
        "iss": ISSUER,
        "iat": issued,
        "exp": issued + timedelta(minutes=ttl_minutes),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def student_id_from_token(token: str, *, secret: str) -> str:
    """The account a token identifies. Raises `InvalidToken` for anything else."""
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["sub", "exp", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise InvalidToken("token carries no subject")
    return subject
