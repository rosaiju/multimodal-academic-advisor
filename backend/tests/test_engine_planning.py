"""Planning, schedule, compare and explain: retakes, prerequisites, in-progress, wording.

Items 5, 8 and 15 of the engine review, plus the planner/compare/explain review:

* a failed or in-progress prerequisite never counts as passed;
* a course passed below a requirement's minimum grade is a RETAKE, and every
  surface (plan, schedule, validate, compare) says so;
* students see requirement NAMES, never block ids;
* a schedule is never a graduation date while the catalog is partial.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.audit.compare import compare_courses
from app.audit.engine import run_audit
from app.audit.explain_audit import explain_block
from app.audit.planning import blocks_needing_retake, build_plan
from app.audit.prereq_graph import passed_courses, unmet_prerequisites
from app.audit.record import CompletedCourse, StudentRecord
from app.audit.schedule import generate_schedule, is_unverified, validate_schedule
from app.catalog.loader import load_program
from app.catalog.schema import Program
from app.schemas.audit import BlockStatus
from app.schemas.provenance import Provenance

MORGAN = Path(__file__).resolve().parents[2] / "data" / "catalog" / "morgan_cosc_bs_2026_2028.yaml"


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(MORGAN)


def rec(*rows) -> StudentRecord:
    return StudentRecord(
        student_id="s",
        completed=[
            CompletedCourse(
                code=r[0],
                term="Fall 2025",
                grade=r[1],
                credits=Decimal(r[2]) if len(r) > 2 else Decimal(3),
                provenance=Provenance.STUDENT_CONFIRMED,
            )
            for r in rows
        ],
    )


def plan_for(morgan, record):
    return build_plan(morgan, record, run_audit(morgan, record), limit=100)


# --------------------------------------------------------------------------- #
# Item 5: prerequisite chains
# --------------------------------------------------------------------------- #
class TestPrerequisites:
    def test_failed_prerequisite_is_unmet(self, morgan) -> None:
        passed = passed_courses(morgan, rec(("COSC111", "F", 4)).completed)
        assert unmet_prerequisites(morgan, "COSC112", passed) == ["COSC111"]

    def test_d_is_below_a_c_prerequisite_minimum(self, morgan) -> None:
        passed = passed_courses(morgan, rec(("COSC111", "D", 4)).completed)
        assert unmet_prerequisites(morgan, "COSC112", passed) == ["COSC111"]

    @pytest.mark.parametrize("grade", ["IP", "REG", "INC", "W", "I", "P"])
    def test_unfinished_or_ungraded_prerequisite_is_unmet(self, morgan, grade) -> None:
        passed = passed_courses(morgan, rec(("COSC111", grade, 4)).completed)
        assert unmet_prerequisites(morgan, "COSC112", passed) == ["COSC111"]

    def test_failed_then_passed_prerequisite_is_met(self, morgan) -> None:
        passed = passed_courses(morgan, rec(("COSC111", "F", 4), ("COSC111", "B", 4)).completed)
        assert unmet_prerequisites(morgan, "COSC112", passed) == []

    def test_in_progress_prereq_blocks_the_next_course_in_the_plan(self, morgan) -> None:
        plan = plan_for(morgan, rec(("COSC111", "A", 4), ("COSC112", "IP", 4)))
        assert "COSC220" in {b.course.code for b in plan.blocked}
        assert "COSC112" not in {r.course.code for r in plan.recommended + plan.blocked}
        assert "COSC112" in {r.code for r in plan.under_way}


# --------------------------------------------------------------------------- #
# Retakes
# --------------------------------------------------------------------------- #
class TestRetakes:
    def test_blocks_needing_retake(self, morgan) -> None:
        assert blocks_needing_retake(morgan, "COSC111", "D") == [
            "Required Courses for Computer Science Major"
        ]
        assert blocks_needing_retake(morgan, "COSC111", "C") == []
        assert blocks_needing_retake(morgan, "UNIV101", "D") == []

    def test_d_in_a_c_minimum_course_is_recommended_as_a_retake(self, morgan) -> None:
        """Regression: `passed` (D counts) hid the course from the plan entirely."""
        plan = plan_for(morgan, rec(("COSC111", "D", 4)))
        entry = next(r for r in plan.recommended if r.course.code == "COSC111")
        assert any("retake" in r and "D" in r for r in entry.reasons)
        assert entry.remaining_chain >= 1

    def test_course_meeting_the_minimum_is_not_recommended_again(self, morgan) -> None:
        plan = plan_for(morgan, rec(("COSC111", "C", 4)))
        assert "COSC111" not in {r.course.code for r in plan.recommended + plan.blocked}

    def test_validate_allows_a_retake_with_a_warning(self, morgan) -> None:
        result = validate_schedule(morgan, rec(("COSC111", "D", 4)), [("Spring 2027", ["COSC111"])])
        assert result.valid
        assert any(i.severity == "warning" and "retake" in i.message for i in result.issues)

    def test_validate_still_rejects_a_course_already_passed_well(self, morgan) -> None:
        result = validate_schedule(morgan, rec(("COSC111", "B", 4)), [("Spring 2027", ["COSC111"])])
        assert not result.valid

    def test_compare_flags_a_retake(self, morgan) -> None:
        record = rec(("COSC111", "D", 4))
        out = compare_courses(morgan, record, run_audit(morgan, record), ["COSC111", "COSC459"])
        facts = {f.code: f for f in out.courses}
        assert facts["COSC111"].state == "passed"
        assert facts["COSC111"].retake_needed
        assert facts["COSC111"].advances_open_requirement
        assert any("retake" in d for d in out.differences)
        assert not any("already passed COSC111" in d for d in out.differences)

    def test_compare_passed_well_is_not_a_retake(self, morgan) -> None:
        record = rec(("COSC111", "A", 4))
        out = compare_courses(morgan, record, run_audit(morgan, record), ["COSC111", "COSC459"])
        assert not out.courses[0].retake_needed
        assert not out.courses[0].advances_open_requirement


# --------------------------------------------------------------------------- #
# Wording students see
# --------------------------------------------------------------------------- #
class TestRequirementNamesNotIds:
    """Lead walkthrough: reasons read "required by 1 unfinished requirement:
    major_required_courses". Students see names; ids stay in `serves`."""

    def test_recommendation_reasons_use_block_names(self, morgan) -> None:
        plan = plan_for(morgan, rec())
        ids = {b.id for b in morgan.requirement_blocks}
        for entry in plan.recommended + plan.blocked:
            assert set(entry.serves) <= ids
            for reason in entry.reasons:
                assert not any(i in reason for i in ids), reason
        first = next(r for r in plan.recommended if r.course.code == "COSC111")
        assert "Required Courses for Computer Science Major" in first.reasons[0]

    def test_schedule_reasons_use_block_names(self, morgan) -> None:
        plan = generate_schedule(morgan, rec(), start_term="Spring 2027")
        ids = {b.id for b in morgan.requirement_blocks}
        for term in plan.terms:
            for item in term.courses:
                for reason in item.reasons:
                    assert not any(i in reason for i in ids), reason


# --------------------------------------------------------------------------- #
# Item 15 / planner review
# --------------------------------------------------------------------------- #
class TestScheduleConstraints:
    @pytest.mark.parametrize("target", [6, 9, 12, 15, 18])
    def test_no_term_exceeds_the_credit_target(self, morgan, target) -> None:
        plan = generate_schedule(
            morgan, rec(), start_term="Fall 2026", credit_target=Decimal(target)
        )
        assert plan.terms
        for term in plan.terms:
            assert term.credits <= target
            assert term.credits == sum(c.course.credits for c in term.courses)

    def test_prerequisites_land_in_strictly_earlier_terms(self, morgan) -> None:
        plan = generate_schedule(morgan, rec(), start_term="Fall 2026", max_terms=12)
        where = {c.course.code: i for i, t in enumerate(plan.terms) for c in t.courses}
        for code, idx in where.items():
            for pre in morgan.course(code).prerequisites:
                if pre in where:
                    assert where[pre] < idx, f"{pre} must precede {code}"

    def test_generated_plan_passes_its_own_validation(self, morgan) -> None:
        record = rec(("COSC111", "A", 4))
        plan = generate_schedule(morgan, record, start_term="Spring 2027")
        layout = [(t.term, [c.course.code for c in t.courses]) for t in plan.terms]
        result = validate_schedule(morgan, record, layout)
        assert result.valid, [i.message for i in result.issues if i.severity == "error"]

    def test_moving_a_course_before_its_prerequisite_is_an_error(self, morgan) -> None:
        record = rec(("COSC111", "A", 4))
        plan = generate_schedule(morgan, record, start_term="Spring 2027")
        layout = [(t.term, [c.course.code for c in t.courses]) for t in plan.terms]
        # Move COSC220 into the term that holds COSC112, its prerequisite.
        i112 = next(i for i, (_, cs) in enumerate(layout) if "COSC112" in cs)
        i220 = next(i for i, (_, cs) in enumerate(layout) if "COSC220" in cs)
        layout[i220][1].remove("COSC220")
        layout[i112][1].append("COSC220")
        result = validate_schedule(morgan, record, layout)
        assert not result.valid
        assert any(i.course == "COSC220" and "COSC112" in i.message for i in result.issues)

    def test_in_progress_course_is_not_planned_again(self, morgan) -> None:
        record = rec(("COSC111", "A", 4), ("COSC112", "IP", 4))
        plan = generate_schedule(morgan, record, start_term="Spring 2027")
        planned = [c.course.code for t in plan.terms for c in t.courses]
        assert "COSC112" not in planned
        assert "COSC220" in planned  # plannable after the in-progress prerequisite
        assert any("taking now are assumed to be passed" in c for c in plan.caveats)

    def test_validate_names_an_in_progress_course(self, morgan) -> None:
        record = rec(("COSC111", "A", 4), ("COSC112", "IP", 4))
        result = validate_schedule(morgan, record, [("Spring 2027", ["COSC112"])])
        assert not result.valid
        assert "in progress" in result.issues[0].message

    def test_irregular_courses_always_carry_a_warning(self, morgan) -> None:
        plan = generate_schedule(morgan, rec(), start_term="Fall 2026", max_terms=12)
        for term in plan.terms:
            for item in term.courses:
                if morgan.course(item.course.code).offered_as_needed:
                    assert any("irregular" in w for w in item.warnings), item.course.code

    def test_validate_warns_on_irregular_courses(self, morgan) -> None:
        record = rec(("COSC111", "A", 4), ("COSC112", "A", 4), ("COSC220", "A", 4))
        result = validate_schedule(morgan, record, [("Spring 2027", ["COSC352"])])
        assert result.valid
        assert any(i.severity == "warning" and "irregularly" in i.message for i in result.issues)

    def test_course_bigger_than_the_target_says_why_it_was_not_placed(self, morgan) -> None:
        plan = generate_schedule(morgan, rec(), start_term="Fall 2026", credit_target=Decimal(3))
        reasons = {u.course.code: u.reason for u in plan.unscheduled}
        assert "COSC111" in reasons
        assert "per-term target" in reasons["COSC111"]

    def test_never_a_graduation_date_on_a_partial_catalog(self, morgan) -> None:
        plan = generate_schedule(morgan, rec(), start_term="Fall 2026")
        assert plan.coverage_complete is False
        assert "not a graduation date" in plan.graduation_caveat
        assert any("not a graduation date" in c for c in plan.caveats)
        dumped = plan.model_dump()
        assert "graduation_term" not in dumped and "graduation_date" not in dumped

    def test_unverified_entries_are_detected(self, morgan) -> None:
        assert is_unverified(morgan.course("MATH141"))
        assert not is_unverified(morgan.course("COSC111"))

    def test_unsourced_prerequisites_are_called_out(self, morgan) -> None:
        """ENGL102 and MATH242 carry no sourced prerequisites, so the planner may place
        them beside ENGL101 / MATH241. It must say it cannot order them."""
        plan = generate_schedule(morgan, rec(), start_term="Fall 2026")
        assert any("records no prerequisites" in c for c in plan.caveats)


# --------------------------------------------------------------------------- #
# explain_audit
# --------------------------------------------------------------------------- #
class TestExplain:
    def test_retake_in_progress_is_explained_as_in_progress(self, morgan) -> None:
        """Regression: the first transcript row (an old F) was the only reason shown."""
        record = rec(("COSC111", "F", 4), ("COSC111", "IP", 4))
        out = explain_block(morgan, record, run_audit(morgan, record), "major_required_courses")
        reason = next(n.reason for n in out.not_counted if n.code == "COSC111")
        assert reason.startswith("In progress")

    def test_pass_grade_is_explained_not_called_below_minimum(self, morgan) -> None:
        record = rec(("UNIV101", "P", 1))
        out = explain_block(morgan, record, run_audit(morgan, record), "first_year_seminar")
        assert out.status is BlockStatus.NEEDS_ADVISOR
        reason = next(n.reason for n in out.not_counted if n.code == "UNIV101")
        assert "pass grade" in reason
        assert "UNIV101" in out.headline

    def test_policy_dependent_gpa_headline_explains_itself(self, morgan) -> None:
        record = rec(("COSC111", "F", 4), ("COSC111", "A", 4))
        out = explain_block(morgan, record, run_audit(morgan, record), "cumulative_gpa")
        assert out.status is BlockStatus.NEEDS_ADVISOR
        assert "repeated" in out.headline

    def test_next_step_uses_requirement_names(self, morgan) -> None:
        record = rec()
        out = explain_block(morgan, record, run_audit(morgan, record), "major_required_courses")
        assert out.next_step and "major_required_courses" not in out.next_step
