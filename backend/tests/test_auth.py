"""Authentication and authorisation.

The audit endpoints shipped with no auth at all: any caller could read any
student's record by guessing an id. These tests pin the two properties that fixed
it, and the guard at the bottom is the one that keeps it fixed - it walks the
route table and fails if a student-scoped endpoint was added without an ownership
check, because the next endpoint someone writes is the one that will leak.

Every credential here is invented and lives in a tmp_path that is deleted when
the test ends.
"""

from __future__ import annotations

import pytest

from app.auth.passwords import MIN_PASSWORD_LENGTH, hash_password, verify_password
from app.auth.tokens import InvalidToken, issue_access_token, student_id_from_token
from tests.conftest import TEST_PASSWORD, register

#: Every endpoint that reads or writes one student's data.
STUDENT_PATHS = [
    "/students/{sid}/record",
    "/students/{sid}/audit",
    "/students/{sid}/audit/summary",
    "/students/{sid}/audit/compare",
    "/students/{sid}/plan",
]


class TestUnauthenticatedAccessIsRefused:
    """The original hole: no token, full access."""

    @pytest.mark.parametrize("path", STUDENT_PATHS)
    def test_every_student_endpoint_needs_a_token(self, anon_client, path) -> None:
        response = anon_client.get(path.format(sid="whatever"))
        assert response.status_code == 401, path

    def test_deleting_a_record_needs_a_token(self, anon_client) -> None:
        assert anon_client.delete("/students/whatever/record").status_code == 401

    def test_confirming_coursework_needs_a_token(self, anon_client) -> None:
        response = anon_client.post(
            "/ingest/confirm",
            json={"source_name": "t.txt", "extractor": "text-parser", "courses": []},
        )
        assert response.status_code == 401

    def test_uploading_a_transcript_needs_a_token(self, anon_client) -> None:
        """Nothing is stored, but parsing an uploaded PDF is real work to hand out."""
        response = anon_client.post(
            "/ingest/transcript", files={"file": ("t.txt", b"Fall 2024\n", "text/plain")}
        )
        assert response.status_code == 401

    def test_the_401_says_how_to_authenticate(self, anon_client) -> None:
        response = anon_client.get("/students/abc/audit")
        assert response.headers.get("WWW-Authenticate") == "Bearer"
        assert "/auth/login" in response.json()["detail"]

    def test_public_endpoints_stay_public(self, anon_client) -> None:
        """Auth must not wall off the catalog - it holds no student data."""
        assert anon_client.get("/health").status_code == 200
        assert anon_client.get("/programs").status_code == 200

    def test_a_forged_token_is_refused(self, anon_client) -> None:
        assert (
            anon_client.get("/auth/me", headers={"Authorization": "Bearer nonsense"}).status_code
            == 401
        )

    def test_a_token_signed_with_another_secret_is_refused(self, anon_client) -> None:
        """The check that makes the secret worth keeping secret."""
        forged = issue_access_token(
            student_id="abc", secret="a-different-secret-also-long-enough-x", ttl_minutes=60
        )
        response = anon_client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})
        assert response.status_code == 401

    def test_a_token_for_a_deleted_account_is_refused(self, client, tmp_path) -> None:
        """The account file is the source of truth, not the token's say-so."""
        (tmp_path / "accounts" / f"{client.student_id}.json").unlink()
        assert client.get("/auth/me").status_code == 401


class TestOneStudentCannotReachAnother:
    """The property that matters most: a valid token is not a skeleton key."""

    @pytest.mark.parametrize("path", STUDENT_PATHS)
    def test_another_students_data_is_not_readable(self, client, other_student, path) -> None:
        response = client.get(path.format(sid=other_student["student_id"]))
        assert response.status_code == 404, path

    def test_another_students_record_cannot_be_deleted(self, client, other_student) -> None:
        assert client.delete(f"/students/{other_student['student_id']}/record").status_code == 404

    def test_the_refusal_does_not_reveal_that_the_record_exists(
        self, client, other_student, anon_client
    ) -> None:
        """404, not 403.

        A 403 would confirm the id belongs to somebody, turning these endpoints
        into a way to enumerate students. An existing record and an imaginary one
        must be indistinguishable to anyone but their owner.
        """
        # Give the other student an actual record to find.
        rows = client.post(
            "/ingest/transcript",
            files={"file": ("t.txt", b"Fall 2024\nCOSC 111 Intro 4.00 A\n", "text/plain")},
            data={"program_id": "morgan_cosc_bs_2026_2028"},
        ).json()["extraction"]["courses"]
        anon_client.post(
            "/ingest/confirm",
            headers=other_student["headers"],
            json={
                "program_id": "morgan_cosc_bs_2026_2028",
                "source_name": "t.txt",
                "extractor": "text-parser",
                "courses": [{"extracted": rows[0]}],
            },
        )

        real_id = other_student["student_id"]
        fake_id = "0000000000000000000000000000dead"
        real = client.get(f"/students/{real_id}/record")
        imaginary = client.get(f"/students/{fake_id}/record")

        assert real.status_code == imaginary.status_code == 404
        # The message echoes the id the CALLER supplied, which they already know.
        # What must not differ is anything else about it.
        assert real.json()["detail"].replace(real_id, "ID") == imaginary.json()["detail"].replace(
            fake_id, "ID"
        )

    def test_each_account_sees_only_its_own_coursework(self, client, other_student, anon_client):
        """Two students, two records, no bleed."""
        rows = client.post(
            "/ingest/transcript",
            files={"file": ("t.txt", b"Fall 2024\nCOSC 111 Intro 4.00 A\n", "text/plain")},
            data={"program_id": "morgan_cosc_bs_2026_2028"},
        ).json()["extraction"]["courses"]
        body = {
            "program_id": "morgan_cosc_bs_2026_2028",
            "source_name": "t.txt",
            "extractor": "text-parser",
            "courses": [{"extracted": rows[0]}],
        }
        client.post("/ingest/confirm", json=body)

        mine = client.get("/students/{me}/record")
        assert mine.status_code == 200
        assert mine.json()["student_id"] == client.student_id

        theirs = anon_client.get(
            f"/students/{other_student['student_id']}/record", headers=other_student["headers"]
        )
        assert theirs.status_code == 404, "the other account confirmed nothing"


class TestRegistrationAndLogin:
    def test_the_server_issues_the_student_id(self, anon_client) -> None:
        """A client that could name its own id could claim someone else's."""
        first = register(anon_client, "one@example.edu")
        second = register(anon_client, "two@example.edu")
        assert first["student_id"] != second["student_id"]
        assert len(first["student_id"]) == 32

    def test_registration_refuses_a_duplicate_address(self, anon_client) -> None:
        register(anon_client, "dup@example.edu")
        again = anon_client.post(
            "/auth/register", json={"email": "dup@example.edu", "password": TEST_PASSWORD}
        )
        assert again.status_code == 409

    def test_a_short_password_is_refused(self, anon_client) -> None:
        response = anon_client.post(
            "/auth/register", json={"email": "weak@example.edu", "password": "short"}
        )
        assert response.status_code == 422
        assert str(MIN_PASSWORD_LENGTH) in response.json()["detail"]

    def test_a_malformed_address_is_refused(self, anon_client) -> None:
        response = anon_client.post(
            "/auth/register", json={"email": "not-an-address", "password": TEST_PASSWORD}
        )
        assert response.status_code == 422

    def test_login_returns_a_working_token(self, anon_client) -> None:
        register(anon_client, "login@example.edu")
        response = anon_client.post(
            "/auth/login", json={"email": "login@example.edu", "password": TEST_PASSWORD}
        )
        assert response.status_code == 200
        token = response.json()["access_token"]
        me = anon_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200

    def test_login_is_case_insensitive_on_the_address(self, anon_client) -> None:
        register(anon_client, "case@example.edu")
        response = anon_client.post(
            "/auth/login", json={"email": "  CASE@Example.EDU ", "password": TEST_PASSWORD}
        )
        assert response.status_code == 200

    def test_a_wrong_password_and_an_unknown_account_are_indistinguishable(
        self, anon_client
    ) -> None:
        """Different messages here would turn login into an account-existence oracle."""
        register(anon_client, "known@example.edu")
        wrong = anon_client.post(
            "/auth/login", json={"email": "known@example.edu", "password": "not-the-password"}
        )
        unknown = anon_client.post(
            "/auth/login", json={"email": "nobody@example.edu", "password": TEST_PASSWORD}
        )
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["detail"] == unknown.json()["detail"]

    def test_no_response_ever_carries_a_password_hash(self, anon_client) -> None:
        registered = anon_client.post(
            "/auth/register", json={"email": "leak@example.edu", "password": TEST_PASSWORD}
        )
        token = registered.json()["access_token"]
        me = anon_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        for body in (registered.text, me.text):
            assert "password" not in body.lower()
            assert "scrypt" not in body


class TestPasswordHashing:
    def test_a_password_is_never_stored_in_the_clear(self, client, tmp_path) -> None:
        stored = (tmp_path / "accounts" / f"{client.student_id}.json").read_text(encoding="utf-8")
        assert TEST_PASSWORD not in stored
        assert "scrypt$" in stored

    def test_the_same_password_hashes_differently_every_time(self) -> None:
        """A per-password salt. Equal hashes would reveal who shares a password."""
        assert hash_password("a passphrase here") != hash_password("a passphrase here")

    def test_verify_accepts_the_right_password_and_rejects_others(self) -> None:
        stored = hash_password("a passphrase here")
        assert verify_password("a passphrase here", stored)
        assert not verify_password("A passphrase here", stored)
        assert not verify_password("", stored)

    def test_a_corrupt_hash_fails_closed(self) -> None:
        """Never raise: a damaged file means "no match", not a 500."""
        for junk in ("", "not-a-hash", "scrypt$bad$8$1$zz$zz", "argon2$x$y"):
            assert verify_password("anything at all", junk) is False


class TestTokens:
    #: 32+ bytes: PyJWT warns below that for HS256, per RFC 7518.
    SECRET = "a-test-signing-secret-long-enough-for-hs256"

    def test_round_trip(self) -> None:
        token = issue_access_token(student_id="abc123", secret=self.SECRET, ttl_minutes=60)
        assert student_id_from_token(token, secret=self.SECRET) == "abc123"

    def test_another_secret_cannot_read_it(self) -> None:
        token = issue_access_token(student_id="abc123", secret=self.SECRET, ttl_minutes=60)
        with pytest.raises(InvalidToken):
            student_id_from_token(token, secret="a-different-secret-also-long-enough-x")

    def test_an_expired_token_is_refused(self) -> None:
        token = issue_access_token(student_id="abc123", secret=self.SECRET, ttl_minutes=-1)
        with pytest.raises(InvalidToken):
            student_id_from_token(token, secret=self.SECRET)

    def test_an_unsigned_token_is_refused(self) -> None:
        """The alg:none family of bug. The algorithm is pinned on decode."""
        import jwt

        forged = jwt.encode({"sub": "abc123", "iss": "advisor-ai"}, key="", algorithm="none")
        with pytest.raises(InvalidToken):
            student_id_from_token(forged, secret=self.SECRET)

    def test_garbage_is_refused(self) -> None:
        for junk in ("", "x", "a.b.c"):
            with pytest.raises(InvalidToken):
                student_id_from_token(junk, secret=self.SECRET)


class TestEveryStudentRouteIsGuarded:
    """The guard that outlives us.

    The specific endpoints above can all be protected and the NEXT one still ship
    open. This walks the live route table instead of a list someone has to
    remember to update.
    """

    def _student_routes(self):
        return [r for r in _all_routes() if "{student_id}" in r.path]

    def test_there_are_student_routes_to_check(self, anon_client) -> None:
        """Guards against this whole class passing because it found nothing."""
        assert len(self._student_routes()) >= len(STUDENT_PATHS)

    def test_every_student_scoped_route_checks_ownership(self, anon_client) -> None:
        from app.auth.dependencies import authorised_student_id

        offenders = []
        for route in self._student_routes():
            dependencies = {d.call for d in route.dependant.dependencies}
            # The ownership check arrives as a sub-dependency of the path param.
            found = authorised_student_id in dependencies or any(
                authorised_student_id is p.dependant.call
                for p in route.dependant.dependencies
                if p.dependant
            )
            if not found:
                flat = _flatten(route.dependant)
                found = authorised_student_id in flat
            if not found:
                offenders.append(f"{sorted(route.methods)} {route.path}")
        assert not offenders, (
            "these routes take a {student_id} without checking it belongs to the "
            f"caller: {offenders}"
        )

    def test_writing_endpoints_require_a_user(self, anon_client) -> None:
        from app.auth.dependencies import current_user

        for path in ("/ingest/confirm", "/ingest/transcript"):
            route = next(r for r in _all_routes() if r.path == path)
            assert current_user in _flatten(route.dependant), path


def _all_routes():
    """Every APIRoute in the app, however deeply the routers are nested.

    `app.routes` does not yield APIRoute objects directly in this FastAPI version -
    an included router appears as a wrapper holding its own `routes`. Walking only
    the top level finds nothing, which would make the guard below pass vacuously;
    `test_there_are_student_routes_to_check` exists to catch exactly that.
    """
    from fastapi.routing import APIRoute

    from app.main import app

    found = []
    stack = list(app.routes)
    seen = set()
    while stack:
        route = stack.pop()
        if id(route) in seen:
            continue
        seen.add(id(route))
        if isinstance(route, APIRoute):
            found.append(route)
        # An included router appears as a wrapper; its endpoints hang off the
        # router it wrapped, not off the wrapper itself.
        inner = getattr(route, "original_router", None)
        stack.extend(getattr(inner, "routes", []))
        stack.extend(getattr(route, "routes", []))
    return found


def _flatten(dependant) -> set:
    """Every dependency callable reachable from a route, at any depth."""
    found = set()
    stack = list(dependant.dependencies)
    while stack:
        dep = stack.pop()
        if dep.call is not None:
            found.add(dep.call)
        stack.extend(dep.dependencies)
    return found


class TestOwnershipCannotBeBypassed:
    """Ways an attacker might try to make their id look like someone else's.

    Written after probing the running server: every case here was tried against a
    live instance and refused. They are pinned as tests so a later change to path
    handling, normalisation, or routing cannot quietly reopen one.

    The ownership check is an exact string comparison against the token's subject,
    which is what makes all of these fail - there is no normalisation step for a
    clever input to survive.
    """

    @pytest.fixture
    def victim(self, client, other_student, anon_client):
        """A second account with coursework actually on record."""
        rows = anon_client.post(
            "/ingest/transcript",
            headers=other_student["headers"],
            files={"file": ("t.txt", b"Fall 2024\nCOSC 111 Intro 4.00 A\n", "text/plain")},
            data={"program_id": "morgan_cosc_bs_2026_2028"},
        ).json()["extraction"]["courses"]
        anon_client.post(
            "/ingest/confirm",
            headers=other_student["headers"],
            json={
                "program_id": "morgan_cosc_bs_2026_2028",
                "source_name": "t.txt",
                "extractor": "text-parser",
                "courses": [{"extracted": rows[0]}],
            },
        )
        return other_student

    def test_the_victim_really_has_a_record(self, victim, anon_client) -> None:
        """Otherwise every refusal below would be a 404 for the boring reason."""
        response = anon_client.get(
            f"/students/{victim['student_id']}/record", headers=victim["headers"]
        )
        assert response.status_code == 200
        assert response.json()["confirmed"]

    @pytest.mark.parametrize(
        "mangle",
        [
            pytest.param(lambda sid: sid.upper(), id="uppercase"),
            pytest.param(lambda sid: f"%20{sid}%20", id="percent-encoded-spaces"),
            pytest.param(lambda sid: f"{sid}%00", id="null-byte"),
            pytest.param(lambda sid: f"{sid}.", id="trailing-dot"),
            pytest.param(lambda sid: f"..%2f{sid}", id="encoded-traversal"),
            pytest.param(lambda sid: sid.replace("a", "%61"), id="percent-encoded-chars"),
        ],
    )
    def test_a_mangled_victim_id_still_refuses(self, client, victim, mangle) -> None:
        response = client.get(f"/students/{mangle(victim['student_id'])}/record")
        assert response.status_code in (400, 404, 422), response.text
        assert "COSC111" not in response.text

    def test_a_trailing_slash_does_not_route_around_the_check(self, client, victim) -> None:
        response = client.get(f"/students/{victim['student_id']}/record/", follow_redirects=True)
        assert response.status_code == 404
        assert "COSC111" not in response.text

    def test_a_method_override_header_is_not_honoured(self, client, victim) -> None:
        response = client.delete(
            f"/students/{victim['student_id']}/record",
            headers={"X-HTTP-Method-Override": "GET"},
        )
        assert response.status_code == 404

    def test_another_students_record_survives_an_attempted_delete(
        self, client, victim, anon_client
    ) -> None:
        """The one that would be unrecoverable if it ever regressed."""
        assert client.delete(f"/students/{victim['student_id']}/record").status_code == 404
        still_there = anon_client.get(
            f"/students/{victim['student_id']}/record", headers=victim["headers"]
        )
        assert still_there.status_code == 200
        assert still_there.json()["confirmed"]

    def test_confirming_cannot_be_aimed_at_another_account(self, client, victim) -> None:
        """The body field is ignored; the token decides. Belt and braces."""
        rows = client.post(
            "/ingest/transcript",
            files={"file": ("t.txt", b"Fall 2024\nENGL 101 Composition 3.00 A\n", "text/plain")},
            data={"program_id": "morgan_cosc_bs_2026_2028"},
        ).json()["extraction"]["courses"]
        response = client.post(
            "/ingest/confirm",
            json={
                "student_id": victim["student_id"],
                "program_id": "morgan_cosc_bs_2026_2028",
                "source_name": "attack.txt",
                "extractor": "text-parser",
                "courses": [{"extracted": rows[0]}],
            },
        )
        assert response.status_code == 200
        assert response.json()["student_id"] == client.student_id

    def test_a_swapped_subject_invalidates_the_signature(self, client, victim) -> None:
        """Editing `sub` without the secret breaks the token, which is the point."""
        import base64
        import json as _json

        header, payload, signature = client.headers["Authorization"].split()[1].split(".")

        def _decode(segment: str) -> dict:
            return _json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))

        def _encode(data: dict) -> str:
            return base64.urlsafe_b64encode(_json.dumps(data).encode()).decode().rstrip("=")

        claims = _decode(payload)
        claims["sub"] = victim["student_id"]
        forged = f"{header}.{_encode(claims)}.{signature}"

        response = client.get(
            f"/students/{victim['student_id']}/record",
            headers={"Authorization": f"Bearer {forged}"},
        )
        assert response.status_code == 401

    def test_there_is_no_endpoint_that_lists_students(self, client) -> None:
        """An index would hand over every id at once, whatever the per-id checks do."""
        for path in ("/students", "/students/", "/students/all", "/auth/users"):
            assert client.get(path).status_code in (404, 405), path


class FakeClock:
    """A clock the tests drive by hand.

    Waiting out a real thirty-second cooldown would make this suite unbearable to
    run, and a suite people skip protects nothing.
    """

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestLoginThrottleUnit:
    """The throttle on its own, away from HTTP."""

    @pytest.fixture
    def clock(self):
        return FakeClock()

    @pytest.fixture
    def throttle(self, clock):
        from app.auth.throttle import LoginThrottle

        return LoginThrottle(
            max_failures=3,
            base_cooldown_seconds=30,
            max_cooldown_seconds=120,
            forget_after_seconds=300,
            clock=clock,
        )

    def test_attempts_below_the_limit_are_allowed(self, throttle) -> None:
        for _ in range(2):
            assert throttle.record_failure("1.2.3.4", "a@example.edu").allowed
        assert throttle.check("1.2.3.4", "a@example.edu").allowed

    def test_the_limit_starts_a_cooldown(self, throttle) -> None:
        for _ in range(2):
            throttle.record_failure("1.2.3.4", "a@example.edu")
        decision = throttle.record_failure("1.2.3.4", "a@example.edu")
        assert not decision.allowed
        assert decision.retry_after == 30

    def test_the_cooldown_expires(self, throttle, clock) -> None:
        for _ in range(3):
            throttle.record_failure("1.2.3.4", "a@example.edu")
        assert not throttle.check("1.2.3.4", "a@example.edu").allowed
        clock.advance(31)
        assert throttle.check("1.2.3.4", "a@example.edu").allowed

    def test_each_lockout_lasts_longer_than_the_last(self, throttle, clock) -> None:
        waits = []
        for _ in range(3):
            for _ in range(2):
                throttle.record_failure("1.2.3.4", "a@example.edu")
            waits.append(throttle.record_failure("1.2.3.4", "a@example.edu").retry_after)
            clock.advance(waits[-1] + 1)
        assert waits == [30, 60, 120]

    def test_the_backoff_is_capped_so_it_never_becomes_permanent(self, throttle, clock) -> None:
        """An unbounded doubling is a permanent lockout wearing a disguise."""
        waits = []
        for _ in range(6):
            for _ in range(2):
                throttle.record_failure("1.2.3.4", "a@example.edu")
            waits.append(throttle.record_failure("1.2.3.4", "a@example.edu").retry_after)
            clock.advance(waits[-1] + 1)
        assert max(waits) == 120, "cooldown must stop growing at max_cooldown_seconds"
        assert waits[-1] == 120

    def test_a_correct_password_clears_the_history(self, throttle) -> None:
        for _ in range(2):
            throttle.record_failure("1.2.3.4", "a@example.edu")
        throttle.record_success("1.2.3.4", "a@example.edu")
        # Back to a full allowance, not one attempt from a lockout.
        for _ in range(2):
            assert throttle.record_failure("1.2.3.4", "a@example.edu").allowed

    def test_an_idle_pair_is_forgotten_entirely(self, throttle, clock) -> None:
        """What makes the lockout temporary in the strongest sense."""
        for _ in range(3):
            throttle.record_failure("1.2.3.4", "a@example.edu")
        clock.advance(400)
        assert throttle.check("1.2.3.4", "a@example.edu").allowed
        # And the backoff has reset too, not merely the lock.
        for _ in range(2):
            throttle.record_failure("1.2.3.4", "a@example.edu")
        assert throttle.record_failure("1.2.3.4", "a@example.edu").retry_after == 30

    def test_another_account_from_the_same_machine_is_unaffected(self, throttle) -> None:
        for _ in range(3):
            throttle.record_failure("1.2.3.4", "victim@example.edu")
        assert throttle.check("1.2.3.4", "someone.else@example.edu").allowed

    def test_the_same_account_from_another_machine_is_unaffected(self, throttle) -> None:
        """Keyed on email alone, anyone could lock anyone out on purpose."""
        for _ in range(3):
            throttle.record_failure("1.2.3.4", "victim@example.edu")
        assert throttle.check("5.6.7.8", "victim@example.edu").allowed


class TestLoginThrottleOverHttp:
    """The same behaviour through the endpoint a browser actually calls."""

    EMAIL = "throttled@example.edu"

    @pytest.fixture
    def fake_clock(self, monkeypatch):
        from app.auth.throttle import login_throttle

        clock = FakeClock()
        monkeypatch.setattr(login_throttle, "clock", clock)
        return clock

    def _fail(self, client, email=None, password="definitely-not-the-password"):
        return client.post("/auth/login", json={"email": email or self.EMAIL, "password": password})

    def test_a_correct_password_still_works(self, anon_client) -> None:
        """The thing that must not break."""
        register(anon_client, self.EMAIL)
        response = anon_client.post(
            "/auth/login", json={"email": self.EMAIL, "password": TEST_PASSWORD}
        )
        assert response.status_code == 200
        assert response.json()["access_token"]

    def test_failures_under_the_limit_stay_401(self, anon_client) -> None:
        register(anon_client, self.EMAIL)
        for _ in range(4):
            assert self._fail(anon_client).status_code == 401

    def test_the_fifth_failure_is_throttled(self, anon_client) -> None:
        register(anon_client, self.EMAIL)
        for _ in range(4):
            self._fail(anon_client)
        response = self._fail(anon_client)
        assert response.status_code == 429
        assert "Retry-After" in response.headers
        assert int(response.headers["Retry-After"]) > 0

    def test_the_right_password_is_refused_during_the_cooldown(self, anon_client) -> None:
        """Otherwise the throttle would be trivially bypassed by guessing correctly."""
        register(anon_client, self.EMAIL)
        for _ in range(5):
            self._fail(anon_client)
        blocked = anon_client.post(
            "/auth/login", json={"email": self.EMAIL, "password": TEST_PASSWORD}
        )
        assert blocked.status_code == 429

    def test_sign_in_works_again_after_the_cooldown(self, anon_client, fake_clock) -> None:
        """Recovery. A lockout that never lifts is a broken account."""
        register(anon_client, self.EMAIL)
        for _ in range(5):
            self._fail(anon_client)
        assert self._fail(anon_client).status_code == 429

        fake_clock.advance(31)

        recovered = anon_client.post(
            "/auth/login", json={"email": self.EMAIL, "password": TEST_PASSWORD}
        )
        assert recovered.status_code == 200
        assert recovered.json()["access_token"]

    def test_a_token_from_after_recovery_actually_works(self, anon_client, fake_clock) -> None:
        register(anon_client, self.EMAIL)
        for _ in range(5):
            self._fail(anon_client)
        fake_clock.advance(31)
        token = anon_client.post(
            "/auth/login", json={"email": self.EMAIL, "password": TEST_PASSWORD}
        ).json()["access_token"]
        me = anon_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200

    def test_a_success_resets_the_allowance(self, anon_client, fake_clock) -> None:
        register(anon_client, self.EMAIL)
        for _ in range(4):
            self._fail(anon_client)
        anon_client.post("/auth/login", json={"email": self.EMAIL, "password": TEST_PASSWORD})
        # Four more failures must not trip it, because the counter went back to zero.
        for _ in range(4):
            assert self._fail(anon_client).status_code == 401

    def test_another_account_is_not_locked_out(self, anon_client) -> None:
        register(anon_client, self.EMAIL)
        register(anon_client, "bystander@example.edu")
        for _ in range(5):
            self._fail(anon_client)
        assert self._fail(anon_client).status_code == 429

        bystander = anon_client.post(
            "/auth/login", json={"email": "bystander@example.edu", "password": TEST_PASSWORD}
        )
        assert bystander.status_code == 200

    def test_an_unknown_address_is_throttled_identically(self, anon_client) -> None:
        """Throttling only real accounts would make 429 mean "this address exists"."""
        for _ in range(5):
            self._fail(anon_client, email="never-registered@example.edu")
        response = self._fail(anon_client, email="never-registered@example.edu")
        assert response.status_code == 429

    def test_changing_the_capitalisation_does_not_buy_a_fresh_allowance(self, anon_client) -> None:
        register(anon_client, self.EMAIL)
        for _ in range(5):
            self._fail(anon_client)
        response = self._fail(anon_client, email=self.EMAIL.upper())
        assert response.status_code == 429

    def test_the_message_says_how_long_to_wait(self, anon_client) -> None:
        register(anon_client, self.EMAIL)
        for _ in range(5):
            self._fail(anon_client)
        detail = self._fail(anon_client).json()["detail"]
        assert "seconds" in detail
        assert "too many failed" in detail.lower()

    def test_registration_is_not_blocked_by_a_login_lockout(self, anon_client) -> None:
        """The throttle guards guessing, not the whole auth surface."""
        for _ in range(6):
            self._fail(anon_client, email="brand.new@example.edu")
        response = anon_client.post(
            "/auth/register",
            json={"email": "brand.new@example.edu", "password": TEST_PASSWORD},
        )
        assert response.status_code == 201

    def test_a_locked_out_client_can_still_use_an_existing_token(self, client) -> None:
        """A session already signed in is not collateral damage."""
        for _ in range(6):
            client.post(
                "/auth/login", json={"email": "someone@example.edu", "password": "wrong-password"}
            )
        assert client.get("/auth/me").status_code == 200
