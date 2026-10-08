"""What-if and unlock questions through the advisor chat route."""

from __future__ import annotations

import pytest

from app.advisor.answers import Intent, detect_intent
from app.config import get_settings
from tests.test_advisor_workflow import PROGRAM, TRANSCRIPT, ask, confirm


@pytest.fixture
def enrolled(client):
    upload = client.post(
        "/ingest/transcript",
        files={"file": ("synthetic.txt", TRANSCRIPT, "text/plain")},
        data={"program_id": PROGRAM},
    )
    confirm(client, upload.json()["extraction"]["courses"])


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("What if I take COSC 220 next semester?", Intent.WHAT_IF),
        ("What if I take COSC 220 and COSC 281 together?", Intent.WHAT_IF),
        ("If I complete COSC 241, what becomes available?", Intent.WHAT_IF),
        ("What would my plan look like if I passed these courses?", Intent.WHAT_IF),
        ("What does COSC 241 unlock?", Intent.UNLOCKS),
        ("What can I take after COSC 220?", Intent.UNLOCKS),
        ("What courses eventually depend on COSC 241?", Intent.UNLOCKS),
        ("What do I need before COSC 241?", Intent.UNLOCKS),
        # unchanged routing
        ("What should I take next semester?", Intent.NEXT_SEMESTER),
        ("What do I need to graduate?", Intent.REMAINING_REQUIREMENTS),
        ("Tell me about COSC 220", Intent.COURSE_INFO),
    ],
)
def test_intent_routing(question, intent) -> None:
    assert detect_intent(question) is intent


def test_what_if_answers_from_the_engine_and_changes_nothing(client, enrolled) -> None:
    record_dir = get_settings().student_record_dir
    before = {p.name: p.read_bytes() for p in record_dir.glob("*.json")}
    reply = ask(client, "What if I take COSC 281 and COSC 241?")
    assert reply["intent"] == "what_if" and reply["source"] == "engine"
    assert "real record is unchanged" in reply["answer"]
    assert "COSC281" in reply["answer"] and "COSC241" in reply["answer"]
    assert {p.name: p.read_bytes() for p in record_dir.glob("*.json")} == before


def _facts_for(client):
    from app.advisor.facts import build_facts
    from app.catalog.registry import registry
    from app.ingestion.store import RecordStore

    stored = RecordStore(get_settings().student_record_dir).load(client.student_id)
    return build_facts(registry.get(PROGRAM), stored.to_student_record())


def test_what_if_can_be_phrased_by_a_model_but_only_faithfully(client, enrolled) -> None:
    from app.advisor.answers import deterministic_answer
    from app.advisor.chat import ask as advise
    from tests.test_advisor import StubProvider

    facts = _facts_for(client)
    question = "What if I take COSC 281?"
    prepared = deterministic_answer(facts, question).text

    ok = advise(facts, question, student_id="a", provider=StubProvider(prepared))
    assert ok.source == "engine+llm"

    altered = prepared.replace("to 18.00", "to 19.00")
    assert altered != prepared
    bad = advise(facts, question, student_id="b", provider=StubProvider(altered))
    assert bad.source == "engine" and bad.discarded == altered


def test_what_if_reports_unknown_and_unmet(client, enrolled) -> None:
    answer = ask(client, "What if I take COSC 999 and COSC 354?")["answer"]
    assert "COSC354" in answer and "Not simulated" in answer
    assert "COSC220" in answer  # the unmet prerequisite is named


def test_what_if_without_courses_asks_which(client, enrolled) -> None:
    reply = ask(client, "What would my plan look like if I passed these courses?")
    assert reply["intent"] == "what_if" and reply["grounded"] is False
    assert "which courses" in reply["answer"]


def test_unlocks_answer(client, enrolled) -> None:
    reply = ask(client, "What does COSC 112 unlock?")
    assert reply["intent"] == "unlocks"
    assert "COSC220" in reply["answer"] and "Prerequisites: COSC111 (completed)" in reply["answer"]


def test_unlocks_unknown_course_is_not_invented(client, enrolled) -> None:
    reply = ask(client, "What does COSC 999 unlock?")
    assert reply["grounded"] is False and "not in the encoded catalog" in reply["answer"]
