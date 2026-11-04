"""Engine answers added for the demo: honest credit totals, follow-up confirmations,
semester plans, comparisons, requirement explanations, "what if I fail", and
support for a student who says they are overwhelmed.

Every answer here is built from the degree engine; no model is involved.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from app.advisor.answers import Intent, detect_intent, deterministic_answer
from app.advisor.consistency import check_rephrasing
from app.advisor.facts import build_facts
from app.audit.record import CompletedCourse, StudentRecord
from app.audit.schedule import generate_schedule
from app.catalog.loader import load_program
from app.config import get_settings
from app.schemas.provenance import Provenance

PROGRAM_FILE = "morgan_cosc_bs_2026_2028.yaml"

COURSES = [
    ("COSC111", "Fall 2025", "A", "4"),
    ("COSC112", "Spring 2026", "B", "4"),
    ("MATH141", "Spring 2026", "B", "4"),
    ("ENGL101", "Fall 2025", "A", "3"),
    ("COSC220", "Fall 2026", "IP", "4"),
]


def _row(code, term, grade, credits):
    return CompletedCourse(
        code=code,
        term=term,
        grade=grade,
        credits=Decimal(credits),
        provenance=Provenance.STUDENT_CONFIRMED,
        institution="Morgan State University",
    )


@pytest.fixture(scope="module")
def program():
    return load_program(get_settings().catalog_dir / PROGRAM_FILE)


@pytest.fixture(scope="module")
def facts(program):
    record = StudentRecord(student_id="ai-small", completed=[_row(*c) for c in COURSES])
    return build_facts(program, record)


@pytest.fixture(scope="module")
def heavy(program):
    """Over the 120-credit total, with major blocks still open: the case where
    'you still need 0' used to read as 'you are done'."""
    rows = [_row(*c) for c in COURSES] + [
        _row(f"HIST{100 + i}", "Fall 2024", "A", "3") for i in range(40)
    ]
    return build_facts(program, StudentRecord(student_id="ai-heavy", completed=rows))


# --------------------------------------------------------------------------- A


def test_credits_below_total_unchanged(facts) -> None:
    text = deterministic_answer(facts, "How many credits am I missing?").text
    assert "you still need 105" in text
    assert "12.5% of the way through" in text


def test_credits_over_total_says_not_done_and_lists_blocks(heavy) -> None:
    answer = deterministic_answer(heavy, "How many credits am I missing?")
    assert answer.intent is Intent.CREDITS
    text = answer.text
    assert heavy.credits_remaining == 0
    assert "still need 0" not in text
    assert "of the way" not in text
    assert "exceeds the 120-credit total" in text
    assert "not done yet" in text
    for block in heavy.blocks:
        if not block["satisfied"]:
            assert block["name"] in text
    assert answer.citations  # the open blocks


def test_progress_over_total_drops_misleading_percent(heavy) -> None:
    text = deterministic_answer(heavy, "How am I doing?").text
    assert "0 to go" not in text
    assert "% by credit" not in text


@pytest.mark.parametrize(
    "question",
    [
        "It says I have earned 138 credits. So I don't think I need anymore. Right?",
        "So I don't need any more classes?",
        "Am I done?",
        "Can I graduate now?",
    ],
)
def test_confirmation_routes_to_requirements(question) -> None:
    assert detect_intent(question) is Intent.REMAINING_REQUIREMENTS


def test_confirmation_answer_opens_with_a_direct_no(heavy) -> None:
    question = "It says I have earned 138 credits. So I don't think I need anymore. Right?"
    text = deterministic_answer(heavy, question).text
    assert text.startswith("No, not yet.")
    assert "cover the 120-credit total" in text


# --------------------------------------------------------------------------- routing


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("Can you help me plan my remaining semesters?", Intent.PLAN_SEMESTERS),
        ("When can I graduate?", Intent.PLAN_SEMESTERS),
        ("Which is better for me, COSC 241 or COSC 243?", Intent.COMPARE),
        ("Compare COSC 241 vs COSC 243", Intent.COMPARE),
        ("Why does this course not count?", Intent.EXPLAIN_REQUIREMENT),
        ("Why is my supporting courses block not satisfied?", Intent.EXPLAIN_REQUIREMENT),
        ("What happens if I fail COSC 220?", Intent.WHAT_IF),
        ("What if I don't pass COSC 220?", Intent.WHAT_IF),
        ("I'm overwhelmed and worried I won't graduate on time", Intent.WELLBEING),
        ("I am so stressed about this semester", Intent.WELLBEING),
        ("I want to kill myself", Intent.CRISIS),
        # Still the old routes.
        ("How many credits am I missing?", Intent.CREDITS),
        ("What should I take next semester?", Intent.NEXT_SEMESTER),
        ("Why is COSC 220 recommended?", Intent.WHY_RECOMMENDED),
        ("What if I take COSC 241?", Intent.WHAT_IF),
    ],
)
def test_new_routes(question, intent) -> None:
    assert detect_intent(question) is intent


def test_worried_about_a_course_alone_is_not_wellbeing() -> None:
    assert detect_intent("I'm worried about COSC 354, can I take it?") is not Intent.WELLBEING


def test_explicit_intent_and_codes_override_keywords(facts) -> None:
    # The understanding layer may choose the route and supply codes from history.
    answer = deterministic_answer(
        facts, "Why does this course not count?", Intent.EXPLAIN_REQUIREMENT, ["COSC220"]
    )
    assert answer.intent is Intent.EXPLAIN_REQUIREMENT
    assert "COSC220 does not count" in answer.text
    # The old two-argument call still works.
    assert deterministic_answer(facts, "How many credits am I missing?").intent is Intent.CREDITS


# --------------------------------------------------------------------------- C


def test_plan_lists_the_planner_terms(facts, program) -> None:
    answer = deterministic_answer(facts, "Can you help me plan my remaining semesters?")
    plan = generate_schedule(program, facts.record)
    assert answer.intent is Intent.PLAN_SEMESTERS
    for term in plan.terms[:8]:
        assert term.term in answer.text
        for course in term.courses:
            assert course.course.code in answer.text
    assert "not a graduation date" in answer.text


def test_compare_uses_engine_facts(facts) -> None:
    answer = deterministic_answer(facts, "Which is better for me, COSC 241 or COSC 243?")
    assert answer.intent is Intent.COMPARE
    assert answer.citations == ["COSC241", "COSC243"]
    assert "ranks nothing" in answer.text
    assert "Where they differ" in answer.text


def test_compare_blocked_course_says_what_it_needs(facts) -> None:
    text = deterministic_answer(facts, "Compare COSC 352 vs COSC 241").text
    assert "COSC352" in text and "not yet eligible - needs COSC220" in text


def test_compare_needs_two_known_courses(facts) -> None:
    answer = deterministic_answer(facts, "Which is better, COSC 241 or COSC 999?")
    assert answer.grounded is False
    assert "COSC999" in answer.text


def test_explain_course_that_does_not_count(facts) -> None:
    text = deterministic_answer(facts, "Why does COSC 220 not count?").text
    assert "COSC220 does not count toward" in text
    assert "In progress" in text


def test_explain_block_by_name(facts) -> None:
    text = deterministic_answer(facts, "Why is my supporting courses block not satisfied?").text
    assert text.startswith("Supporting Courses:")
    assert "What it asks for" in text


def test_fail_what_if_lists_what_stays_blocked(facts) -> None:
    answer = deterministic_answer(facts, "What happens if I fail COSC 220?")
    assert answer.intent is Intent.WHAT_IF
    assert "if you do not pass COSC220" in answer.text
    assert "COSC352" in answer.text and "COSC354" in answer.text
    assert "4 credits are not earned" in answer.text


def test_fail_what_if_on_a_passed_course(facts) -> None:
    text = deterministic_answer(facts, "What if I failed COSC 111?").text
    assert "already shows COSC111 as passed" in text


# --------------------------------------------------------------------------- D


def test_wellbeing_gives_facts_and_people_not_promises(facts) -> None:
    answer = deterministic_answer(facts, "I'm overwhelmed and worried I won't graduate on time")
    text = answer.text
    assert answer.intent is Intent.WELLBEING
    assert "105 still needed" in text
    assert "Required Courses for Computer Science Major" in text
    assert "academic advisor" in text and "counseling center" in text
    assert "cannot promise you a graduation date" in text
    assert "will graduate" not in text.lower()
    # No invented phone numbers, URLs or office names.
    assert "http" not in text and not re.search(r"\d{3}[-. ]\d{3,4}", text)
    assert not re.search(r"\b(depress|disorder|diagnos)", text, re.IGNORECASE)


def test_wellbeing_without_a_record_still_supports() -> None:
    answer = deterministic_answer(None, "I'm so overwhelmed")
    assert answer.intent is Intent.WELLBEING
    assert "counseling center" in answer.text


def test_crisis_reply_is_fixed_and_beats_any_route(facts) -> None:
    for chosen in (None, Intent.CREDITS, Intent.WELLBEING):
        answer = deterministic_answer(facts, "honestly I want to kill myself", chosen)
        assert answer.intent is Intent.CRISIS
        assert "988" in answer.text and "911" in answer.text
    assert deterministic_answer(None, "I want to end my life").intent is Intent.CRISIS


# --------------------------------------------------------------------------- consistency


@pytest.mark.parametrize(
    "question",
    [
        "How many credits am I missing?",
        "It says I have earned 138 credits. So I don't think I need anymore. Right?",
        "Can you help me plan my remaining semesters?",
        "Which is better for me, COSC 241 or COSC 243?",
        "Why does COSC 220 not count?",
        "What happens if I fail COSC 220?",
        "I'm overwhelmed and worried I won't graduate on time",
    ],
)
def test_engine_text_passes_its_own_rephrasing_check(facts, heavy, question) -> None:
    """A model that repeats the engine verbatim must be allowed through; otherwise
    these answers could never be phrased by a model at all."""
    for f in (facts, heavy):
        prepared = deterministic_answer(f, question)
        assert check_rephrasing(prepared, prepared.text, f, question=question) == []
