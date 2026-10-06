"""Voice into the advisor: a spoken question becomes the same answer as a typed one.

Deepgram is SawcyD's scripted fake (test_listen_api.py); everything after it is
real - the WebSocket relay, course-code normalisation, the chat route, the degree
engine and the rephrasing check. The student record is synthetic and lives in
pytest's tmp_path.
"""

from __future__ import annotations

import pytest

from app.advisor.chat import conversations
from app.speech.live import Final, Partial, UtteranceEnd
from tests.test_advisor_workflow import TRANSCRIPT, confirm
from tests.test_listen_api import ScriptedLive, converse, use, voice_on  # noqa: F401

PROGRAM = "morgan_cosc_bs_2026_2028"

#: A Deepgram key is set (to a dummy) and the fake replaces the real connection.
pytestmark = pytest.mark.usefixtures("voice_on")


def enrol(client) -> None:
    response = client.post(
        "/ingest/transcript",
        files={"file": ("synthetic.txt", TRANSCRIPT, "text/plain")},
        data={"program_id": PROGRAM},
    )
    assert response.status_code == 200, response.text
    confirm(client, response.json()["extraction"]["courses"])


def spoken(client, monkeypatch, *phrases: str) -> dict:
    """One voice session that hears `phrases`; returns the final `done` message."""
    script = [Partial(phrases[0][:12]), *(Final(p, 0.97) for p in phrases), UtteranceEnd()]
    use(monkeypatch, ScriptedLive(script))
    messages = converse(client, after_ready=[b"\x00" * 64])
    done = messages[-1]
    assert done["type"] == "done", messages
    return done


def ask(client, message: str) -> dict:
    response = client.post("/advisor/chat", json={"message": message, "use_llm": False})
    assert response.status_code == 200, response.text
    return response.json()


def test_spoken_blocker_question_reaches_the_engine(client, monkeypatch) -> None:
    enrol(client)
    done = spoken(client, monkeypatch, "Can I take computer science three fifty four?")
    assert done["text"] == "Can I take COSC 354?"

    reply = ask(client, done["text"])
    assert "NOT eligible" in reply["answer"]
    assert "COSC220 (in progress now), COSC241" in reply["answer"]


def test_spoken_and_typed_questions_get_the_same_answer(client, monkeypatch) -> None:
    enrol(client)
    done = spoken(client, monkeypatch, "What courses am I taking right now?")
    by_voice = ask(client, done["text"])
    typed = ask(client, "What courses am I taking right now?")
    assert by_voice["intent"] == typed["intent"] == "in_progress"
    assert by_voice["answer"] == typed["answer"]
    assert "COSC220" in by_voice["answer"]


def test_listening_never_asks_the_advisor(client, monkeypatch) -> None:
    """Voice fills the box; only the student pressing Ask sends a question."""
    enrol(client)
    conversations._data.clear()
    spoken(client, monkeypatch, "How many credits am I missing?")
    assert conversations._data == {}


def test_an_unknown_spoken_course_is_not_turned_into_a_code(client, monkeypatch) -> None:
    enrol(client)
    done = spoken(client, monkeypatch, "Can I take computer science nine ninety nine?")
    assert "COSC 999" not in done["text"]
    reply = ask(client, done["text"])
    assert "COSC999" not in reply["answer"]
