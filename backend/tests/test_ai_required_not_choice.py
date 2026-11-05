"""Courses a block needs ALL of must never read as a pick-one choice.

Seen live on 2026-11-04 with qwen2.5:7b: the engine wrote "Required Courses for
Computer Science Major: 2 course(s), 6 credit(s) still needed / Options: COSC459,
COSC490" and the model shipped "Your options are either COSC459 Database Design or
COSC490 Senior Project". Every number and code survived, so nothing caught it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.advisor.answers import Intent, deterministic_answer
from app.advisor.consistency import check_rephrasing
from app.advisor.facts import build_facts, every_option_required, render_facts
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.loader import load_program
from app.config import get_settings
from app.ingestion.parser import parse_transcript_text
from app.schemas.provenance import Provenance

SAMPLE = (
    Path(__file__).resolve().parents[2] / "data" / "samples" / "advanced_student_transcript.txt"
)


@pytest.fixture(scope="module")
def program():
    return load_program(get_settings().catalog_dir / "morgan_cosc_bs_2026_2028.yaml")


@pytest.fixture(scope="module")
def facts(program):
    extraction = parse_transcript_text(SAMPLE.read_text(encoding="utf-8"), program=program)
    rows = [
        CompletedCourse(
            code=c.code,
            term=c.term,
            grade=c.grade,
            credits=c.credits,
            provenance=Provenance.STUDENT_CONFIRMED,
            institution="Morgan State University",
        )
        for c in extraction.courses
    ]
    return build_facts(program, StudentRecord(student_id="advanced-fictional", completed=rows))


@pytest.fixture(scope="module")
def all_required_block(facts):
    blocks = [b for b in facts.blocks if every_option_required(b)]
    assert blocks, "the advanced sample should leave a block whose options are all required"
    return blocks[0]


def test_helper_distinguishes_all_required_from_a_choice() -> None:
    assert every_option_required(
        {"satisfied": False, "courses_still_needed": 2, "options": ["COSC459", "COSC490"]}
    )
    assert not every_option_required(
        {"satisfied": False, "courses_still_needed": 1, "options": ["HLTH103", "HLTH200"]}
    )
    assert not every_option_required(
        {"satisfied": True, "courses_still_needed": 0, "options": ["COSC459"]}
    )
    assert not every_option_required({"satisfied": False, "courses_still_needed": 1, "options": []})


def test_engine_answer_and_model_facts_say_all_are_needed(facts, all_required_block) -> None:
    prepared = deterministic_answer(facts, "What do I still need to graduate?")
    assert prepared.intent is Intent.REMAINING_REQUIREMENTS
    shown = ", ".join(all_required_block["options"])
    assert f"All of these are needed: {shown}" in prepared.text
    assert f"all required: {shown}" in render_facts(facts)


def test_either_or_rephrasing_is_rejected(facts, all_required_block) -> None:
    a, b = all_required_block["options"][:2]
    prepared = deterministic_answer(facts, "What do I still need to graduate?")
    rephrased = prepared.text.replace(
        f"All of these are needed: {a}, {b}", f"Your options are either {a} or {b}."
    )
    assert rephrased != prepared.text
    problems = check_rephrasing(prepared, rephrased, facts)
    assert any("all required as a choice" in p for p in problems), problems


def test_faithful_rephrasing_still_passes_that_check(facts, all_required_block) -> None:
    prepared = deterministic_answer(facts, "What do I still need to graduate?")
    problems = check_rephrasing(prepared, prepared.text, facts)
    assert not any("all required as a choice" in p for p in problems), problems
