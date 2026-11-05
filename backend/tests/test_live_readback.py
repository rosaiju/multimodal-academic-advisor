"""Live voice turns: read back a course code before answering when unsure."""

from __future__ import annotations

import pytest

from app.routers import chat as chat_router


@pytest.fixture
def ask_spy(monkeypatch):
    calls: list[str] = []
    real = chat_router.ask

    def spy(facts, question, **kw):
        calls.append(question)
        return real(facts, question, **kw)

    monkeypatch.setattr(chat_router, "ask", spy)
    return calls


def live(client, message, confidence):
    r = client.post(
        "/advisor/chat",
        json={"message": message, "mode": "live", "confidence": confidence, "use_llm": False},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_low_confidence_course_asks_back(client, ask_spy) -> None:
    body = live(client, "can I take COSC 241", 0.4)
    assert body["clarification"]["question"] == "Did you mean COSC 241?"
    assert body["clarification"]["candidates"] == ["COSC 241"]
    assert body["answer"] == "Did you mean COSC 241?"
    assert body["source"] == "readback"
    assert ask_spy == []


def test_unknown_code_asks_back(client, ask_spy) -> None:
    body = live(client, "can I take COSC 999", 0.95)
    assert body["clarification"] is not None
    assert "COSC 999" in body["clarification"]["question"]
    assert ask_spy == []


def test_confident_known_course_answers_with_code_prefix(client, ask_spy) -> None:
    body = live(client, "what does COSC 241 need", 0.95)
    assert body["clarification"] is None
    assert body["answer"].startswith("For COSC 241")
    assert ask_spy == ["what does COSC 241 need"]


def test_text_mode_unchanged(client, ask_spy) -> None:
    r = client.post("/advisor/chat", json={"message": "can I take COSC 999", "use_llm": False})
    body = r.json()
    assert body["clarification"] is None
    assert not body["answer"].startswith("For ")
    assert ask_spy == ["can I take COSC 999"]
