"""POST /advisor/simulate and GET /advisor/unlocks/{code}: auth, isolation, read-only."""

from __future__ import annotations

import pytest

from app.config import get_settings
from tests.test_advisor_workflow import TRANSCRIPT, confirm

PROGRAM = "morgan_cosc_bs_2026_2028"


@pytest.fixture
def enrolled(client):
    upload = client.post(
        "/ingest/transcript",
        files={"file": ("synthetic.txt", TRANSCRIPT, "text/plain")},
        data={"program_id": PROGRAM},
    )
    confirm(client, upload.json()["extraction"]["courses"])


def record_files() -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in get_settings().student_record_dir.glob("*.json")}


def test_requires_sign_in(anon_client) -> None:
    assert anon_client.post("/advisor/simulate", json={"courses": ["COSC241"]}).status_code == 401
    assert anon_client.get("/advisor/unlocks/COSC241").status_code == 401


def test_no_record_is_a_clear_404(client) -> None:
    response = client.post("/advisor/simulate", json={"courses": ["COSC241"]})
    assert response.status_code == 404


def test_simulation_leaves_the_stored_record_byte_identical(client, enrolled) -> None:
    before = record_files()
    assert before
    response = client.post("/advisor/simulate", json={"courses": ["COSC241", "COSC281"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["simulated"] is True
    assert {c["code"] for c in body["applied"]} == {"COSC241", "COSC281"}
    assert record_files() == before
    # and the real audit still shows nothing of it
    audit = client.get("/students/{me}/audit").json()
    assert all(
        a["course"]["code"] not in {"COSC241", "COSC281"}
        for b in audit["blocks"]
        for a in b["applied"]
    )


def test_student_id_in_the_body_is_ignored_not_trusted(client, enrolled, other_student) -> None:
    body = {"courses": ["COSC241"], "student_id": other_student["student_id"]}
    assert client.post("/advisor/simulate", json=body).status_code == 200


def test_validation(client, enrolled) -> None:
    assert client.post("/advisor/simulate", json={"courses": []}).status_code == 422
    assert (
        client.post("/advisor/simulate", json={"courses": ["COSC241"], "assumed_grade": "Z"})
    ).status_code == 422
    assert client.post("/advisor/simulate", json={"courses": ["COSC111"] * 13}).status_code == 422


def test_unlocks_endpoint(client, enrolled) -> None:
    response = client.get("/advisor/unlocks/cosc241")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "COSC241" and body["known"] is True
    assert body["prerequisite_tree"]["code"] == "COSC241"
    assert any(e["course"]["code"] == "COSC354" for e in body["direct_unlocks"])


def test_unlocks_unknown_course(client, enrolled) -> None:
    body = client.get("/advisor/unlocks/COSC999").json()
    assert body["known"] is False and body["direct_unlocks"] == []
