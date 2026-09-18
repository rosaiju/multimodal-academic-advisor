"""The degree audit endpoints, and the full upload -> confirm -> audit loop.

These are the tests that exercise the system as a student would use it. The
guarantees they pin are the same ones the engine enforces, asserted at the HTTP
boundary where a frontend will actually meet them:

* only confirmed coursework counts,
* an incomplete catalog never yields "you can graduate",
* the coverage caveat travels with the headline numbers, always.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

PROGRAM = "morgan_cosc_bs_2026_2028"
DEMO = "demo_cs_bs"

TRANSCRIPT = (
    b"Fall 2024\n"
    b"COSC 111  Introduction to Computer Science I   4.00  A\n"
    b"ENGL 101  Composition I                       3.00  B\n"
    b"ENGL 102  Composition II                      3.00  A\n"
    b"UNIV 101  University First-Year Seminar       1.00  A\n"
    b"Spring 2025\n"
    b"COSC 112  Introduction to Computer Science II  4.00  A\n"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDENT_RECORD_DIR", str(tmp_path))
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def enrol(client, *, student_id="jane", program=PROGRAM, data=TRANSCRIPT, only=None):
    """Upload a transcript and confirm some or all of its rows."""
    upload = client.post(
        "/ingest/transcript",
        files={"file": ("t.txt", data, "text/plain")},
        data={"program_id": program} if program else {},
    )
    rows = upload.json()["extraction"]["courses"]
    if only is not None:
        rows = [r for r in rows if r["code"] in only]
    client.post(
        "/ingest/confirm",
        json={
            "student_id": student_id,
            "program_id": program,
            "source_name": "t.txt",
            "extractor": "text-parser",
            "courses": [{"extracted": r} for r in rows],
        },
    )
    return rows


class TestTheFullLoop:
    def test_upload_confirm_audit(self, client) -> None:
        enrol(client)
        response = client.get("/students/jane/audit")
        assert response.status_code == 200
        body = response.json()
        assert body["program_id"] == PROGRAM
        assert body["catalog_year"] == "2026-2028"
        assert float(body["total_credits_applied"]) == 15.0

    def test_only_confirmed_courses_count(self, client) -> None:
        """Confirm two of five rows; the other three must not appear."""
        enrol(client, only={"COSC111", "ENGL101"})
        body = client.get("/students/jane/audit").json()
        applied = {a["course"]["code"] for b in body["blocks"] for a in b["applied"]}
        assert applied == {"COSC111", "ENGL101"}
        assert float(body["total_credits_applied"]) == 7.0

    def test_audit_reflects_a_correction(self, client) -> None:
        rows = client.post(
            "/ingest/transcript",
            files={"file": ("t.txt", TRANSCRIPT, "text/plain")},
            data={"program_id": PROGRAM},
        ).json()["extraction"]["courses"]
        cosc111 = next(r for r in rows if r["code"] == "COSC111")
        client.post(
            "/ingest/confirm",
            json={
                "student_id": "jane",
                "program_id": PROGRAM,
                "source_name": "t.txt",
                "extractor": "text-parser",
                "courses": [{"extracted": cosc111, "grade": "D"}],
            },
        )
        body = client.get("/students/jane/audit").json()
        # The major block needs C or better, so a corrected D must not apply.
        major = next(b for b in body["blocks"] if b["block_id"] == "major_required_courses")
        assert major["applied"] == []


class TestRefusesToOverreport:
    def test_never_graduation_eligible_on_a_partial_catalog(self, client) -> None:
        enrol(client)
        assert client.get("/students/jane/audit").json()["is_graduation_eligible"] is False

    def test_summary_always_carries_the_coverage_caveat(self, client) -> None:
        enrol(client)
        summary = client.get("/students/jane/audit/summary").json()
        assert "PARTIAL" in summary["coverage"]
        assert "absent, not failed" in summary["coverage"]
        assert summary["graduation_eligible"] is False

    def test_confirming_nothing_leaves_no_record_to_audit(self, client) -> None:
        """Confirming zero rows is rejected, so no record exists and the audit 404s.

        That is the right shape: an empty record would audit as "you have completed
        nothing", which is a different claim from "we have nothing from you".
        """
        enrol(client, only=set())
        assert client.get("/students/jane/audit/summary").status_code == 404

    def test_a_single_confirmed_course_audits_cleanly(self, client) -> None:
        enrol(client, only={"COSC111"})
        summary = client.get("/students/jane/audit/summary").json()
        assert summary["credits_applied"] == "4"
        assert summary["graduation_eligible"] is False


class TestSummary:
    def test_counts_every_block_status(self, client) -> None:
        enrol(client)
        summary = client.get("/students/jane/audit/summary").json()
        total = (
            summary["satisfied"]
            + summary["in_progress"]
            + summary["unmet"]
            + summary["needs_advisor"]
        )
        assert total == 6, "Morgan has six encoded blocks"

    def test_percent_is_against_the_whole_degree(self, client) -> None:
        enrol(client)
        summary = client.get("/students/jane/audit/summary").json()
        assert 0 < summary["percent_complete"] < 20


class TestStrategy:
    def test_defaults_to_optimal(self, client) -> None:
        """Greedy can under-report, and telling a student to retake something they
        have done is the worse error."""
        enrol(client)
        body = client.get("/students/jane/audit").json()
        assert body["strategy"] == "optimal_bipartite"

    def test_greedy_can_be_requested(self, client) -> None:
        enrol(client)
        body = client.get("/students/jane/audit", params={"strategy": "greedy"}).json()
        assert body["strategy"] == "greedy"

    def test_invalid_strategy_is_rejected(self, client) -> None:
        enrol(client)
        response = client.get("/students/jane/audit", params={"strategy": "vibes"})
        assert response.status_code == 422

    def test_compare_endpoint_reports_what_greedy_misses(self, client) -> None:
        enrol(client)
        body = client.get("/students/jane/audit/compare").json()
        assert set(body["only_with_optimal"]) == set(body["optimal_satisfied"]) - set(
            body["greedy_satisfied"]
        )
        # Morgan's six blocks share no course, so the two agree here.
        assert body["only_with_optimal"] == []


class TestErrors:
    def test_unknown_student_is_404(self, client) -> None:
        assert client.get("/students/nobody/audit").status_code == 404

    def test_unknown_program_is_404(self, client) -> None:
        enrol(client)
        response = client.get("/students/jane/audit", params={"program_id": "nope"})
        assert response.status_code == 404

    def test_no_program_on_record_and_none_given_is_400(self, client) -> None:
        enrol(client, program=None)
        response = client.get("/students/jane/audit")
        assert response.status_code == 400
        assert "program_id" in response.json()["detail"]

    def test_program_override_is_honoured(self, client) -> None:
        """A student may ask what a different degree would say."""
        enrol(client)
        body = client.get("/students/jane/audit", params={"program_id": DEMO}).json()
        assert body["program_id"] == DEMO

    def test_unsafe_student_id_does_not_escape_the_store(self, client) -> None:
        response = client.get("/students/..%2Fescape/audit")
        assert response.status_code in (400, 404)
