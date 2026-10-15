"""What-if simulation: engine-only, and never touches the stored record."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.audit.record import CompletedCourse, StudentRecord
from app.audit.simulation import SimulationRequest, canonical_code, simulate
from app.catalog.registry import registry
from app.config import get_settings
from app.schemas.audit import BlockStatus

PROGRAM = "morgan_cosc_bs_2026_2028"


@pytest.fixture(scope="module")
def program():
    if not registry.is_loaded:
        registry.load(get_settings().catalog_dir)
    return registry.get(PROGRAM)


def course(code: str, grade: str = "A", credits: str = "4") -> CompletedCourse:
    return CompletedCourse(code=code, term="Fall 2024", grade=grade, credits=Decimal(credits))


def record(*courses: CompletedCourse) -> StudentRecord:
    return StudentRecord(student_id="s1", completed=list(courses))


BASE = record(course("COSC111"), course("COSC112"), course("MATH141", credits="4"))


def run(program, rec=BASE, *codes, **kw):
    return simulate(program, rec, SimulationRequest(courses=list(codes), **kw))


def test_applies_course_and_adds_its_credits(program) -> None:
    result = run(program, BASE, "cosc 220")
    assert [c.code for c in result.applied] == ["COSC220"]
    assert result.credits_added == Decimal(4)
    assert result.after.credits_earned == result.before.credits_earned + 4
    assert result.simulated is True


def test_stored_record_is_never_mutated(program) -> None:
    snapshot = BASE.model_dump()
    run(program, BASE, "COSC220", "COSC281")
    assert BASE.model_dump() == snapshot
    assert len(BASE.completed) == 3


def test_newly_unlocked_courses(program) -> None:
    result = run(program, BASE, "COSC220")
    unlocked = {c.code for c in result.newly_unlocked}
    assert "COSC352" in unlocked  # needs only COSC220
    assert "COSC354" not in unlocked  # also needs COSC241
    assert "COSC220" not in unlocked  # the hypothetical itself is not "unlocked"


def test_still_blocked_lists_remaining_prerequisites(program) -> None:
    result = run(program, BASE, "COSC220")
    blocked = {b.course.code: b.missing_prerequisites for b in result.still_blocked}
    assert blocked.get("COSC354") == ["COSC241"]


def test_two_courses_together(program) -> None:
    result = run(program, BASE, "COSC220", "COSC281")
    assert {c.code for c in result.applied} == {"COSC220", "COSC281"}
    assert result.credits_added == Decimal(7)


def test_same_term_rejects_a_course_whose_prerequisite_is_in_the_same_term(program) -> None:
    result = run(program, BASE, "COSC220", "COSC352")
    assert [c.code for c in result.applied] == ["COSC220"]
    skipped = result.skipped[0]
    assert skipped.code == "COSC352" and skipped.reason == "missing_prerequisites"
    assert skipped.missing_prerequisites == ["COSC220"]
    assert "same term" in skipped.detail


def test_completed_mode_chains_prerequisites(program) -> None:
    result = run(program, BASE, "COSC352", "COSC220", mode="completed")
    assert {c.code for c in result.applied} == {"COSC220", "COSC352"}
    assert result.skipped == []


def test_unknown_course_is_reported_not_invented(program) -> None:
    result = run(program, BASE, "COSC999", "COSC220")
    assert [(s.code, s.reason) for s in result.skipped] == [("COSC999", "unknown_course")]
    assert [c.code for c in result.applied] == ["COSC220"]


def test_already_completed_and_duplicates_change_nothing(program) -> None:
    result = run(program, BASE, "COSC112", "COSC220", "COSC220")
    reasons = {(s.code, s.reason) for s in result.skipped}
    assert reasons == {("COSC112", "already_completed"), ("COSC220", "duplicate")}
    assert result.credits_added == Decimal(4)


def test_nothing_applicable_means_no_change(program) -> None:
    result = run(program, BASE, "COSC999")
    assert result.applied == [] and result.credits_added == 0
    assert result.newly_unlocked == [] and result.changed_blocks == []


def test_in_progress_course_is_not_double_counted(program) -> None:
    rec = record(*BASE.completed, course("COSC220", grade="IP"))
    result = run(program, rec, "COSC220")
    assert result.after.credits_earned == result.before.credits_earned + 4


def test_failed_prerequisite_grade_is_respected(program) -> None:
    weak = record(course("COSC111"), course("COSC112", grade="D"))
    result = run(program, weak, "COSC220")  # needs a C in COSC112
    assert result.applied == []
    assert result.skipped[0].missing_prerequisites == ["COSC112"]


def test_block_changes_are_reported(program) -> None:
    result = run(program, BASE, "COSC220", "COSC281", "COSC241")
    assert result.changed_blocks, "adding core courses must move at least one block"
    for change in result.newly_satisfied_blocks:
        assert change.status_after is BlockStatus.SATISFIED
        assert change.status_before is not BlockStatus.SATISFIED


def test_warnings_carry_the_assumptions_and_catalog_caveat(program) -> None:
    result = run(program, BASE, "COSC220")
    text = " ".join(result.warnings)
    assert "at least a C" in text and "GPA is not projected" in text
    assert "partial" in text


def test_canonical_code() -> None:
    assert canonical_code("cosc 220") == "COSC220"
    assert canonical_code("COSC-220") == "COSC220"
