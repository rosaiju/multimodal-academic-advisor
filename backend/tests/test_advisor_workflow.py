"""Upload -> review -> confirm -> audit -> advisor, through the HTTP API.

One synthetic student, one in-progress course, and the provider in each state the
advisor has to survive. Every account and record lives in a pytest tmp_path (see
the `client` fixture); nothing touches backend/accounts or backend/student_records.
"""

from __future__ import annotations

import pytest

import app.advisor.chat as advisor_chat
from app.config import get_settings

PROGRAM = "morgan_cosc_bs_2026_2028"

#: Synthetic. COSC 220 is in progress, so COSC 352 and 354 are blocked.
TRANSCRIPT = (
    b"Fall 2025\n"
    b"COSC 111  Introduction to Computer Science I   4.00  A\n"
    b"ENGL 101  Composition I                       3.00  A\n"
    b"Spring 2026\n"
    b"COSC 112  Introduction to Computer Science II  4.00  B\n"
    b"MATH 141  Precalculus                         4.00  B\n"
    b"Fall 2026\n"
    b"COSC 220  Data Structures and Algorithms      4.00  IP\n"
)


def ask(client, message: str, **extra) -> dict:
    response = client.post("/advisor/chat", json={"message": message, **extra})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def uploaded(client):
    response = client.post(
        "/ingest/transcript",
        files={"file": ("synthetic.txt", TRANSCRIPT, "text/plain")},
        data={"program_id": PROGRAM},
    )
    assert response.status_code == 200, response.text
    return response.json()["extraction"]["courses"]


def confirm(client, rows) -> None:
    response = client.post(
        "/ingest/confirm",
        json={
            "program_id": PROGRAM,
            "source_name": "synthetic.txt",
            "extractor": "text-parser",
            "courses": [{"extracted": r} for r in rows],
        },
    )
    assert response.status_code == 200, response.text


class TestWorkflow:
    def test_nothing_counts_until_the_student_confirms(self, client, uploaded) -> None:
        assert {r["code"] for r in uploaded} >= {"COSC111", "COSC220"}
        assert ask(client, "What am I taking right now?")["intent"] == "no_record"

    def test_confirmed_record_flows_through_audit_and_advisor(self, client, uploaded) -> None:
        confirm(client, uploaded)

        audit = client.get(f"/students/{client.student_id}/audit").json()
        assert float(audit["total_credits_earned"]) == 15
        assert float(audit["total_credits_in_progress"]) == 4
        assert audit["percent_complete"] is None, "partial catalog: no completion percent"

        credits = ask(client, "How many credits am I missing?")
        assert "15.00 credits of the 120" in credits["answer"]
        assert "catalog is still partial" in credits["answer"]

        now = ask(client, "What am I taking right now?")
        assert now["intent"] == "in_progress"
        assert "COSC220" in now["answer"] and "not counted yet" in now["answer"]

        blocker = ask(client, "Can I take COSC 354?")
        assert "NOT eligible" in blocker["answer"]
        assert "COSC220 (in progress now), COSC241" in blocker["answer"]

        nxt = ask(client, "What should I take next semester?")
        assert (
            "COSC220" not in nxt["answer"].split("Not yet eligible")[0]
        ), "an in-progress course must not be recommended"

        off_topic = ask(client, "What is the parking policy?")
        assert "I will not guess" in off_topic["answer"]

    def test_another_student_cannot_see_the_record(self, client, uploaded, other_student) -> None:
        confirm(client, uploaded)
        response = client.post(
            "/advisor/chat",
            json={"message": "What am I taking right now?"},
            headers=other_student["headers"],
        )
        assert response.json()["intent"] == "no_record"
        assert "COSC220" not in response.json()["answer"]
        forbidden = client.get(
            f"/students/{client.student_id}/audit", headers=other_student["headers"]
        )
        assert forbidden.status_code in (403, 404)


class TestProviderStatesThroughTheApi:
    @pytest.fixture
    def enrolled(self, client, uploaded):
        confirm(client, uploaded)
        return client

    def test_unreachable_ollama_answers_from_the_engine(self, enrolled, monkeypatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:1")
        get_settings.cache_clear()
        body = ask(enrolled, "How many credits am I missing?")
        assert body["source"] == "engine"
        assert body["provider"] == "ollama"
        assert "not available" in body["notice"]
        assert "105" in body["answer"]

    def test_inconsistent_model_reply_is_never_shown(self, enrolled, monkeypatch) -> None:
        bad = "You need 105 more credits. That's 12.5% of the total credits required."

        class Stub:
            provider_id, name = "stub", "stub-model"

            def available(self):
                return True, "stub"

            def complete(self, **_):
                return bad

        monkeypatch.setattr(advisor_chat, "get_chat_provider", lambda: Stub())
        body = ask(enrolled, "How many credits am I missing?")
        assert body["source"] == "engine"
        assert bad not in body["answer"]
        assert "did not match the degree engine" in body["notice"]
        assert "discarded" not in body or body.get("discarded") is None

    def test_use_llm_false_never_calls_the_provider(self, enrolled, monkeypatch) -> None:
        def explode():
            raise AssertionError("provider must not be built when use_llm is false")

        monkeypatch.setattr(advisor_chat, "get_chat_provider", explode)
        body = ask(enrolled, "How many credits am I missing?", use_llm=False)
        assert body["source"] == "engine"
