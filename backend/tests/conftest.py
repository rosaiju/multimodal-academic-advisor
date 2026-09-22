"""Shared test fixtures.

Every student-scoped endpoint now requires a token, so an API test needs an
account before it can do anything. `client` provides one: a signed-in TestClient
whose record and account directories are throwaway.

Student ids are issued by the server and are random, so a test cannot write
`/students/jane/audit` any more. Paths use the literal placeholder `{me}`, which
`AuthedClient` swaps for the signed-in id. That keeps the tests readable and, more
importantly, keeps them honest: a test that wants ANOTHER student's id has to go
and get one, which is exactly the thing the authorisation tests do.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth.throttle import login_throttle
from app.config import get_settings

#: What a test account signs in with. Not a credential for anything real - it
#: exists only inside a tmp_path that is deleted when the test ends.
TEST_PASSWORD = "correct horse battery staple"


class AuthedClient(TestClient):
    """A TestClient signed in as one account.

    `student_id` is the account's issued id. Any `{me}` in a request path is
    replaced with it.
    """

    student_id: str = ""

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        if isinstance(url, str) and "{me}" in url:
            url = url.replace("{me}", self.student_id)
        return super().request(method, url, *args, **kwargs)


def register(client: TestClient, email: str, password: str = TEST_PASSWORD) -> dict:
    """Create an account and return {'student_id', 'token', 'headers'}."""
    response = client.post("/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    body = response.json()
    token = body["access_token"]
    return {
        "student_id": body["user"]["student_id"],
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


@pytest.fixture(autouse=True)
def _clean_throttle():
    """The login throttle is process-wide state, so tests must not inherit it.

    Autouse: a test that fails only when run after another is the kind of flake
    nobody enjoys tracking down.
    """
    login_throttle.reset()
    yield
    login_throttle.reset()


@pytest.fixture
def anon_client(tmp_path, monkeypatch):
    """A client with NO account signed in. For testing the 401s."""
    monkeypatch.setenv("STUDENT_RECORD_DIR", str(tmp_path / "records"))
    monkeypatch.setenv("USER_DIR", str(tmp_path / "accounts"))
    # A fixed secret so a test never depends on the ephemeral-secret fallback.
    monkeypatch.setenv(
        "JWT_SECRET", "test-only-signing-secret-not-used-anywhere-and-long-enough-for-hs256"
    )
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


@pytest.fixture
def client(anon_client):
    """A signed-in client. `{me}` in a path becomes this account's student id."""
    account = register(anon_client, "student@example.edu")
    authed = AuthedClient(anon_client.app)
    authed.student_id = account["student_id"]
    authed.headers.update(account["headers"])
    with authed:
        yield authed


@pytest.fixture
def other_student(client):
    """A SECOND account, for the tests that try to reach across accounts."""
    return register(client, "someone.else@example.edu")
