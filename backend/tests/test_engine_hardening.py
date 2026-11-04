"""Engine hardening: grades, credit totals, GPA, residency, retakes, overlap.

Each class pins one rule from docs/engine-rules.md, with the expected behaviour and
the edge cases that used to go wrong. Where Morgan's policy is NOT encoded the test
asserts the engine says so (NEEDS_ADVISOR, a range, a note) rather than a guess.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.audit.each_of import evaluate_each_of
from app.audit.engine import run_audit
from app.audit.grades import earns_credit, gpa_figures, is_recognised, subject_of
from app.audit.optimal import representative_indices
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.loader import load_program
from app.catalog.schema import Program
from app.schemas.audit import BlockStatus, MatcherStrategy
from app.schemas.provenance import Provenance

CATALOG_DIR = Path(__file__).resolve().parents[2] / "data" / "catalog"
MORGAN = CATALOG_DIR / "morgan_cosc_bs_2026_2028.yaml"
DEMO = CATALOG_DIR / "demo_university_cs.yaml"
HOME = "Morgan State University"


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(MORGAN)


@pytest.fixture(scope="module")
def demo() -> Program:
    return load_program(DEMO)


def row(code, grade, credits=3, *, term="Fall 2025", institution=None, provenance=None):
    return CompletedCourse(
        code=code,
        term=term,
        grade=grade,
        credits=Decimal(credits),
        institution=institution,
        provenance=provenance or Provenance.VERIFIED,
    )


def rec(*rows) -> StudentRecord:
    return StudentRecord(student_id="s", completed=list(rows))


def block(result, block_id):
    return next(b for b in result.blocks if b.block_id == block_id)


def tiny_program(blocks, courses=None, **extra) -> Program:
    courses = courses or [
        {"code": "AAA100", "title": "A", "credits": 3},
        {"code": "AAA200", "title": "B", "credits": 3},
        {"code": "AAA300", "title": "C", "credits": 3},
    ]
    data = {
        "program_id": "tiny",
        "program": "Tiny",
        "institution": HOME,
        "catalog_year": "2026",
        "total_credits_required": 9,
        "major_subjects": ["AAA"],
        "courses": courses,
        "requirement_blocks": blocks,
    }
    data.update(extra)
    return Program.model_validate(data)


# --------------------------------------------------------------------------- #
# Grade classification
# --------------------------------------------------------------------------- #
class TestGradeClassification:
    @pytest.mark.parametrize("grade", ["A", "b+", " C- ", "D", "P", "S", "CR"])
    def test_credit_earning_grades(self, grade) -> None:
        assert earns_credit(grade)

    @pytest.mark.parametrize("grade", ["F", "W", "I", "IP", "REG", "INC", "NP", "AU"])
    def test_grades_that_earn_nothing(self, grade) -> None:
        assert not earns_credit(grade)

    @pytest.mark.parametrize("grade", ["WF", "NR", "Z", "X"])
    def test_unknown_grades_fail_closed(self, grade) -> None:
        assert not is_recognised(grade)
        assert not earns_credit(grade)

    def test_transfer_bucket_subject_uses_leading_letters(self, morgan) -> None:
        assert subject_of(morgan, "COSC116TR") == "COSC"
        assert subject_of(morgan, "COSC111") == "COSC"


# --------------------------------------------------------------------------- #
# Item 8: in-progress
# --------------------------------------------------------------------------- #
class TestInProgressIsNeverEarned:
    """Regression: REG and INC were counted as earned AND in progress at once."""

    @pytest.mark.parametrize("grade", ["IP", "REG", "INC", "ip"])
    def test_in_progress_grades_are_not_earned(self, morgan, grade) -> None:
        result = run_audit(morgan, rec(row("COSC111", grade, 4)))
        assert result.total_credits_earned == 0
        assert result.total_credits_in_progress == 4
        assert block(result, "major_required_courses").courses_applied == 0

    def test_in_progress_counts_in_neither_gpa(self, morgan) -> None:
        result = run_audit(morgan, rec(row("COSC111", "A", 4), row("COSC112", "IP", 4)))
        assert result.gpa.cumulative == Decimal("4.00")


# --------------------------------------------------------------------------- #
# Item 7: repeated courses
# --------------------------------------------------------------------------- #
class TestRetakes:
    def test_retake_earns_credit_once(self, morgan) -> None:
        """Regression: F-then-B and C-then-B both summed every passing row."""
        result = run_audit(
            morgan,
            rec(
                row("COSC111", "F", 4),
                row("COSC111", "B", 4),
                row("ENGL101", "C"),
                row("ENGL101", "B"),
            ),
        )
        assert result.total_credits_earned == Decimal(7)

    def test_passing_twice_is_noted(self, morgan) -> None:
        result = run_audit(morgan, rec(row("ENGL101", "C"), row("ENGL101", "B")))
        assert any("ENGL101" in n and "once" in n for n in result.notes)

    def test_failed_then_passed_is_not_a_double_pass(self, morgan) -> None:
        result = run_audit(morgan, rec(row("COSC111", "F", 4), row("COSC111", "B", 4)))
        assert not any("Passed more than once" in n for n in result.notes)

    def test_transfer_bucket_codes_are_not_collapsed(self, morgan) -> None:
        """Fourteen transfer courses can share COSC116TR. Each is real credit."""
        rows = [
            row("COSC116TR", "B", 3, institution="CCBC", term=f"Fall {y}")
            for y in range(2018, 2022)
        ]
        result = run_audit(morgan, rec(*rows))
        assert result.total_credits_earned == Decimal(12)

    def test_retake_fixes_a_grade_below_the_block_minimum(self, morgan) -> None:
        result = run_audit(morgan, rec(row("COSC111", "D", 4), row("COSC111", "B", 4)))
        major = block(result, "major_required_courses")
        assert [a.grade for a in major.applied] == ["B"]

    @pytest.mark.parametrize("strategy", list(MatcherStrategy))
    def test_retake_never_fills_two_slots(self, demo, strategy) -> None:
        # COSC241 is in core_cs_lower AND systems_concentration in the demo catalog.
        result = run_audit(demo, rec(row("COSC241", "B"), row("COSC241", "A")), strategy)
        codes = [a.course.code for b in result.blocks for a in b.applied]
        assert codes.count("COSC241") == 1


# --------------------------------------------------------------------------- #
# Item 10: GPA
# --------------------------------------------------------------------------- #
class TestGpa:
    def test_cumulative_gpa_includes_courses_the_catalog_does_not_name(self, morgan) -> None:
        """Regression: cumulative GPA silently skipped every non-catalog course."""
        result = run_audit(morgan, rec(row("COSC111", "A", 3), row("MUSC101", "F", 3)))
        assert result.gpa.cumulative == Decimal("2.00")

    def test_major_gpa_counts_non_catalog_major_courses(self, morgan) -> None:
        result = run_audit(morgan, rec(row("COSC111", "A", 3), row("COSC299", "C", 3)))
        assert result.gpa.major == Decimal("3.00")

    @pytest.mark.parametrize("grade", ["W", "I", "P", "IP", "AU", "NP"])
    def test_non_letter_grades_are_excluded(self, morgan, grade) -> None:
        result = run_audit(morgan, rec(row("COSC111", "B", 4), row("COSC112", grade, 4)))
        assert result.gpa.cumulative == Decimal("3.00")

    def test_no_repeats_no_transfer_means_no_range(self, morgan) -> None:
        result = run_audit(morgan, rec(row("COSC111", "B", 4), row("COSC112", "C", 4)))
        assert result.gpa.cumulative_low == result.gpa.cumulative_high == result.gpa.cumulative
        assert result.gpa.notes == []

    def test_repeat_policy_unknown_gives_a_range(self, morgan) -> None:
        result = run_audit(morgan, rec(row("COSC111", "F", 4), row("COSC111", "A", 4)))
        assert result.gpa.cumulative == Decimal("2.00")  # every attempt
        assert result.gpa.cumulative_low == Decimal("0.00")
        assert result.gpa.cumulative_high == Decimal("4.00")
        assert any("COSC111" in n for n in result.gpa.notes)

    def test_gpa_block_straddling_the_minimum_needs_an_advisor(self, morgan) -> None:
        """2.0 with both attempts, 4.0 with replacement: Morgan's rule decides."""
        result = run_audit(morgan, rec(row("COSC111", "F", 4), row("COSC111", "A", 4)))
        gpa = block(result, "cumulative_gpa")
        assert gpa.status is BlockStatus.NEEDS_ADVISOR
        assert gpa.note and "repeated" in gpa.note
        assert not result.gpa.meets_requirements

    def test_gpa_block_met_under_every_reading_is_satisfied(self, morgan) -> None:
        result = run_audit(morgan, rec(row("COSC111", "B", 4), row("COSC111", "A", 4)))
        assert block(result, "cumulative_gpa").status is BlockStatus.SATISFIED

    def test_gpa_block_missed_under_every_reading_is_unmet(self, morgan) -> None:
        result = run_audit(morgan, rec(row("COSC111", "F", 4), row("COSC111", "D", 4)))
        assert block(result, "cumulative_gpa").status is BlockStatus.UNMET

    def test_transfer_grades_widen_the_range(self, morgan) -> None:
        figures = gpa_figures(
            morgan,
            [row("COSC111", "A", 4, institution=HOME), row("ENGL101", "D", 3, institution="CCBC")],
            "cumulative",
        )
        assert figures.transfer_graded == 1
        assert figures.high == Decimal("4.00")
        assert figures.low < figures.high

    def test_unconfirmed_rows_never_reach_the_gpa(self, morgan) -> None:
        result = run_audit(
            morgan,
            rec(
                row("COSC111", "A", 4),
                row("COSC112", "F", 4, provenance=Provenance.UNVERIFIED_EXTRACTION),
            ),
        )
        assert result.gpa.cumulative == Decimal("4.00")


# --------------------------------------------------------------------------- #
# Unknown and pass grades
# --------------------------------------------------------------------------- #
class TestUnknownAndPassGrades:
    def test_unknown_grade_earns_nothing_and_is_noted(self, morgan) -> None:
        result = run_audit(morgan, rec(row("MUSC101", "WF", 3), row("COSC111", "A", 4)))
        assert result.total_credits_earned == Decimal(4)
        assert any("WF" in n for n in result.notes)

    def test_pass_grade_earns_credit(self, morgan) -> None:
        result = run_audit(morgan, rec(row("UNIV101", "P", 1)))
        assert result.total_credits_earned == Decimal(1)

    def test_pass_grade_holds_the_block_for_an_advisor(self, morgan) -> None:
        """Whether P meets a minimum grade is not encoded. UNMET would be a guess."""
        result = run_audit(morgan, rec(row("UNIV101", "P", 1)))
        seminar = block(result, "first_year_seminar")
        assert seminar.status is BlockStatus.NEEDS_ADVISOR
        assert "UNIV101" in seminar.note

    def test_pass_grade_does_not_override_a_satisfied_block(self, morgan) -> None:
        result = run_audit(
            morgan, rec(row("ENGL101", "B"), row("ENGL102", "A"), row("ENGL111", "P"))
        )
        assert block(result, "gen_ed_composition").status is BlockStatus.SATISFIED

    def test_standalone_each_of_applies_the_same_hold(self, morgan) -> None:
        comp = next(b for b in morgan.requirement_blocks if b.id == "gen_ed_composition")
        result = evaluate_each_of(morgan, comp, rec(row("ENGL101", "A"), row("ENGL102", "P")))
        assert result.status is BlockStatus.NEEDS_ADVISOR

    def test_unconfirmed_pass_grade_holds_nothing(self, morgan) -> None:
        result = run_audit(
            morgan, rec(row("UNIV101", "P", 1, provenance=Provenance.UNVERIFIED_EXTRACTION))
        )
        assert block(result, "first_year_seminar").status is BlockStatus.UNMET


# --------------------------------------------------------------------------- #
# Item 9: residency
# --------------------------------------------------------------------------- #
RESIDENCY = [
    {"id": "res", "name": "Residency", "type": "residency", "min_credits_at_institution": 6}
]


class TestResidency:
    def test_unknown_institution_needs_an_advisor(self) -> None:
        program = tiny_program(RESIDENCY)
        result = run_audit(program, rec(row("AAA100", "A"), row("AAA200", "A", institution=HOME)))
        assert block(result, "res").status is BlockStatus.NEEDS_ADVISOR

    def test_failed_and_in_progress_courses_are_not_residence_credit(self) -> None:
        """Regression: an F at Morgan counted toward residency."""
        program = tiny_program(RESIDENCY)
        result = run_audit(
            program,
            rec(
                row("AAA100", "A", institution=HOME),
                row("AAA200", "F", institution=HOME),
                row("AAA300", "IP", institution=HOME),
            ),
        )
        res = block(result, "res")
        assert res.credits_applied == Decimal(3)
        assert res.status is BlockStatus.IN_PROGRESS

    def test_courses_outside_the_catalog_still_count_as_residence(self) -> None:
        program = tiny_program(RESIDENCY)
        result = run_audit(
            program, rec(row("AAA100", "A", institution=HOME), row("ZZZ999", "B", institution=HOME))
        )
        assert block(result, "res").status is BlockStatus.SATISFIED

    def test_transfer_credit_is_not_residence(self) -> None:
        program = tiny_program(RESIDENCY)
        result = run_audit(
            program,
            rec(row("AAA100", "A", institution=HOME), row("AAA200", "A", institution="CCBC")),
        )
        res = block(result, "res")
        assert res.credits_applied == Decimal(3)
        assert res.status is not BlockStatus.SATISFIED

    def test_retake_is_residence_credit_once(self) -> None:
        program = tiny_program(RESIDENCY)
        result = run_audit(
            program,
            rec(row("AAA100", "C", institution=HOME), row("AAA100", "A", institution=HOME)),
        )
        assert block(result, "res").credits_applied == Decimal(3)

    def test_morgan_encodes_no_residency_block(self, morgan) -> None:
        """Q4 is open (all vs two-thirds). The engine must not invent one."""
        assert not any(b.type == "residency" for b in morgan.requirement_blocks)


# --------------------------------------------------------------------------- #
# Item 6: transfer credit
# --------------------------------------------------------------------------- #
class TestTransferCredit:
    def test_transfer_row_with_a_catalog_code_satisfies_its_requirement(self, morgan) -> None:
        """Equated transfer credit (TRB -> B) is still the course."""
        result = run_audit(
            morgan, rec(row("ENGL101", "B", institution="CCBC"), row("ENGL102", "B"))
        )
        assert block(result, "gen_ed_composition").status is BlockStatus.SATISFIED

    def test_transfer_bucket_credit_is_earned_but_never_applied(self, morgan) -> None:
        result = run_audit(morgan, rec(row("COSC116TR", "A", 3, institution="CCBC")))
        assert result.total_credits_earned == Decimal(3)
        assert result.total_credits_applied == 0
        assert [c.code for c in result.outside_catalog] == ["COSC116TR"]
        assert result.outside_catalog[0].institution == "CCBC"

    def test_transfer_below_block_minimum_does_not_apply(self, morgan) -> None:
        result = run_audit(morgan, rec(row("COSC111", "D", 4, institution="CCBC")))
        assert block(result, "major_required_courses").courses_applied == 0


# --------------------------------------------------------------------------- #
# Items 11/12: overlap and double counting
# --------------------------------------------------------------------------- #
CREDITS_FIRST = [
    {
        "id": "electives",
        "name": "Electives",
        "type": "credits_from",
        "credits_required": 3,
        "course_filter": {"subject": "AAA"},
    },
    {"id": "core", "name": "Core", "type": "all_of", "courses": ["AAA100"]},
]


class TestOverlapAndDoubleCounting:
    def test_optimal_credits_from_does_not_steal_a_slot_course(self) -> None:
        """Regression: credits_from listed BEFORE a slot block spent its course."""
        program = tiny_program(CREDITS_FIRST)
        record = rec(row("AAA100", "A"), row("AAA200", "A"))
        result = run_audit(program, record, MatcherStrategy.OPTIMAL_BIPARTITE)
        assert block(result, "core").status is BlockStatus.SATISFIED
        assert block(result, "electives").status is BlockStatus.SATISFIED

    def test_greedy_keeps_its_documented_under_report(self) -> None:
        """Greedy is the honest baseline and may under-report; it must not over-report."""
        program = tiny_program(CREDITS_FIRST)
        record = rec(row("AAA100", "A"), row("AAA200", "A"))
        result = run_audit(program, record, MatcherStrategy.GREEDY)
        applied = [a.course.code for b in result.blocks for a in b.applied]
        assert len(applied) == len(set(applied))

    @pytest.mark.parametrize("strategy", list(MatcherStrategy))
    def test_applied_credit_never_exceeds_distinct_course_credit(self, demo, strategy) -> None:
        record = rec(
            row("COSC241", "A"),
            row("COSC349", "A"),
            row("COSC350", "A"),
            row("COSC350", "B"),
            row("PHYS211", "A", 4),
            row("MATH312", "A"),
        )
        result = run_audit(demo, record, strategy)
        applied = [a.course.code for b in result.blocks for a in b.applied]
        assert len(applied) == len(set(applied))
        distinct = {c.code: c for c in record.completed}
        ceiling = sum(demo.course(code).credits for code in distinct)
        assert result.total_credits_applied <= ceiling

    def test_optimal_ignores_an_unconfirmed_better_attempt(self, morgan) -> None:
        """Regression: an unconfirmed A became the representative and hid a confirmed B."""
        record = rec(
            row("COSC111", "B", 4),
            row("COSC111", "A", 4, provenance=Provenance.UNVERIFIED_EXTRACTION),
        )
        assert representative_indices(record.completed) == {"COSC111": 0}
        result = run_audit(morgan, record, MatcherStrategy.OPTIMAL_BIPARTITE)
        assert block(result, "major_required_courses").courses_applied == 1


# --------------------------------------------------------------------------- #
# Item 14: graduation safeguards
# --------------------------------------------------------------------------- #
class TestGraduationSafeguards:
    def _everything_encoded(self, morgan) -> StudentRecord:
        rows = [
            row(c, "A", morgan.course(c).credits)
            for c in (
                "COSC111 COSC112 COSC220 COSC243 COSC281 COSC349 COSC351 COSC352 COSC354 "
                "COSC458 COSC459 COSC490 MATH241 MATH242 MATH312 MATH331 COSC201 ENGL101 "
                "ENGL102 PHIL109 HLTH103 AFST350 UNIV101"
            ).split()
        ]
        rows += [row(f"FILL{i:03d}", "A", 10) for i in range(6)]
        return rec(*rows)

    def test_every_encoded_block_met_is_still_not_eligible(self, morgan) -> None:
        result = run_audit(morgan, self._everything_encoded(morgan))
        assert all(b.is_complete for b in result.blocks)
        assert result.total_credits_earned >= 120
        assert result.is_graduation_eligible is False
        assert any("partly encoded" in r for r in result.graduation_blockers)

    def test_blockers_name_each_unfinished_block(self, morgan) -> None:
        result = run_audit(morgan, rec(row("COSC111", "A", 4)))
        assert any(r.startswith("Supporting Courses") for r in result.graduation_blockers)

    def test_a_fully_encoded_program_can_say_yes(self) -> None:
        """The safeguards must not make eligibility unreachable on a complete catalog."""
        program = tiny_program(
            [
                {
                    "id": "core",
                    "name": "Core",
                    "type": "all_of",
                    "courses": ["AAA100", "AAA200", "AAA300"],
                },
                {"id": "gpa", "name": "GPA", "type": "gpa", "min_gpa": 2.0},
            ]
        )
        result = run_audit(program, rec(row("AAA100", "A"), row("AAA200", "B"), row("AAA300", "C")))
        assert result.coverage.is_complete
        assert result.graduation_blockers == []
        assert result.is_graduation_eligible is True

    def test_one_missing_course_on_a_complete_catalog_says_no(self) -> None:
        program = tiny_program(
            [
                {
                    "id": "core",
                    "name": "Core",
                    "type": "all_of",
                    "courses": ["AAA100", "AAA200", "AAA300"],
                }
            ]
        )
        result = run_audit(
            program, rec(row("AAA100", "A"), row("AAA200", "B"), row("AAA300", "IP"))
        )
        assert result.is_graduation_eligible is False
        assert any(r.startswith("Core") for r in result.graduation_blockers)

    def test_policy_dependent_gpa_blocks_graduation(self, demo) -> None:
        result = run_audit(demo, rec(row("COSC111", "F", 4), row("COSC111", "A", 4)))
        assert "The GPA minimums are not confirmed as met." in result.graduation_blockers
