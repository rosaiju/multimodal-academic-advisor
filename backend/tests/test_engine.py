"""The degree audit engine, end to end.

These tests run real student records against both catalogs: the fictional demo
program (complete, built to exercise the matcher) and the real Morgan catalog
(deliberately incomplete, built to refuse to guess).

The most important assertions here are the ones about what the engine REFUSES to
say. An audit that over-reports progress is worse than no audit.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.audit.engine import run_audit
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.loader import load_program
from app.catalog.schema import Program
from app.schemas.audit import BlockStatus, MatcherStrategy
from app.schemas.provenance import Provenance

CATALOG_DIR = Path(__file__).resolve().parents[2] / "data" / "catalog"
MORGAN = CATALOG_DIR / "morgan_cosc_bs_2026_2028.yaml"
DEMO = CATALOG_DIR / "demo_university_cs.yaml"


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(MORGAN)


@pytest.fixture(scope="module")
def demo() -> Program:
    return load_program(DEMO)


def record(*rows, student_id: str = "s1") -> StudentRecord:
    """rows are (code, grade) or (code, grade, credits) or (code, grade, credits, institution)."""
    completed = []
    for row in rows:
        code, grade = row[0], row[1]
        credits = Decimal(row[2]) if len(row) > 2 else Decimal(3)
        institution = row[3] if len(row) > 3 else None
        completed.append(
            CompletedCourse(
                code=code,
                term="Fall 2026",
                grade=grade,
                credits=credits,
                institution=institution,
            )
        )
    return StudentRecord(student_id=student_id, completed=completed)


def block(result, block_id):
    return next(b for b in result.blocks if b.block_id == block_id)


class TestRefusesToOverreport:
    """What the engine will not say, which matters more than what it will."""

    def test_incomplete_catalog_is_never_graduation_eligible(self, morgan) -> None:
        """Even a student who satisfied every encoded block has not graduated.

        The Morgan catalog omits every disputed requirement rather than guessing it.
        Saying "you can graduate" off a partial catalog is the worst possible failure
        here, so it fails closed regardless of the blocks.
        """
        result = run_audit(morgan, record(("COSC111", "A", 4)))
        assert result.coverage is not None
        assert not result.coverage.is_complete
        assert not result.is_graduation_eligible

    def test_coverage_reports_the_gap_honestly(self, morgan) -> None:
        result = run_audit(morgan, record())
        coverage = result.coverage
        assert coverage.blocks_encoded == 6
        assert coverage.courses_in_catalog == 56
        assert coverage.courses_satisfying_no_block == 34
        assert "PARTIAL" in coverage.summary
        assert "absent, not failed" in coverage.summary

    def test_unimplemented_strategy_raises_rather_than_silently_degrading(self, morgan) -> None:
        with pytest.raises(NotImplementedError, match="not implemented yet"):
            run_audit(morgan, record(), strategy=MatcherStrategy.OPTIMAL_BIPARTITE)

    def test_residency_needs_an_advisor_when_the_record_is_silent(self, demo) -> None:
        """Assuming courses were taken in residence would pass a transfer student."""
        result = run_audit(demo, record(("COSC111", "A", 4), ("MATH241", "A", 4)))
        assert block(result, "residency").status is BlockStatus.NEEDS_ADVISOR

    def test_residency_is_computed_when_the_record_says_where(self, demo) -> None:
        rows = [("COSC111", "A", 4, "Demo University"), ("MATH241", "A", 4, "Elsewhere CC")]
        result = run_audit(demo, record(*rows))
        residency = block(result, "residency")
        assert residency.status is not BlockStatus.NEEDS_ADVISOR
        assert residency.credits_applied == Decimal(4), "only the in-residence course counts"


class TestNoDoubleCounting:
    def test_a_course_is_applied_to_at_most_one_block(self, morgan) -> None:
        result = run_audit(
            morgan,
            record(
                ("COSC111", "A", 4),
                ("COSC112", "A", 4),
                ("ENGL101", "A", 3),
                ("ENGL102", "A", 3),
                ("MATH241", "B", 4),
                ("UNIV101", "A", 1),
            ),
        )
        applied = [a.course.code for b in result.blocks for a in b.applied]
        assert len(applied) == len(set(applied)), f"duplicate assignment: {applied}"

    def test_total_credits_applied_equals_the_sum_of_applied_courses(self, morgan) -> None:
        result = run_audit(morgan, record(("COSC111", "A", 4), ("ENGL101", "A", 3)))
        expected = sum((a.credits_applied for b in result.blocks for a in b.applied), Decimal(0))
        assert result.total_credits_applied == expected

    def test_gpa_blocks_do_not_consume_courses(self, morgan) -> None:
        """A course counts toward GPA and toward a requirement. That is not double counting."""
        result = run_audit(morgan, record(("COSC111", "A", 4)))
        assert block(result, "cumulative_gpa").applied == []
        assert block(result, "major_required_courses").courses_applied == 1


class TestBlockEvaluation:
    def test_all_of_tracks_progress_and_names_what_is_missing(self, morgan) -> None:
        result = run_audit(morgan, record(("COSC111", "A", 4), ("COSC112", "A", 4)))
        major = block(result, "major_required_courses")
        assert major.status is BlockStatus.IN_PROGRESS
        assert major.courses_applied == 2
        assert major.courses_required == 12
        missing = {c.code for c in major.still_needed}
        assert "COSC459" in missing and "COSC111" not in missing

    def test_all_of_is_satisfied_when_every_course_is_present(self, demo) -> None:
        result = run_audit(
            demo,
            record(("ENGL101", "A", 3), ("ENGL102", "A", 3)),
        )
        assert block(result, "gen_ed_composition").status is BlockStatus.SATISFIED

    def test_n_of_stops_at_n(self, demo) -> None:
        result = run_audit(
            demo, record(("HIST105", "A", 3), ("PHIL109", "A", 3), ("ECON211", "A", 3))
        )
        humanities = block(result, "gen_ed_humanities")
        assert humanities.courses_required == 2
        assert humanities.courses_applied == 2, "the third course must be left for other blocks"

    def test_credits_from_accumulates_until_satisfied(self, demo) -> None:
        result = run_audit(
            demo,
            record(("COSC349", "A", 3), ("COSC350", "A", 3), ("COSC370", "A", 3)),
        )
        electives = block(result, "cs_electives_upper")
        assert electives.credits_required == Decimal(9)
        assert electives.credits_applied <= Decimal(9)

    def test_each_of_is_wired_into_the_engine(self, morgan) -> None:
        result = run_audit(morgan, record(("ENGL101", "A", 3), ("ENGL111", "A", 3)))
        comp = block(result, "gen_ed_composition")
        assert comp.status is BlockStatus.IN_PROGRESS, "two Part A courses is not both parts"
        assert comp.courses_applied == 1

    def test_retake_does_not_satisfy_a_requirement_twice(self, morgan) -> None:
        result = run_audit(morgan, record(("COSC111", "C", 4), ("COSC111", "A", 4)))
        major = block(result, "major_required_courses")
        assert major.courses_applied == 1


class TestGrades:
    def test_cumulative_and_major_gpa(self, morgan) -> None:
        result = run_audit(
            morgan, record(("COSC111", "A", 4), ("COSC112", "B", 4), ("ENGL101", "C", 3))
        )
        # cumulative: (4*4 + 3*4 + 2*3) / 11 = 34/11 = 3.09
        assert result.gpa.cumulative == Decimal("3.09")
        # major: COSC only -> (4*4 + 3*4) / 8 = 3.50
        assert result.gpa.major == Decimal("3.50")

    def test_gpa_is_none_not_zero_when_nothing_is_gradeable(self, morgan) -> None:
        """None means unknown. Reporting 0.0 would say the student is failing."""
        result = run_audit(morgan, record(("COSC111", "W", 4)))
        assert result.gpa.cumulative is None
        assert not result.gpa.meets_requirements

    def test_gpa_block_fails_below_the_minimum(self, morgan) -> None:
        result = run_audit(morgan, record(("COSC111", "D", 4), ("COSC112", "D", 4)))
        assert block(result, "cumulative_gpa").status is BlockStatus.UNMET

    def test_grade_below_block_minimum_does_not_apply(self, morgan) -> None:
        """The major block requires C or better, so a D does not count toward it."""
        result = run_audit(morgan, record(("COSC111", "D", 4)))
        assert block(result, "major_required_courses").courses_applied == 0


class TestProvenance:
    def test_unconfirmed_extraction_never_reaches_the_audit(self, morgan) -> None:
        rec = StudentRecord(
            student_id="s1",
            completed=[
                CompletedCourse(
                    code="COSC111",
                    term="Fall 2026",
                    grade="A",
                    credits=Decimal(4),
                    provenance=Provenance.UNVERIFIED_EXTRACTION,
                )
            ],
        )
        result = run_audit(morgan, rec)
        assert result.total_credits_applied == Decimal(0)
        assert result.gpa.cumulative is None
        assert result.unapplied == []

    def test_student_confirmed_extraction_counts(self, morgan) -> None:
        rec = StudentRecord(
            student_id="s1",
            completed=[
                CompletedCourse(
                    code="COSC111",
                    term="Fall 2026",
                    grade="A",
                    credits=Decimal(4),
                    provenance=Provenance.STUDENT_CONFIRMED,
                )
            ],
        )
        result = run_audit(morgan, rec)
        assert result.total_credits_applied == Decimal(4)


class TestResultShape:
    def test_unapplied_lists_courses_no_block_claimed(self, morgan) -> None:
        """COSC470 is a real catalog course in no encoded block - an unencoded
        elective group would claim it, which is exactly what coverage warns about."""
        result = run_audit(morgan, record(("COSC470", "A", 3)))
        assert [a.course.code for a in result.unapplied] == ["COSC470"]

    def test_unknown_course_is_ignored_entirely(self, morgan) -> None:
        result = run_audit(morgan, record(("NOTREAL999", "A", 3)))
        assert result.unapplied == []
        assert result.total_credits_applied == Decimal(0)

    def test_credits_come_from_the_catalog_not_the_transcript(self, morgan) -> None:
        """COSC111 is 4 credits in the catalog; a transcript claiming 99 does not win."""
        result = run_audit(morgan, record(("COSC111", "A", 99)))
        assert result.total_credits_applied == Decimal(4)

    def test_carries_program_identity_and_strategy(self, morgan) -> None:
        result = run_audit(morgan, record())
        assert result.program_id == "morgan_cosc_bs_2026_2028"
        assert result.catalog_year == "2026-2028"
        assert result.strategy is MatcherStrategy.GREEDY
        assert result.provenance is Provenance.VERIFIED

    def test_critical_path_is_absent_not_faked(self, morgan) -> None:
        """Not yet computed. None is honest; a made-up number would not be."""
        result = run_audit(morgan, record())
        assert result.critical_path_terms is None
        assert result.critical_path == []

    def test_percent_complete_is_against_the_full_degree(self, morgan) -> None:
        result = run_audit(morgan, record(("COSC111", "A", 4), ("ENGL101", "A", 3)))
        assert 0 < result.percent_complete < 10

    def test_empty_record_produces_a_clean_unmet_audit(self, morgan) -> None:
        result = run_audit(morgan, record())
        assert result.total_credits_applied == Decimal(0)
        assert result.unapplied == []
        course_blocks = [b for b in result.blocks if b.courses_required]
        assert all(b.status is BlockStatus.UNMET for b in course_blocks)
