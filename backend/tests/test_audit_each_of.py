"""End-to-end engine evaluation of `each_of` blocks.

The central test is `test_two_from_the_same_group_does_not_satisfy`. That is the
exact wrong answer the old `n_of` encoding produced: a student who took both Part A
options was told Composition was done, when they had not started Part B.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.audit.each_of import evaluate_each_of
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.loader import load_program
from app.catalog.schema import Course, EachOfBlock, Program
from app.schemas.audit import BlockStatus
from app.schemas.provenance import Provenance

CATALOG = Path(__file__).resolve().parents[2] / "data" / "catalog" / "morgan_cosc_bs_2026_2028.yaml"


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(CATALOG)


@pytest.fixture(scope="module")
def composition(morgan: Program) -> EachOfBlock:
    block = next(b for b in morgan.requirement_blocks if b.id == "gen_ed_composition")
    assert isinstance(block, EachOfBlock)
    return block


def record(
    *courses: tuple[str, str],
    provenance: Provenance = Provenance.VERIFIED,
) -> StudentRecord:
    return StudentRecord(
        student_id="s1",
        completed=[
            CompletedCourse(
                code=code,
                term="Fall 2026",
                grade=grade,
                credits=Decimal(3),
                provenance=provenance,
            )
            for code, grade in courses
        ],
    )


class TestTheCoreRule:
    """One course from each group satisfies; two from one group does not."""

    def test_one_from_each_group_satisfies(self, morgan, composition) -> None:
        result = evaluate_each_of(morgan, composition, record(("ENGL101", "A"), ("ENGL102", "B")))
        assert result.status is BlockStatus.SATISFIED
        assert result.is_complete
        assert result.courses_applied == 2
        assert result.courses_required == 2
        assert {a.course.code for a in result.applied} == {"ENGL101", "ENGL102"}
        assert result.still_needed == []

    def test_two_from_the_same_group_does_not_satisfy(self, morgan, composition) -> None:
        """ENGL101 + ENGL111 are both Part A. Part B is untouched.

        Under the previous n_of encoding this returned SATISFIED. That was a wrong
        graduation answer, and it is the reason each_of exists.
        """
        result = evaluate_each_of(morgan, composition, record(("ENGL101", "A"), ("ENGL111", "A")))
        assert result.status is BlockStatus.IN_PROGRESS
        assert not result.is_complete
        assert result.courses_applied == 1, "only one Part A course may be applied"
        assert {c.code for c in result.still_needed} == {"ENGL102", "ENGL112"}

    def test_two_from_the_other_group_also_does_not_satisfy(self, morgan, composition) -> None:
        """Symmetry: the rule is not accidentally one-directional."""
        result = evaluate_each_of(morgan, composition, record(("ENGL102", "A"), ("ENGL112", "A")))
        assert result.status is BlockStatus.IN_PROGRESS
        assert result.courses_applied == 1
        assert {c.code for c in result.still_needed} == {"ENGL101", "ENGL111"}

    def test_either_option_within_a_group_works(self, morgan, composition) -> None:
        """The honors variants satisfy their parts just as the standard courses do."""
        result = evaluate_each_of(morgan, composition, record(("ENGL111", "B"), ("ENGL112", "B")))
        assert result.status is BlockStatus.SATISFIED

    def test_no_coursework_is_unmet_not_in_progress(self, morgan, composition) -> None:
        result = evaluate_each_of(morgan, composition, record())
        assert result.status is BlockStatus.UNMET
        assert result.courses_applied == 0
        assert len(result.still_needed) == 4


class TestNoDoubleCounting:
    def test_a_course_is_applied_at_most_once(self, morgan, composition) -> None:
        result = evaluate_each_of(
            morgan, composition, record(("ENGL101", "A"), ("ENGL102", "A"), ("ENGL112", "A"))
        )
        codes = [a.course.code for a in result.applied]
        assert len(codes) == len(set(codes))
        assert len(codes) == 2, "two groups can consume at most two courses"

    def test_overlapping_groups_reassign_rather_than_fail(self, morgan) -> None:
        """A course eligible for two groups must be given up if that satisfies both.

        Greedy assignment fails here: if group 1 takes SHARED first, group 2 has
        nothing left, and the block is reported unmet though the student has done it.
        """
        program = Program(
            program_id="t",
            program="T",
            institution="T",
            catalog_year="2026-2028",
            total_credits_required=Decimal(120),
            courses=[Course(code=c, title=c, credits=Decimal(3)) for c in ("SHARED", "ONLYA")],
            requirement_blocks=[
                EachOfBlock(id="b", name="B", groups=[["SHARED", "ONLYA"], ["SHARED"]])
            ],
        )
        block = program.requirement_blocks[0]
        result = evaluate_each_of(program, block, record(("SHARED", "A"), ("ONLYA", "A")))
        assert result.status is BlockStatus.SATISFIED
        assert {a.course.code for a in result.applied} == {"SHARED", "ONLYA"}


class TestGrades:
    def test_grade_below_the_block_minimum_does_not_count(self, morgan, composition) -> None:
        """Composition carries min_grade C, so a D does not satisfy Part A."""
        assert composition.min_grade == "C"
        result = evaluate_each_of(morgan, composition, record(("ENGL101", "D"), ("ENGL102", "A")))
        assert result.status is BlockStatus.IN_PROGRESS
        assert {a.course.code for a in result.applied} == {"ENGL102"}

    def test_failing_grade_never_counts(self, morgan, composition) -> None:
        result = evaluate_each_of(morgan, composition, record(("ENGL101", "F"), ("ENGL102", "F")))
        assert result.status is BlockStatus.UNMET

    def test_withdrawal_never_counts(self, morgan, composition) -> None:
        result = evaluate_each_of(morgan, composition, record(("ENGL101", "W"), ("ENGL102", "A")))
        assert result.courses_applied == 1


class TestProvenance:
    def test_unconfirmed_extraction_cannot_satisfy_a_requirement(self, morgan, composition) -> None:
        """An AI-parsed transcript must not move a degree audit until confirmed."""
        result = evaluate_each_of(
            morgan,
            composition,
            record(
                ("ENGL101", "A"),
                ("ENGL102", "A"),
                provenance=Provenance.UNVERIFIED_EXTRACTION,
            ),
        )
        assert result.status is BlockStatus.UNMET
        assert result.applied == []

    def test_student_confirmed_extraction_does_count(self, morgan, composition) -> None:
        result = evaluate_each_of(
            morgan,
            composition,
            record(("ENGL101", "A"), ("ENGL102", "A"), provenance=Provenance.STUDENT_CONFIRMED),
        )
        assert result.status is BlockStatus.SATISFIED

    def test_applied_courses_keep_their_provenance(self, morgan, composition) -> None:
        result = evaluate_each_of(
            morgan,
            composition,
            record(("ENGL101", "A"), ("ENGL102", "A"), provenance=Provenance.STUDENT_CONFIRMED),
        )
        assert all(a.provenance is Provenance.STUDENT_CONFIRMED for a in result.applied)


class TestResultShape:
    def test_credits_reported_from_the_catalog_not_the_transcript(
        self, morgan, composition
    ) -> None:
        """Catalog credits are authoritative; a transcript row does not redefine them."""
        result = evaluate_each_of(morgan, composition, record(("ENGL101", "A"), ("ENGL102", "A")))
        assert result.credits_applied == Decimal(6)
        assert result.credits_required == Decimal(6)

    def test_carries_the_catalog_note_through(self, morgan, composition) -> None:
        result = evaluate_each_of(morgan, composition, record())
        assert result.note == composition.note

    def test_unknown_course_on_the_transcript_is_ignored(self, morgan, composition) -> None:
        result = evaluate_each_of(
            morgan, composition, record(("NOTREAL999", "A"), ("ENGL101", "A"), ("ENGL102", "A"))
        )
        assert result.status is BlockStatus.SATISFIED
        assert len(result.applied) == 2

    def test_advisor_flag_overrides_satisfied(self, morgan) -> None:
        """A block the catalog defers to a human is never auto-satisfied."""
        program = Program(
            program_id="t2",
            program="T",
            institution="T",
            catalog_year="2026-2028",
            total_credits_required=Decimal(120),
            courses=[Course(code=c, title=c, credits=Decimal(3)) for c in ("A1", "B1")],
            requirement_blocks=[
                EachOfBlock(
                    id="b",
                    name="B",
                    groups=[["A1"], ["B1"]],
                    advisor_approval_required=True,
                )
            ],
        )
        block = program.requirement_blocks[0]
        result = evaluate_each_of(program, block, record(("A1", "A"), ("B1", "A")))
        assert result.status is BlockStatus.NEEDS_ADVISOR
