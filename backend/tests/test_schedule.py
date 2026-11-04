"""Semester planning.

The rule these tests protect: a planned course is one the student can actually take
that term. Never ahead of its prerequisites, never in a term the catalog says it does
not run, and never a plan that claims more than the catalog encodes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.audit.prereq_graph import passed_courses
from app.audit.record import CompletedCourse, StudentRecord
from app.audit.schedule import (
    TermError,
    default_start_term,
    generate_schedule,
    next_term,
    parse_term,
    validate_schedule,
)
from app.catalog.loader import load_program
from app.catalog.schema import Program, Term
from app.schemas.provenance import Provenance

CATALOG_DIR = Path(__file__).resolve().parents[2] / "data" / "catalog"


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(CATALOG_DIR / "morgan_cosc_bs_2026_2028.yaml")


def with_math141_stub(program: Program) -> Program:
    """MATH141 as the zero-credit UNVERIFIED stub it was until 2026-11-04.

    Its [COURSE] record has since been read, so the real catalog no longer holds an
    unverified stub; this rebuilds one so the stub warning stays exercised.
    """
    courses = [
        (
            c.model_copy(update={"title": "UNVERIFIED - test stub", "credits": Decimal(0)})
            if c.code == "MATH141"
            else c
        )
        for c in program.courses
    ]
    return program.model_copy(update={"courses": courses})


def record(*rows) -> StudentRecord:
    """rows are (code, grade) or (code, grade, credits)."""
    return StudentRecord(
        student_id="jane",
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


def fall_only_program(morgan: Program, code: str = "COSC111") -> Program:
    """The real catalog gives every course fall and spring, so offering rules need a variant."""
    courses = [
        c.model_copy(update={"terms_offered": [Term.FALL]}) if c.code == code else c
        for c in morgan.courses
    ]
    return morgan.model_copy(update={"courses": courses})


class TestTerms:
    def test_parses_season_and_year(self) -> None:
        assert parse_term("Fall 2027") == (Term.FALL, 2027)
        assert parse_term("  spring 2028 ") == (Term.SPRING, 2028)

    @pytest.mark.parametrize("bad", ["2027", "Autumn 2027", "Fall", "Winter 2027", "Fall 27x"])
    def test_rejects_what_it_cannot_read(self, bad: str) -> None:
        with pytest.raises(TermError):
            parse_term(bad)

    def test_next_term_skips_summer_by_default(self) -> None:
        assert next_term(Term.SPRING, 2027) == (Term.FALL, 2027)
        assert next_term(Term.FALL, 2027) == (Term.SPRING, 2028)

    def test_next_term_can_include_summer(self) -> None:
        assert next_term(Term.SPRING, 2027, include_summer=True) == (Term.SUMMER, 2027)
        assert next_term(Term.SUMMER, 2027, include_summer=True) == (Term.FALL, 2027)

    @pytest.mark.parametrize(
        ("today", "expected"),
        [
            (date(2026, 11, 4), "Spring 2027"),
            (date(2027, 3, 1), "Fall 2027"),
            (date(2027, 6, 20), "Fall 2027"),
            (date(2027, 9, 2), "Spring 2028"),
        ],
    )
    def test_default_start_is_the_next_unstarted_term(self, today: date, expected: str) -> None:
        assert default_start_term(today) == expected


class TestGenerate:
    def test_every_planned_course_is_takeable_when_planned(self, morgan: Program) -> None:
        """Replay the plan: each course's prerequisites must be done by an earlier term."""
        rec = record(("COSC111", "A", 4))
        plan = generate_schedule(morgan, rec, start_term="Spring 2027")
        assert plan.terms, "a first-year student has plenty left to plan"

        passed = passed_courses(morgan, rec.completed)
        for planned_term in plan.terms:
            season, _ = parse_term(planned_term.term)
            for item in planned_term.courses:
                course = morgan.course(item.course.code)
                assert season in course.terms_offered
                for prereq in course.prerequisites:
                    assert prereq in passed, f"{course.code} before {prereq}"
            # Only AFTER the term do its courses count as passed.
            for item in planned_term.courses:
                passed[item.course.code] = "A"

    def test_no_course_is_planned_twice_or_after_being_passed(self, morgan: Program) -> None:
        plan = generate_schedule(morgan, record(("COSC111", "A", 4)), start_term="Spring 2027")
        codes = [c.course.code for t in plan.terms for c in t.courses]
        assert len(codes) == len(set(codes))
        assert "COSC111" not in codes

    def test_a_term_never_exceeds_the_credit_target(self, morgan: Program) -> None:
        plan = generate_schedule(morgan, record(), start_term="Fall 2026", credit_target=Decimal(9))
        assert plan.terms
        assert all(t.credits <= Decimal(9) for t in plan.terms)

    def test_a_prerequisite_unlocks_the_next_term_not_the_same_one(self, morgan: Program) -> None:
        plan = generate_schedule(morgan, record(), start_term="Fall 2026")
        where = {c.course.code: i for i, t in enumerate(plan.terms) for c in t.courses}
        assert "COSC111" in where
        if "COSC112" in where:
            assert where["COSC112"] > where["COSC111"]

    def test_the_generated_plan_passes_the_validator(self, morgan: Program) -> None:
        """Generator and validator share one set of rules. They must never disagree."""
        rec = record(("COSC111", "A", 4))
        plan = generate_schedule(morgan, rec, start_term="Spring 2027")
        layout = [(t.term, [c.course.code for c in t.courses]) for t in plan.terms]
        result = validate_schedule(morgan, rec, layout)
        assert result.valid, [i.message for i in result.issues if i.severity == "error"]

    def test_in_progress_courses_are_assumed_finished_and_not_replanned(
        self, morgan: Program
    ) -> None:
        rec = record(("COSC111", "A", 4), ("COSC112", "IP", 4))
        plan = generate_schedule(morgan, rec, start_term="Spring 2027")
        codes = {c.course.code for t in plan.terms for c in t.courses}
        assert "COSC112" not in codes
        assert any("taking now" in c for c in plan.caveats)

    def test_unconfirmed_coursework_does_not_shorten_the_plan(self, morgan: Program) -> None:
        unconfirmed = StudentRecord(
            student_id="jane",
            completed=[
                CompletedCourse(
                    code="COSC111",
                    term="Fall 2025",
                    grade="A",
                    credits=Decimal(4),
                    provenance=Provenance.UNVERIFIED_EXTRACTION,
                )
            ],
        )
        plan = generate_schedule(morgan, unconfirmed, start_term="Spring 2027")
        codes = {c.course.code for t in plan.terms for c in t.courses}
        assert "COSC111" in codes

    def test_never_claims_a_graduation_date_off_a_partial_catalog(self, morgan: Program) -> None:
        plan = generate_schedule(morgan, record(), start_term="Fall 2026")
        assert any("not a graduation date" in c for c in plan.caveats)
        assert any("assumed to be passed" in c for c in plan.caveats)

    def test_a_fall_only_course_waits_for_the_fall(self, morgan: Program) -> None:
        program = fall_only_program(morgan)
        plan = generate_schedule(program, record(), start_term="Spring 2027")
        where = {c.course.code: t.term for t in plan.terms for c in t.courses}
        assert where["COSC111"].startswith("Fall")

    def test_says_when_term_offerings_are_assumed(self, morgan: Program) -> None:
        plan = generate_schedule(morgan, record(), start_term="Fall 2026")
        assert any("does not record which terms" in c for c in plan.caveats)

    def test_as_needed_courses_carry_a_warning(self, morgan: Program) -> None:
        plan = generate_schedule(morgan, record(("COSC111", "A", 4)), start_term="Spring 2027")
        for term in plan.terms:
            for item in term.courses:
                if morgan.course(item.course.code).offered_as_needed:
                    assert any("irregularly" in w for w in item.warnings)

    def test_schedules_a_prerequisite_no_requirement_lists(self, morgan: Program) -> None:
        """COSC354 needs COSC241, which needs MATH141. No block lists either of those."""
        rec = record(("COSC111", "A", 4), ("COSC112", "A", 4))
        plan = generate_schedule(morgan, rec, start_term="Spring 2027")
        where = {c.course.code: i for i, t in enumerate(plan.terms) for c in t.courses}
        assert where["MATH141"] < where["COSC241"] < where["COSC354"]
        assert not plan.unscheduled

    def test_an_unverified_stub_course_is_flagged(self, morgan: Program) -> None:
        plan = generate_schedule(
            with_math141_stub(morgan),
            record(("COSC111", "A", 4), ("COSC112", "A", 4)),
            start_term="Spring 2027",
        )
        stub = next(c for t in plan.terms for c in t.courses if c.course.code == "MATH141")
        assert any("no verified title" in w for w in stub.warnings)

    def test_plans_are_marked_verified(self, morgan: Program) -> None:
        plan = generate_schedule(morgan, record(), start_term="Fall 2026")
        assert plan.provenance is Provenance.VERIFIED

    def test_a_bad_start_term_is_an_error_not_a_guess(self, morgan: Program) -> None:
        with pytest.raises(TermError):
            generate_schedule(morgan, record(), start_term="sometime soon")


class TestValidate:
    def test_flags_a_course_ahead_of_its_prerequisite(self, morgan: Program) -> None:
        result = validate_schedule(morgan, record(), [("Fall 2026", ["COSC112"])])
        assert not result.valid
        assert any(i.course == "COSC112" and "COSC111" in i.message for i in result.issues)

    def test_a_prerequisite_in_the_same_term_does_not_help(self, morgan: Program) -> None:
        result = validate_schedule(morgan, record(), [("Fall 2026", ["COSC111", "COSC112"])])
        assert not result.valid

    def test_a_prerequisite_in_an_earlier_term_does(self, morgan: Program) -> None:
        result = validate_schedule(
            morgan, record(), [("Fall 2026", ["COSC111"]), ("Spring 2027", ["COSC112"])]
        )
        assert not [i for i in result.issues if i.severity == "error" and i.course == "COSC112"]

    def test_flags_a_term_the_course_does_not_run(self, morgan: Program) -> None:
        fall_only = fall_only_program(morgan)
        result = validate_schedule(fall_only, record(), [("Spring 2027", ["COSC111"])])
        assert not result.valid
        assert any("not offered in spring" in i.message for i in result.issues)

    def test_flags_a_course_already_passed(self, morgan: Program) -> None:
        result = validate_schedule(
            morgan, record(("COSC111", "A", 4)), [("Fall 2026", ["COSC111"])]
        )
        assert not result.valid

    def test_flags_a_duplicate_across_terms(self, morgan: Program) -> None:
        result = validate_schedule(
            morgan, record(), [("Fall 2026", ["COSC111"]), ("Spring 2027", ["COSC111"])]
        )
        assert not result.valid

    def test_flags_an_unknown_course(self, morgan: Program) -> None:
        result = validate_schedule(morgan, record(), [("Fall 2026", ["ZZZZ999"])])
        assert not result.valid

    def test_flags_terms_out_of_order(self, morgan: Program) -> None:
        result = validate_schedule(
            morgan, record(), [("Spring 2027", ["COSC111"]), ("Fall 2026", ["ENGL101"])]
        )
        assert any("chronological" in i.message for i in result.issues)

    def test_overload_is_a_warning_not_an_error(self, morgan: Program) -> None:
        result = validate_schedule(
            morgan, record(), [("Fall 2026", ["COSC111"])], credit_target=Decimal(1)
        )
        assert any(i.severity == "warning" and "target" in i.message for i in result.issues)
        assert result.valid

    def test_accepts_codes_with_a_space_and_any_case(self, morgan: Program) -> None:
        result = validate_schedule(morgan, record(), [("Fall 2026", ["cosc 111"])])
        assert not [i for i in result.issues if i.course == "COSC111" and i.severity == "error"]

    def test_an_unreadable_term_is_reported(self, morgan: Program) -> None:
        result = validate_schedule(morgan, record(), [("someday", ["COSC111"])])
        assert not result.valid
