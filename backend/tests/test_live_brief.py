"""Live voice: short spoken answers that still keep every caveat and status."""

from __future__ import annotations

from app.advisor.answers import Intent, detect_intent, deterministic_answer
from app.advisor.chat import ask
from app.advisor.consistency import check_rephrasing
from app.routers import chat as chat_router
from app.speech.speakable import to_speech
from tests.test_advisor import StubProvider, facts  # noqa: F401 - fixture

REMAINING = "What courses do I still need to graduate?"
NEXT = "What should I take next semester?"


# --- the relaxed check: omissions allowed, distortions still rejected ---------


def test_brief_check_allows_leaving_out_details(facts) -> None:  # noqa: F811
    prepared = deterministic_answer(facts, REMAINING)
    short = (
        f"You still need {facts.credits_remaining} credits across a few requirement groups. "
        "The catalog here is still partial, so check with your advisor."
    )
    assert check_rephrasing(prepared, short, facts, question=REMAINING)  # full mode rejects
    assert check_rephrasing(prepared, short, facts, question=REMAINING, brief=True) == []


def test_brief_check_still_rejects_invented_course(facts) -> None:  # noqa: F811
    prepared = deterministic_answer(facts, NEXT)
    bad = "Take COSC 999 next. The catalog is still partial."
    assert any("did not supply" in p for p in check_rephrasing(prepared, bad, facts, brief=True))


def test_brief_check_still_rejects_dropped_partial_caveat(facts) -> None:  # noqa: F811
    prepared = deterministic_answer(facts, REMAINING)
    bad = f"You still need {facts.credits_remaining} credits."
    assert any("partial" in p for p in check_rephrasing(prepared, bad, facts, brief=True))


def test_brief_check_still_rejects_answering_a_refusal(facts) -> None:  # noqa: F811
    prepared = deterministic_answer(facts, "can I get a parking permit?")
    bad = "Sure, permits are at the front desk. The catalog is still partial."
    assert any("declined" in p for p in check_rephrasing(prepared, bad, facts, brief=True))


def test_brief_check_still_rejects_wrong_status(facts) -> None:  # noqa: F811
    blocked = facts.blocked[0].course.code
    prepared = deterministic_answer(facts, NEXT)
    bad = f"You can take {blocked} now. The catalog is still partial."
    problems = check_rephrasing(prepared, bad, facts, brief=True)
    assert any("blocked course available" in p for p in problems)


# --- ask(live=True) uses the voice prompt and the relaxed check ---------------


def test_live_ask_uses_voice_prompt_and_ships_short_answer(facts) -> None:  # noqa: F811
    short = (
        f"You still need {facts.credits_remaining} credits. "
        "The catalog here is still partial, so check with your advisor."
    )
    stub = StubProvider(reply=short)
    reply = ask(facts, REMAINING, student_id="s", provider=stub, live=True)
    assert reply.source == "engine+llm"
    assert reply.answer == short
    assert "two or three short sentences" in " ".join(stub.calls[0][0].split())


def test_typed_ask_keeps_full_prompt(facts) -> None:  # noqa: F811
    stub = StubProvider(reply="Short.")
    reply = ask(facts, REMAINING, student_id="s", provider=stub)
    assert reply.source == "engine"  # dropped details still rejected in typed chat
    assert "two or three short sentences" not in " ".join(stub.calls[0][0].split())


# --- deterministic brief speech: the fallback when no model answer ships ------


def test_brief_speech_is_short_and_keeps_headline_and_caveat(facts) -> None:  # noqa: F811
    text = deterministic_answer(facts, REMAINING).text
    out = to_speech(text, brief=True)
    assert len(out.split()) <= 70
    assert str(facts.credits_remaining) in out
    assert "partial" in out
    assert "on your screen" in out


def test_brief_speech_leaves_short_answers_alone() -> None:
    assert to_speech("You have 19 credits.", brief=True) == "You have 19 credits."


def test_speak_route_brief_flag(client, monkeypatch) -> None:
    heard: list[str] = []
    monkeypatch.setattr(
        chat_router, "synthesize", lambda t, *, settings=None: heard.append(t) or b"mp3"
    )
    long = "You need:\n" + "\n".join(f"- COSC {n}" for n in range(201, 211))
    client.post("/advisor/speak", json={"text": long, "brief": True})
    client.post("/advisor/speak", json={"text": long})
    assert len(heard[0]) < len(heard[1])


# --- "graduate early / summer" gets its own honest answer ---------------------


def test_summer_question_routes_to_graduate_early() -> None:
    assert detect_intent("Should I take summer classes to graduate early?") is Intent.GRADUATE_EARLY
    assert detect_intent("can I finish early") is Intent.GRADUATE_EARLY


def test_graduate_early_answer_is_honest(facts) -> None:  # noqa: F811
    a = deterministic_answer(facts, "Should I take summer classes to graduate early?")
    assert a.intent is Intent.GRADUATE_EARLY
    assert a.grounded is False  # it declines to plan summer terms
    assert str(facts.credits_remaining) in a.text
    assert "summer" in a.text.lower() and "advisor" in a.text.lower()


def test_brief_keeps_block_names_with_dashes() -> None:
    text = (
        "You need:\n- General Education - Critical Thinking: 1 course(s)\n"
        "- COSC220 Data (4 cr) - required by 1 unfinished requirement"
    )
    out = to_speech(text, brief=True)
    assert "Critical Thinking" in out
    assert "required by" not in out
