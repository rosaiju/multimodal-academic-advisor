"""Prerequisites sourced from Morgan [COURSE] records on 2026-11-04.

Each expectation below was read from the course record cited in the YAML comment
(preview_course_nopop.php?catoid=28&coid=N). The student records are fictional.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.audit.prereq_graph import passed_courses, unmet_prerequisites
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.loader import load_program
from app.catalog.schema import Program

CATALOG = Path(__file__).resolve().parents[2] / "data" / "catalog" / "morgan_cosc_bs_2026_2028.yaml"

#: code -> (coid, prerequisites as printed, minus any permission alternative)
SOURCED = {
    "ENGL102": (62241, ["ENGL101"]),
    "ENGL112": (62243, ["ENGL111"]),
    "MATH242": (62681, ["MATH241"]),
    "MATH312": (62686, ["MATH241"]),
    "MATH313": (62687, ["MATH312"]),
    "MATH331": (62689, ["MATH242"]),
    "COSC320": (63660, ["COSC220", "COSC281"]),
    "COSC323": (63661, ["COSC238", "MATH312"]),
    "COSC458": (62115, ["COSC220"]),
    "COSC459": (63662, ["COSC220"]),
    "COSC472": (63664, ["COSC112", "MATH312"]),
    "COSC480": (63665, ["COSC220"]),
}

#: Records that print no course prerequisite (none, or permission/standing only).
NO_COURSE_PREREQ = ["ENGL101", "ENGL111", "COSC111", "MATH141", "MATH241", "COSC490", "COSC498"]


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(CATALOG)


def _passed(program: Program, *codes: str, grade: str = "B") -> dict[str, str]:
    record = StudentRecord(
        student_id="fictional-prereq-student",
        completed=[
            CompletedCourse(code=c, term="Fall 2025", grade=grade, credits=Decimal(3))
            for c in codes
        ],
    )
    return passed_courses(program, record.completed)


@pytest.mark.parametrize("code", SOURCED)
def test_prerequisites_match_the_course_record(morgan: Program, code: str) -> None:
    course = morgan.course(code)
    assert course is not None
    assert course.prerequisites == SOURCED[code][1]
    assert course.min_prereq_grade == "C"


@pytest.mark.parametrize("code", NO_COURSE_PREREQ)
def test_courses_without_a_course_prerequisite_carry_none(morgan: Program, code: str) -> None:
    assert morgan.course(code).prerequisites == []


def test_engl102_waits_for_engl101(morgan: Program) -> None:
    """The planner must not put ENGL101 and ENGL102 in the same term."""
    assert unmet_prerequisites(morgan, "ENGL102", _passed(morgan)) == ["ENGL101"]
    assert unmet_prerequisites(morgan, "ENGL102", _passed(morgan, "ENGL101")) == []


def test_math242_waits_for_math241(morgan: Program) -> None:
    assert unmet_prerequisites(morgan, "MATH242", _passed(morgan)) == ["MATH241"]
    assert unmet_prerequisites(morgan, "MATH242", _passed(morgan, "MATH241")) == []


def test_a_d_does_not_unlock_the_next_course(morgan: Program) -> None:
    """Every sourced record says 'grade of C or higher'."""
    assert unmet_prerequisites(morgan, "MATH331", _passed(morgan, "MATH242", grade="D")) == [
        "MATH242"
    ]


def test_cosc459_needs_data_structures(morgan: Program) -> None:
    assert unmet_prerequisites(morgan, "COSC459", _passed(morgan, "COSC111", "COSC112")) == [
        "COSC220"
    ]
    assert unmet_prerequisites(morgan, "COSC459", _passed(morgan, "COSC220")) == []


def test_math141_is_sourced_not_a_stub(morgan: Program) -> None:
    course = morgan.course("MATH141")
    assert course.title == "Precalculus"
    assert course.credits == Decimal(4)


def test_cosc238_is_offered_every_fall_and_spring(morgan: Program) -> None:
    """[COURSE] coid=62088 says FALL/SPRING; an earlier pass flagged it AS NEEDED."""
    assert morgan.course("COSC238").offered_as_needed is False
