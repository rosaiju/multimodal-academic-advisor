"""Optimal (global) course-to-requirement assignment.

The point of this matcher is a single claim: it never reports less progress than
greedy, and sometimes reports more, because it does not let an early block spend a
course a later block needed.

`demo_university_cs.yaml` was constructed to contain exactly that conflict, so the
headline tests here run real audits against it rather than a synthetic fixture.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.audit.engine import run_audit
from app.audit.optimal import assign_optimally, representative_indices
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.loader import load_program
from app.catalog.schema import Program
from app.schemas.audit import BlockStatus, MatcherStrategy

CATALOG_DIR = Path(__file__).resolve().parents[2] / "data" / "catalog"
DEMO = CATALOG_DIR / "demo_university_cs.yaml"
MORGAN = CATALOG_DIR / "morgan_cosc_bs_2026_2028.yaml"


@pytest.fixture(scope="module")
def demo() -> Program:
    return load_program(DEMO)


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(MORGAN)


def record(*rows, student_id: str = "s1") -> StudentRecord:
    completed = []
    for row in rows:
        code, grade = row[0], row[1]
        credits = Decimal(row[2]) if len(row) > 2 else Decimal(3)
        completed.append(CompletedCourse(code=code, term="Fall 2026", grade=grade, credits=credits))
    return StudentRecord(student_id=student_id, completed=completed)


def block(result, block_id):
    return next(b for b in result.blocks if b.block_id == block_id)


def satisfied_ids(result) -> set[str]:
    return {b.block_id for b in result.blocks if b.status is BlockStatus.SATISFIED}


class TestGreedyUnderReports:
    """The reason this matcher exists."""

    def test_phys211_conflict_is_resolved(self, demo) -> None:
        """PHYS211 satisfies both the math elective and lab science.

        With MATH312 also completed, the student has genuinely done both
        requirements. Greedy gives PHYS211 to the math elective because it comes
        first in the catalog, then reports science unmet.
        """
        rec = record(("PHYS211", "A", 4), ("MATH312", "A", 3))

        greedy = run_audit(demo, rec, strategy=MatcherStrategy.GREEDY)
        assert block(greedy, "math_elective").status is BlockStatus.SATISFIED
        assert block(greedy, "gen_ed_science").status is BlockStatus.UNMET

        optimal = run_audit(demo, rec, strategy=MatcherStrategy.OPTIMAL_BIPARTITE)
        assert block(optimal, "math_elective").status is BlockStatus.SATISFIED
        assert block(optimal, "gen_ed_science").status is BlockStatus.SATISFIED

    def test_optimal_satisfies_a_superset_of_greedy(self, demo) -> None:
        rec = record(("PHYS211", "A", 4), ("MATH312", "A", 3))
        greedy = satisfied_ids(run_audit(demo, rec, strategy=MatcherStrategy.GREEDY))
        optimal = satisfied_ids(run_audit(demo, rec, strategy=MatcherStrategy.OPTIMAL_BIPARTITE))
        assert greedy < optimal, "optimal must strictly improve on this record"

    def test_optimal_applies_at_least_as_many_credits(self, demo) -> None:
        rec = record(("PHYS211", "A", 4), ("MATH312", "A", 3))
        greedy = run_audit(demo, rec, strategy=MatcherStrategy.GREEDY)
        optimal = run_audit(demo, rec, strategy=MatcherStrategy.OPTIMAL_BIPARTITE)
        assert optimal.total_credits_applied >= greedy.total_credits_applied

    def test_optimal_applies_more_courses(self, demo) -> None:
        """Measured through the engine, not a second copy of the greedy algorithm."""
        rec = record(("PHYS211", "A", 4), ("MATH312", "A", 3))
        greedy = run_audit(demo, rec, strategy=MatcherStrategy.GREEDY)
        optimal = run_audit(demo, rec, strategy=MatcherStrategy.OPTIMAL_BIPARTITE)
        assert len([a for b in optimal.blocks for a in b.applied]) > len(
            [a for b in greedy.blocks for a in b.applied]
        )


class TestInvariantsHold:
    """Everything guaranteed under greedy must still hold under optimal."""

    def test_no_double_counting(self, demo) -> None:
        rec = record(
            ("COSC111", "A", 4),
            ("COSC112", "A", 4),
            ("COSC220", "A", 4),
            ("COSC241", "A", 3),
            ("COSC281", "A", 3),
            ("COSC349", "A", 3),
            ("COSC350", "A", 3),
            ("COSC370", "A", 3),
        )
        result = run_audit(demo, rec, strategy=MatcherStrategy.OPTIMAL_BIPARTITE)
        applied = [a.course.code for b in result.blocks for a in b.applied]
        assert len(applied) == len(set(applied)), f"duplicate assignment: {applied}"

    def test_retake_cannot_fill_two_slots(self, demo) -> None:
        """Two transcript rows for one course are still one course to the degree."""
        rec = record(("COSC349", "C", 3), ("COSC349", "A", 3))
        result = run_audit(demo, rec, strategy=MatcherStrategy.OPTIMAL_BIPARTITE)
        applied = [a.course.code for b in result.blocks for a in b.applied]
        assert applied.count("COSC349") <= 1

    def test_representative_prefers_the_better_grade(self) -> None:
        pool = [
            CompletedCourse(code="X", term="t", grade="D", credits=Decimal(3)),
            CompletedCourse(code="X", term="t", grade="A", credits=Decimal(3)),
        ]
        assert representative_indices(pool)["X"] == 1

    def test_non_passing_grades_are_not_representatives(self) -> None:
        pool = [CompletedCourse(code="X", term="t", grade="W", credits=Decimal(3))]
        assert representative_indices(pool) == {}

    def test_grade_minimums_are_still_enforced(self, demo) -> None:
        """core_cs_lower requires C or better, so a D must not fill its slot."""
        result = run_audit(
            demo, record(("COSC111", "D", 4)), strategy=MatcherStrategy.OPTIMAL_BIPARTITE
        )
        assert block(result, "core_cs_lower").courses_applied == 0

    def test_coverage_and_eligibility_rules_unchanged(self, morgan) -> None:
        result = run_audit(
            morgan, record(("COSC111", "A", 4)), strategy=MatcherStrategy.OPTIMAL_BIPARTITE
        )
        assert not result.coverage.is_complete
        assert not result.is_graduation_eligible

    def test_strategy_is_recorded_in_the_result(self, demo) -> None:
        result = run_audit(demo, record(), strategy=MatcherStrategy.OPTIMAL_BIPARTITE)
        assert result.strategy is MatcherStrategy.OPTIMAL_BIPARTITE


class TestEquivalenceWhereThereIsNoConflict:
    def test_identical_when_no_course_is_contested(self, demo) -> None:
        """With no overlap, the two strategies must agree exactly."""
        rec = record(("ENGL101", "A", 3), ("ENGL102", "A", 3), ("MATH241", "A", 4))
        greedy = run_audit(demo, rec, strategy=MatcherStrategy.GREEDY)
        optimal = run_audit(demo, rec, strategy=MatcherStrategy.OPTIMAL_BIPARTITE)
        assert satisfied_ids(greedy) == satisfied_ids(optimal)
        assert greedy.total_credits_applied == optimal.total_credits_applied

    def test_empty_record_is_identical(self, demo) -> None:
        greedy = run_audit(demo, record(), strategy=MatcherStrategy.GREEDY)
        optimal = run_audit(demo, record(), strategy=MatcherStrategy.OPTIMAL_BIPARTITE)
        assert satisfied_ids(greedy) == satisfied_ids(optimal)

    def test_morgan_audit_agrees_where_blocks_do_not_overlap(self, morgan) -> None:
        """Morgan's six encoded blocks share no course, so both strategies match."""
        rec = record(
            ("COSC111", "A", 4),
            ("ENGL101", "A", 3),
            ("ENGL102", "A", 3),
            ("UNIV101", "A", 1),
        )
        greedy = run_audit(morgan, rec, strategy=MatcherStrategy.GREEDY)
        optimal = run_audit(morgan, rec, strategy=MatcherStrategy.OPTIMAL_BIPARTITE)
        assert satisfied_ids(greedy) == satisfied_ids(optimal)
        assert greedy.total_credits_applied == optimal.total_credits_applied


class TestSlotExpansion:
    def test_all_of_makes_one_slot_per_required_course(self, demo) -> None:
        pool = list(record(("COSC111", "A", 4), ("COSC112", "A", 4)).completed)
        assigned = assign_optimally(demo, pool)
        assert len(assigned["core_cs_lower"]) == 2

    def test_n_of_never_takes_more_than_n(self, demo) -> None:
        """Three eligible systems courses, but the concentration needs only two."""
        pool = list(record(("COSC349", "A", 3), ("COSC350", "A", 3), ("COSC370", "A", 3)).completed)
        assigned = assign_optimally(demo, pool)
        assert len(assigned.get("systems_concentration", [])) <= 2

    def test_credits_from_is_left_to_the_engine(self, demo) -> None:
        """credits_from is quantity-based, not slot-based, so it is not preassigned."""
        pool = list(record(("COSC349", "A", 3)).completed)
        assert "cs_electives_upper" not in assign_optimally(demo, pool)
