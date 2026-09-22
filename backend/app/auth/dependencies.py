"""Who is calling, and what they are allowed to touch.

Two dependencies, and the distinction between them is the whole authorisation
model:

* `current_user` answers "who is this?" and rejects anyone it cannot identify.
* `authorised_student_id` answers "may they touch THIS record?" and is what every
  endpoint carrying a `{student_id}` in its path depends on instead of reading
  the raw path parameter.

An endpoint that takes the path parameter directly is one that forgot to check,
so the parameter is shadowed: `authorised_student_id` returns the id, and the
route signature never names it. `tests/test_auth.py` walks the route table and
fails the build if a student-scoped route is missing the dependency, because the
next endpoint someone adds is the one that will leak.

*** NO LLM CODE. Nothing here is a judgement a model should make. ***
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Path, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.models import User
from app.auth.store import UserStore, UserStoreError
from app.auth.tokens import InvalidToken, student_id_from_token
from app.config import Settings, get_settings

#: auto_error=False so a missing header produces OUR 401 with a WWW-Authenticate
#: challenge and a useful message, rather than the default 403 that tells a
#: student nothing about what to do next.
_bearer = HTTPBearer(auto_error=False, description="Bearer token from POST /auth/login")

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="sign in first: POST /auth/login and send the token as 'Authorization: Bearer <token>'",
    headers={"WWW-Authenticate": "Bearer"},
)


def user_store(settings: Annotated[Settings, Depends(get_settings)]) -> UserStore:
    return UserStore(settings.user_dir)


def user_for_token(token: str, settings: Settings, store: UserStore) -> User | None:
    """The account behind a token, or None for every kind of failure alike.

    Shared by `current_user` and the voice WebSocket, which carries its token in
    a message rather than a header. One function, so both paths accept exactly
    the same tokens.
    """
    try:
        student_id = student_id_from_token(token, secret=settings.jwt_signing_secret)
    except InvalidToken:
        return None
    try:
        return store.get(student_id)
    except UserStoreError:
        # A corrupt account file is our problem, not a hint to hand out.
        return None


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[UserStore, Depends(user_store)],
) -> User:
    """The account behind this request, or 401.

    Every failure - no header, malformed token, expired token, or a token whose
    account has since been deleted - produces the same 401 with the same message.
    Distinguishing them tells an attacker which half of a guess was right.
    """
    if credentials is None or not credentials.credentials:
        raise _UNAUTHENTICATED
    user = user_for_token(credentials.credentials, settings, store)
    if user is None:
        raise _UNAUTHENTICATED
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def authorised_student_id(
    student_id: Annotated[str, Path(description="Must be the signed-in student's own id")],
    user: CurrentUser,
) -> str:
    """The record id this request may act on. 404 when it is not theirs.

    404 rather than 403 on purpose. A 403 confirms that the requested record
    EXISTS, which turns this endpoint into a way to enumerate students; a 404 is
    indistinguishable from asking for a record that was never there. The cost is
    a slightly less informative error for someone who mistyped their own id, and
    that is the right trade for a system holding academic records.
    """
    if student_id != user.student_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no record for student {student_id!r}",
        )
    return student_id


#: Depend on this instead of naming `student_id` in a route signature.
AuthorisedStudentId = Annotated[str, Depends(authorised_student_id)]
