"""Registration, sign-in, and "who am I".

The student id is ISSUED here and never accepted from the caller. That is the
whole reason these endpoints exist rather than the frontend picking an id: if a
client could name its own record, the first person to claim an id would own every
upload later made under it.

*** NO LLM CODE. ***
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth.dependencies import CurrentUser, user_store
from app.auth.models import InvalidEmail, PublicUser, User, new_student_id, normalise_email
from app.auth.passwords import WeakPassword, hash_password, verify_password
from app.auth.store import EmailAlreadyRegistered, UserStore
from app.auth.throttle import login_throttle
from app.auth.tokens import issue_access_token
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


class RegisterRequest(BaseModel):
    """Note what is absent: there is no student_id field. We issue it."""

    email: str
    password: str = Field(description="At least 10 characters; a passphrase is fine")
    name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user: PublicUser


def _token_for(user: User, settings: Settings) -> TokenResponse:
    return TokenResponse(
        access_token=issue_access_token(
            student_id=user.student_id,
            secret=settings.jwt_signing_secret,
            ttl_minutes=settings.access_token_ttl_minutes,
        ),
        expires_in_minutes=settings.access_token_ttl_minutes,
        user=user.public(),
    )


@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    request: RegisterRequest,
    store: Annotated[UserStore, Depends(user_store)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    """Create an account and sign in.

    A duplicate address is reported plainly. Hiding it behind a generic error is a
    common instinct, but the registration form has to tell someone their address is
    taken or they cannot proceed - and the same fact is already obtainable by
    trying to register. The defence that matters is that this says nothing about
    the password.
    """
    try:
        email = normalise_email(request.email)
    except InvalidEmail as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    try:
        password_hash = hash_password(request.password)
    except WeakPassword as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    user = User(
        student_id=new_student_id(),
        email=email,
        name=(request.name or "").strip() or None,
        password_hash=password_hash,
    )
    try:
        store.create(user)
    except EmailAlreadyRegistered:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="that email address already has an account - sign in instead",
        ) from None

    logger.info("registered account %s", user.student_id)
    return _token_for(user, settings)


def _client_of(http_request: Request) -> str:
    """Who is asking, for throttling purposes.

    `request.client.host` and nothing else. An X-Forwarded-For header would be
    more accurate behind a proxy and is also attacker-controlled - trusting it
    here would let anyone reset their own throttle by inventing an address.
    Deployed behind a real proxy, this needs uvicorn's --proxy-headers and a
    trusted-hosts list, not a header read in application code.
    """
    return http_request.client.host if http_request.client else "unknown"


@router.post("/auth/login", response_model=TokenResponse)
def login(
    request: LoginRequest,
    http_request: Request,
    store: Annotated[UserStore, Depends(user_store)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    """Exchange an email and password for an access token.

    A wrong address and a wrong password give the identical 401. Saying "no such
    account" turns this into a way to find out who has one.

    Repeated failures from one client against one address trigger a cooldown. The
    throttle is applied BEFORE the account is looked up and counts failures
    whether or not the address is registered - otherwise a 429 would itself mean
    "this address exists", reintroducing the oracle by another door.
    """
    _configure_throttle(settings)
    client = _client_of(http_request)
    # Normalised so "A@x.edu" and "a@x.edu" share one counter rather than giving
    # an attacker a fresh allowance per capitalisation.
    try:
        key_email = normalise_email(request.email)
    except InvalidEmail:
        key_email = request.email.strip().lower()

    decision = login_throttle.check(client, key_email)
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"too many failed sign-in attempts. Try again in {decision.retry_after} seconds."
            ),
            headers={"Retry-After": str(decision.retry_after)},
        )

    user = store.find_by_email(request.email)

    # Hash even when the account is missing, against a dummy value. Otherwise an
    # unknown address returns noticeably faster than a known one with a bad
    # password, and the timing difference is itself an account oracle.
    stored_hash = user.password_hash if user is not None else _DUMMY_HASH
    password_ok = verify_password(request.password, stored_hash)

    if user is None or not password_ok:
        after_failure = login_throttle.record_failure(client, key_email)
        if not after_failure.allowed:
            # Report the wait on the attempt that triggered it, not the next one.
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"too many failed sign-in attempts. Try again in "
                    f"{after_failure.retry_after} seconds."
                ),
                headers={"Retry-After": str(after_failure.retry_after)},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="email or password is incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # A correct password is proof the guessing has stopped.
    login_throttle.record_success(client, key_email)
    logger.info("signed in account %s", user.student_id)
    return _token_for(user, settings)


def _configure_throttle(settings: Settings) -> None:
    """Keep the process-wide throttle in step with configuration."""
    login_throttle.max_failures = settings.login_max_failures
    login_throttle.base_cooldown_seconds = settings.login_cooldown_seconds
    login_throttle.max_cooldown_seconds = settings.login_cooldown_max_seconds
    login_throttle.forget_after_seconds = settings.login_forget_after_seconds


@router.get("/auth/me", response_model=PublicUser)
def me(user: CurrentUser) -> PublicUser:
    """The signed-in account. How the frontend learns its own student id."""
    return user.public()


#: A real hash of a value nobody can log in with, so the failure path does the
#: same work as the success path. Computed once at import, not per request.
_DUMMY_HASH = hash_password("not-a-real-password-placeholder")
