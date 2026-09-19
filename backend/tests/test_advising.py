"""Prerequisite graph analysis and course recommendations.

The rule these tests exist to protect: a recommendation must be something a human
advisor could check line by line. Never a course the student cannot take, never a
course that counts toward nothing, and never advice that outruns what the catalog
actually encodes.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.audit.engine import run_audit
from app.audit.planning import build_plan
from app.audit.prereq_graph import (
    chain_depth,
    eligible_courses,
    is_eligible,
    passed_courses,
    remaining_depth,
    unlocks,
    unmet_prerequisites,
)
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.loader import load_program
from app.catalog.schema import Program
from app.schemas.audit import BlockStatus
from app.schemas.provenance import Provenance

CATALOG_DIR = Path(__file__).resolve().parents[2] / "data" / "catalog"


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(CATALOG_DIR / "morgan_cosc_bs_2026_2028.yaml")


def record(*rows, student_id: str = "jane") -> StudentRecord:
    """rows are (code, grade) or (code, grade, credits)."""
    return StudentRecord(
        student_id=student_id,
        completed=[
            CompletedCourse(
                code=r[0],
                term="Fall 2024",
                grade=r[1],
                credits=Decimal(r[2]) if len(r) > 2 else Decimal(3),
                provenance=Provenance.STUDENT_CONFIRMED,
            )
            for r in rows
        ],
    )


def plan_for(morgan, rec, **kwargs):
    return build_plan(morgan, rec, run_audit(morgan, rec), **kwargs)


class TestPassedCourses:
    def test_maps_code_to_grade(self, morgan) -> None:
        passed = passed_courses(morgan, record(("COSC111", "A"), ("ENGL101", "B")).completed)
        assert passed == {"COSC111": "A", "ENGL101": "B"}

    def test_unconfirmed_coursework_never_counts(self, morgan) -> None:
        """An unconfirmed extraction cannot make a student eligible for a course."""
        rec = StudentRecord(
            student_id="s",
            completed=[
                CompletedCourse(
                    code="COSC111",
                    term="Fall 2024",
                    grade="A",
                    credits=Decimal(4),
                    provenance=Provenance.UNVERIFIED_EXTRACTION,
                )
            ],
        )
        assert passed_courses(morgan, rec.completed) == {}

    def test_keeps_the_better_grade_on_a_retake(self, morgan) -> None:
        passed = passed_courses(morgan, record(("COSC111", "D"), ("COSC111", "A")).completed)
        assert passed["COSC111"] == "A"

    def test_ignores_courses_the_catalog_does_not_know(self, morgan) -> None:
        assert passed_courses(morgan, record(("ZZZZ999", "A")).completed) == {}


class TestEligibility:
    def test_course_with_no_prerequisites_is_always_eligible(self, morgan) -> None:
        assert is_eligible(morgan, "COSC111", {})

    def test_blocked_until_the_prerequisite_is_passed(self, morgan) -> None:
        assert not is_eligible(morgan, "COSC112", {})
        assert is_eligible(morgan, "COSC112", {"COSC111": "A"})

    def test_names_what_is_missing(self, morgan) -> None:
        missing = unmet_prerequisites(morgan, "COSC354", {})
        assert set(missing) == {"COSC220", "COSC241"}

    def test_a_d_does_not_satisfy_a_c_prerequisite(self, morgan) -> None:
        """COSC112 needs C or better in COSC111. A D counts for credit, not for entry."""
        assert morgan.course("COSC112").min_prereq_grade == "C"
        assert not is_eligible(morgan, "COSC112", {"COSC111": "D"})
        assert is_eligible(morgan, "COSC112", {"COSC111": "C"})

    def test_unknown_course_is_not_eligible(self, morgan) -> None:
        assert not is_eligible(morgan, "ZZZZ999", {})

    def test_eligible_courses_excludes_what_is_already_passed(self, morgan) -> None:
        available = {c.code for c in eligible_courses(morgan, {"COSC111": "A"})}
        assert "COSC111" not in available
        assert "COSC112" in available


class TestGraphShape:
    def test_unlocks_finds_dependents(self, morgan) -> None:
        assert "COSC112" in unlocks(morgan, "COSC111")
        assert unlocks(morgan, "COSC490") == []

    def test_chain_depth_counts_the_course_itself(self, morgan) -> None:
        assert chain_depth(morgan, "COSC111") == 1
        assert chain_depth(morgan, "COSC112") == 2
        assert chain_depth(morgan, "COSC220") == 3

    def test_remaining_depth_shrinks_as_work_is_done(self, morgan) -> None:
        """What is left matters for advice; raw catalog depth does not."""
        assert remaining_depth(morgan, "COSC220", {}) == 3
        assert remaining_depth(morgan, "COSC220", {"COSC111": "A"}) == 2
        assert remaining_depth(morgan, "COSC220", {"COSC111": "A", "COSC112": "A"}) == 1

    def test_remaining_depth_of_a_finished_course_is_zero(self, morgan) -> None:
        assert remaining_depth(morgan, "COSC111", {"COSC111": "A"}) == 0


class TestRecommendations:
    def test_only_recommends_courses_that_count(self, morgan) -> None:
        """A course serving no unfinished requirement is never suggested."""
        plan = plan_for(morgan, record(("COSC111", "A", 4)))
        for entry in plan.recommended:
            assert entry.serves, f"{entry.course.code} counts toward nothing"

    def test_never_recommends_a_course_already_passed(self, morgan) -> None:
        plan = plan_for(morgan, record(("COSC111", "A", 4), ("COSC112", "A", 4)))
        suggested = {r.course.code for r in plan.recommended}
        assert not suggested & {"COSC111", "COSC112"}

    def test_ineligible_courses_are_separated_not_suggested(self, morgan) -> None:
        """Suggesting a course whose prerequisites are unmet wastes a term."""
        plan = plan_for(morgan, record(("COSC111", "A", 4)))
        assert all(r.eligible_now for r in plan.recommended)
        assert all(not r.eligible_now for r in plan.blocked)
        assert all(r.missing_prerequisites for r in plan.blocked)

    def test_longest_remaining_chain_ranks_first(self, morgan) -> None:
        """A course with prerequisites behind it must start early or delay graduation."""
        plan = plan_for(morgan, record(("COSC111", "A", 4), ("COSC112", "A", 4)))
        chains = [r.remaining_chain for r in plan.recommended]
        assert chains == sorted(chains, reverse=True)

    def test_a_course_unlocking_more_ranks_higher_at_equal_depth(self, morgan) -> None:
        plan = plan_for(morgan, record(("COSC111", "A", 4), ("COSC112", "A", 4)))
        top = plan.recommended[0]
        assert top.course.code == "COSC220", "COSC220 unlocks the most and is available now"
        assert top.unlocks_count >= 6

    def test_every_recommendation_carries_its_reasons(self, morgan) -> None:
        plan = plan_for(morgan, record(("COSC111", "A", 4)))
        for entry in plan.recommended:
            assert entry.reasons
            assert any("required by" in r for r in entry.reasons)

    def test_recommendations_are_verified_provenance(self, morgan) -> None:
        """Derived from the catalog by rules, not generated."""
        plan = plan_for(morgan, record(("COSC111", "A", 4)))
        assert all(r.provenance is Provenance.VERIFIED for r in plan.recommended)

    def test_irregular_courses_are_flagged(self, morgan) -> None:
        plan = plan_for(morgan, record(("COSC111", "A", 4), ("COSC112", "A", 4)), limit=30)
        irregular = [r for r in plan.recommended if r.warnings]
        assert irregular, "some Morgan courses are offered AS NEEDED"
        assert any("irregularly" in w for r in irregular for w in r.warnings)

    def test_limit_is_honoured(self, morgan) -> None:
        plan = plan_for(morgan, record(("COSC111", "A", 4)), limit=3)
        assert len(plan.recommended) <= 3


class TestPlanShape:
    def test_reports_completed_coursework_and_credits(self, morgan) -> None:
        plan = plan_for(morgan, record(("COSC111", "A", 4), ("ENGL101", "B", 3)))
        assert {c.code for c in plan.completed} == {"COSC111", "ENGL101"}
        assert plan.completed_credits == Decimal(7)

    def test_gaps_cover_every_unfinished_requirement(self, morgan) -> None:
        rec = record(("COSC111", "A", 4))
        audit = run_audit(morgan, rec)
        plan = build_plan(morgan, rec, audit)
        unfinished = {b.block_id for b in audit.blocks if b.status is not BlockStatus.SATISFIED}
        assert {g.block_id for g in plan.gaps} == unfinished

    def test_gap_says_how_many_courses_remain(self, morgan) -> None:
        plan = plan_for(morgan, record(("COSC111", "A", 4)))
        major = next(g for g in plan.gaps if g.block_id == "major_required_courses")
        assert major.courses_still_needed == 11  # 12 required, 1 done

    def test_carries_the_coverage_caveat(self, morgan) -> None:
        """Advice off a partial catalog is partial advice, and must say so."""
        plan = plan_for(morgan, record(("COSC111", "A", 4)))
        assert plan.coverage is not None and "PARTIAL" in plan.coverage
        assert any("awaiting department clarification" in c for c in plan.caveats)

    def test_empty_record_still_produces_a_usable_plan(self, morgan) -> None:
        plan = plan_for(morgan, record())
        assert plan.completed == []
        assert plan.recommended, "a new student should be told where to start"
        assert all(r.eligible_now for r in plan.recommended)

    def test_plan_and_audit_agree_on_what_is_done(self, morgan) -> None:
        """Built from the audit, so the two can never disagree."""
        rec = record(("COSC111", "A", 4), ("ENGL101", "B", 3))
        audit = run_audit(morgan, rec)
        plan = build_plan(morgan, rec, audit)
        applied = {a.course.code for b in audit.blocks for a in b.applied}
        assert {c.code for c in plan.completed} == applied
