"""Read-only catalog endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    # TestClient as a context manager runs lifespan, which loads the catalog.
    with TestClient(app) as c:
        yield c


class TestRequirements:
    def test_returns_every_block(self, client: TestClient) -> None:
        r = client.get("/catalog/demo_cs_bs/requirements")
        assert r.status_code == 200
        body = r.json()
        assert body["program_id"] == "demo_cs_bs"
        assert len(body["blocks"]) == 12

    def test_each_block_carries_plain_english(self, client: TestClient) -> None:
        blocks = client.get("/catalog/demo_cs_bs/requirements").json()["blocks"]
        assert all(b["requirement"].strip() for b in blocks)

    def test_response_is_marked_verified(self, client: TestClient) -> None:
        assert client.get("/catalog/demo_cs_bs/requirements").json()["provenance"] == "verified"

    def test_advisor_discretion_is_surfaced(self, client: TestClient) -> None:
        blocks = client.get("/catalog/demo_cs_bs/requirements").json()["blocks"]
        humanities = next(b for b in blocks if b["block_id"] == "gen_ed_humanities")
        assert humanities["advisor_approval_required"] is True

    def test_unknown_program_is_404(self, client: TestClient) -> None:
        r = client.get("/catalog/not_a_program/requirements")
        assert r.status_code == 404
        assert "unknown program" in r.json()["detail"]


class TestCourses:
    def test_lists_all_courses(self, client: TestClient) -> None:
        assert len(client.get("/catalog/demo_cs_bs/courses").json()) == 23

    def test_subject_filter(self, client: TestClient) -> None:
        courses = client.get("/catalog/demo_cs_bs/courses", params={"subject": "math"}).json()
        assert {c["code"] for c in courses} == {"MATH241", "MATH242", "MATH312"}

    def test_unknown_subject_returns_empty_not_error(self, client: TestClient) -> None:
        r = client.get("/catalog/demo_cs_bs/courses", params={"subject": "ZZZ"})
        assert r.status_code == 200
        assert r.json() == []


class TestPrerequisites:
    def test_chain_and_depth(self, client: TestClient) -> None:
        body = client.get("/catalog/demo_cs_bs/courses/COSC490/prerequisites").json()
        assert body["depth"] == 5
        assert body["tree"]["code"] == "COSC490"

    def test_course_code_is_case_insensitive(self, client: TestClient) -> None:
        assert client.get("/catalog/demo_cs_bs/courses/cosc490/prerequisites").status_code == 200

    def test_unknown_course_is_404(self, client: TestClient) -> None:
        r = client.get("/catalog/demo_cs_bs/courses/NOPE999/prerequisites")
        assert r.status_code == 404


class TestNoRegression:
    """The endpoints that already worked must keep working."""

    def test_health(self, client: TestClient) -> None:
        body = client.get("/health").json()
        assert body["status"] == "ok" and body["catalog_loaded"] is True

    def test_programs(self, client: TestClient) -> None:
        assert client.get("/programs").json()[0]["program_id"] == "demo_cs_bs"
