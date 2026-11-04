"""The semester plan endpoints, at the HTTP boundary a student meets them."""

from __future__ import annotations

from tests.test_audit_api import enrol


class TestSchedule:
    def test_requires_a_signed_in_student(self, client) -> None:
        # Another student's id is rejected before any record is read.
        response = client.post("/students/someone-else/schedule", json={})
        assert response.status_code in (401, 403, 404)

    def test_generates_a_plan_from_the_confirmed_record(self, client) -> None:
        enrol(client)
        response = client.post("/students/{me}/schedule", json={"start_term": "Fall 2026"})
        assert response.status_code == 200
        body = response.json()
        assert body["terms"]
        assert body["provenance"] == "verified"
        assert any("not a graduation date" in c for c in body["caveats"])
        codes = [c["course"]["code"] for t in body["terms"] for c in t["courses"]]
        assert "COSC111" not in codes  # already on the transcript

    def test_no_record_is_a_404(self, client) -> None:
        response = client.post("/students/{me}/schedule", json={"start_term": "Fall 2026"})
        assert response.status_code == 404

    def test_a_bad_term_is_a_422(self, client) -> None:
        enrol(client)
        response = client.post("/students/{me}/schedule", json={"start_term": "later"})
        assert response.status_code == 422

    def test_credit_target_is_bounded(self, client) -> None:
        enrol(client)
        response = client.post("/students/{me}/schedule", json={"credit_target": 99})
        assert response.status_code == 422


class TestValidate:
    def test_rejects_a_course_ahead_of_its_prerequisite(self, client) -> None:
        enrol(client)
        response = client.post(
            "/students/{me}/schedule/validate",
            json={"terms": [{"term": "Fall 2026", "courses": ["COSC354"]}]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is False
        assert body["issues"][0]["severity"] == "error"

    def test_accepts_the_generated_plan(self, client) -> None:
        enrol(client)
        plan = client.post("/students/{me}/schedule", json={"start_term": "Fall 2026"}).json()
        layout = [
            {"term": t["term"], "courses": [c["course"]["code"] for c in t["courses"]]}
            for t in plan["terms"]
        ]
        result = client.post("/students/{me}/schedule/validate", json={"terms": layout}).json()
        assert result["valid"] is True
