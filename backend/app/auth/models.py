"""What an account is.

A `student_id` is issued by this system, never chosen by the person registering.
If a student could pick their own, the first person to register could claim any
id they liked and every later upload under that id would land in their record.
The id is therefore opaque and random, and the email address is the thing a human
types.

*** This module is data only. ***
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


def normalise_email(email: str) -> str:
    """Trim and lowercase, so " A.Student@Example.edu " and the same address typed
    in lowercase are one account rather than two.

    Deliberately NOT full RFC validation. The address is a login handle here, not
    something we deliver mail to, and an over-strict pattern rejects real addresses
    - which is a worse failure than accepting an odd one. The only structural
    requirement is a single @ with something either side.
    """
    cleaned = email.strip().lower()
    local, sep, domain = cleaned.partition("@")
    if not sep or not local or not domain or "@" in domain:
        raise InvalidEmail(f"{email!r} does not look like an email address")
    return cleaned


class InvalidEmail(ValueError):
    """Raised when an address is not shaped like one."""


def new_student_id() -> str:
    """An opaque, unguessable record id.

    Unguessable matters even though every endpoint checks ownership: it means a
    student id appearing in a log, a URL or a screenshare is not a handle anyone
    can do anything with.
    """
    return secrets.token_hex(16)


class User(BaseModel):
    """One account, as stored. The password hash never leaves this layer."""

    model_config = ConfigDict(frozen=True)

    student_id: str = Field(description="Issued by us; the key for every record lookup")
    email: str = Field(description="Normalised by `normalise_email`; the sign-in handle")
    name: str | None = Field(default=None, description="Display name, if given")
    password_hash: str = Field(description="scrypt; see app/auth/passwords.py")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def public(self) -> PublicUser:
        """The view safe to send to a browser."""
        return PublicUser(student_id=self.student_id, email=self.email, name=self.name)


class PublicUser(BaseModel):
    """An account as the API reports it. Deliberately has no password field.

    A separate type rather than a response_model_exclude on User: excluding by
    name is one typo away from leaking a hash, and a type that cannot hold one
    cannot leak one.
    """

    model_config = ConfigDict(frozen=True)

    student_id: str
    email: str
    name: str | None = None
