"""The guidance endpoints, at the HTTP boundary a student meets them."""

from __future__ import annotations

from tests.test_audit_api import enrol


class TestExplain:
    def test_explains_a_block(self, client) -> None:
        enrol(client)
        r = client.get("/students/{me}/audit/explain/major_required_courses")
        assert r.status_code == 200
        body = r.json()
        assert body["provenance"] == "verified"
        assert body["headline"]
        assert body["options"]

    def test_explains_every_block(self, client) -> None:
        enrol(client)
        r = client.get("/students/{me}/audit/explain")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_unknown_block_is_a_404(self, client) -> None:
        enrol(client)
        assert client.get("/students/{me}/audit/explain/nope").status_code == 404

    def test_no_record_is_a_404(self, client) -> None:
        assert client.get("/students/{me}/audit/explain").status_code == 404


class TestCompare:
    def test_compares_courses_for_the_student(self, client) -> None:
        enrol(client)
        r = client.get(
            "/students/{me}/compare", params=[("codes", "COSC220"), ("codes", "COSC354")]
        )
        assert r.status_code == 200
        states = {c["code"]: c["state"] for c in r.json()["courses"]}
        assert states["COSC220"] == "eligible_now"

    def test_one_course_is_a_422(self, client) -> None:
        enrol(client)
        assert (
            client.get("/students/{me}/compare", params=[("codes", "COSC220")]).status_code == 422
        )

    def test_an_unknown_course_is_a_422(self, client) -> None:
        enrol(client)
        r = client.get("/students/{me}/compare", params=[("codes", "COSC220"), ("codes", "ZZZ999")])
        assert r.status_code == 422
        assert "ZZZ999" in r.json()["detail"]


class TestDeadlines:
    def test_personalised_by_earned_credits(self, client) -> None:
        enrol(client)
        r = client.get("/students/{me}/deadlines", params={"today": "2026-11-04"})
        assert r.status_code == 200
        body = r.json()
        assert body["standing"]["id"] == "freshman"
        assert body["not_yet_sourced"]
        assert any(d["is_yours"] for d in body["deadlines"])

    def test_downloads_a_calendar_file(self, client) -> None:
        enrol(client)
        r = client.get("/students/{me}/deadlines.ics", params={"today": "2026-11-04"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/calendar")
        assert "BEGIN:VEVENT" in r.text

    def test_another_students_id_is_refused(self, client) -> None:
        assert client.get("/students/someone-else/deadlines").status_code in (401, 403, 404)
