"""Account storage.

One JSON file per account, mirroring `app/ingestion/store.py`. Files rather than
a table for the same reason the records are files: a person can open one and see
exactly what the system holds. What they will see is an email, a display name and
an scrypt hash - never a password.

Lookup by email needs a scan of the directory. At the scale of a class project
that is fine, and it avoids a second index file that could fall out of step with
the accounts themselves.

*** NO LLM CODE. ***
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.auth.models import User, normalise_email

logger = logging.getLogger(__name__)

#: Student ids are issued by us and become filenames, so they must not be able to
#: escape the directory. Kept identical in spirit to the record store's guard.
_SAFE_ID = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class UserStoreError(RuntimeError):
    """Raised when an account cannot be read, written, or safely addressed."""


class EmailAlreadyRegistered(UserStoreError):
    """That address already has an account."""


def _validate_id(student_id: str) -> str:
    if not student_id or not set(student_id) <= _SAFE_ID:
        raise UserStoreError(f"unsafe student id {student_id!r}")
    return student_id


class UserStore:
    """Reads and writes accounts in one directory."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def path_for(self, student_id: str) -> Path:
        return self.directory / f"{_validate_id(student_id)}.json"

    def save(self, user: User) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(user.student_id)
        path.write_text(user.model_dump_json(indent=2), encoding="utf-8")
        # The id, never the email, and never anything from the hash.
        logger.info("saved account %s", user.student_id)
        return path

    def get(self, student_id: str) -> User | None:
        """The account with this id, or None. Never raises for a missing file."""
        try:
            path = self.path_for(student_id)
        except UserStoreError:
            # An unsafe id cannot name an account, so it simply has none.
            return None
        if not path.is_file():
            return None
        try:
            return User.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError) as exc:
            raise UserStoreError(f"{path.name}: corrupt account file: {exc}") from exc

    def find_by_email(self, email: str) -> User | None:
        """The account for an address, or None."""
        try:
            wanted = normalise_email(email)
        except ValueError:
            return None
        if not self.directory.is_dir():
            return None
        for path in sorted(self.directory.glob("*.json")):
            try:
                user = User.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, ValueError):
                # One unreadable file must not make every login fail.
                logger.warning("skipping unreadable account file %s", path.name)
                continue
            if user.email == wanted:
                return user
        return None

    def create(self, user: User) -> User:
        """Store a new account, refusing a duplicate address."""
        if self.find_by_email(user.email) is not None:
            raise EmailAlreadyRegistered(f"{user.email} already has an account")
        self.save(user)
        return user
