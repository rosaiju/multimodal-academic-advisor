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
from app.audit.planning import build_plan
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

    def test_both_strategies_are_implemented(self, morgan) -> None:
        """Both run. Their divergence is covered in tests/test_optimal_matcher.py."""
        for strategy in (MatcherStrategy.GREEDY, MatcherStrategy.OPTIMAL_BIPARTITE):
            result = run_audit(morgan, record(("COSC111", "A", 4)), strategy=strategy)
            assert result.strategy is strategy

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

    def test_percent_complete_is_withheld_on_a_partial_catalog(self, morgan) -> None:
        """It used to divide block-scoped credit by the whole degree's credits.

        Morgan's catalog is deliberately incomplete, so no honest degree percentage
        exists. None forces a caller to say so instead of printing a number that
        reads as completion.
        """
        result = run_audit(morgan, record(("COSC111", "A", 4), ("ENGL101", "A", 3)))
        assert result.coverage is not None and not result.coverage.is_complete
        assert result.percent_complete is None

    def test_credit_progress_is_reported_even_when_percent_is_not(self, morgan) -> None:
        """Earned against required needs no requirement blocks to be meaningful."""
        result = run_audit(morgan, record(("COSC111", "A", 4), ("ENGL101", "A", 3)))
        assert result.total_credits_earned == Decimal(7)
        assert 0 < result.credit_progress_percent < 10

    def test_empty_record_produces_a_clean_unmet_audit(self, morgan) -> None:
        result = run_audit(morgan, record())
        assert result.total_credits_applied == Decimal(0)
        assert result.unapplied == []
        course_blocks = [b for b in result.blocks if b.courses_required]
        assert all(b.status is BlockStatus.UNMET for b in course_blocks)


class TestCreditAccountingSeparatesThreeQuantities:
    """Regression: a student holding 150 credits was shown 54.

    `total_credits_applied` counts only what the matcher placed into an ENCODED
    requirement block. On a deliberately partial catalog that is a small fraction
    of a real record, and it was being presented as the student's credit total and
    divided by the whole degree to make a completion percentage.

    Three quantities, three names:
      earned   - trusted passing credit on the record, catalog or not
      applied  - credit that landed in an encoded block
      outside  - confirmed credit the catalog has no entry for

    All coursework below is invented.
    """

    def _record(self):
        """Two catalog courses and two the catalog has never heard of."""
        return StudentRecord(
            student_id="s",
            completed=[
                CompletedCourse(
                    code="COSC111",
                    term="Fall 2024",
                    grade="A",
                    credits=Decimal(4),
                    provenance=Provenance.STUDENT_CONFIRMED,
                ),
                CompletedCourse(
                    code="ENGL101",
                    term="Fall 2024",
                    grade="A",
                    credits=Decimal(3),
                    provenance=Provenance.STUDENT_CONFIRMED,
                ),
                CompletedCourse(
                    code="PSYC101",
                    term="Fall 2023",
                    grade="A",
                    credits=Decimal(3),
                    provenance=Provenance.STUDENT_CONFIRMED,
                    institution="EXAMPLE COMMUNITY COLLEGE",
                ),
                CompletedCourse(
                    code="COSC116TR",
                    term="Fall 2023",
                    grade="B",
                    credits=Decimal(3),
                    provenance=Provenance.STUDENT_CONFIRMED,
                    institution="EXAMPLE EVALUATION SERVICE",
                ),
            ],
        )

    def test_earned_counts_credit_the_catalog_does_not_know(self, morgan) -> None:
        result = run_audit(morgan, self._record())
        assert result.total_credits_earned == Decimal(13)

    def test_applied_stays_scoped_to_encoded_blocks(self, morgan) -> None:
        """Unchanged meaning - it is the LABEL that was wrong, not this number."""
        result = run_audit(morgan, self._record())
        assert result.total_credits_applied < result.total_credits_earned

    def test_outside_catalog_courses_are_reported_not_dropped(self, morgan) -> None:
        result = run_audit(morgan, self._record())
        codes = {c.code for c in result.outside_catalog}
        assert codes == {"PSYC101", "COSC116TR"}
        assert sum(c.credits for c in result.outside_catalog) == Decimal(6)

    def test_outside_catalog_keeps_the_sending_institution(self, morgan) -> None:
        """Residency questions need it, and it is the only provenance these have."""
        result = run_audit(morgan, self._record())
        psyc = next(c for c in result.outside_catalog if c.code == "PSYC101")
        assert psyc.institution == "EXAMPLE COMMUNITY COLLEGE"

    def test_outside_catalog_credit_is_never_applied_to_a_requirement(self, morgan) -> None:
        """Reported, never counted toward a block. That decision is an evaluator's."""
        result = run_audit(morgan, self._record())
        applied = {a.course.code for b in result.blocks for a in b.applied}
        assert applied.isdisjoint({"PSYC101", "COSC116TR"})

    def test_the_three_buckets_do_not_overlap(self, morgan) -> None:
        result = run_audit(morgan, self._record())
        applied = {a.course.code for b in result.blocks for a in b.applied}
        unapplied = {a.course.code for a in result.unapplied}
        outside = {c.code for c in result.outside_catalog}
        assert applied.isdisjoint(unapplied)
        assert applied.isdisjoint(outside)
        assert unapplied.isdisjoint(outside)

    def test_unconfirmed_credit_is_not_earned(self, morgan) -> None:
        """The provenance gate holds here too."""
        record = StudentRecord(
            student_id="s",
            completed=[
                CompletedCourse(
                    code="PSYC101",
                    term="Fall 2023",
                    grade="A",
                    credits=Decimal(3),
                    provenance=Provenance.UNVERIFIED_EXTRACTION,
                )
            ],
        )
        result = run_audit(morgan, record)
        assert result.total_credits_earned == Decimal(0)
        assert result.outside_catalog == []


class TestInProgressCreditIsCountedSeparately:
    """Regression: `total_credits_in_progress` was hardcoded to zero.

    The field existed and always lied, so a student sitting on twelve credits saw
    none of them anywhere on the dashboard.
    """

    def _record(self):
        return StudentRecord(
            student_id="s",
            completed=[
                CompletedCourse(
                    code="COSC111",
                    term="Fall 2024",
                    grade="A",
                    credits=Decimal(4),
                    provenance=Provenance.STUDENT_CONFIRMED,
                ),
                CompletedCourse(
                    code="COSC490",
                    term="Fall 2026",
                    grade="IP",
                    credits=Decimal(3),
                    provenance=Provenance.STUDENT_CONFIRMED,
                ),
            ],
        )

    def test_in_progress_credit_is_reported(self, morgan) -> None:
        result = run_audit(morgan, self._record())
        assert result.total_credits_in_progress == Decimal(3)

    def test_in_progress_credit_is_not_earned_credit(self, morgan) -> None:
        """The safeguard. Attempted is not earned."""
        result = run_audit(morgan, self._record())
        assert result.total_credits_earned == Decimal(4)

    def test_in_progress_credit_satisfies_nothing(self, morgan) -> None:
        result = run_audit(morgan, self._record())
        applied = {a.course.code for b in result.blocks for a in b.applied}
        assert "COSC490" not in applied


class TestNoDegreePercentageOnAPartialCatalog:
    """Regression: "45% complete" shown to a student with 150 of 120 credits.

    The fraction mixed scopes - credit applied to six encoded blocks over the whole
    degree's 120 credits. No repair of that fraction is honest while requirements
    are missing, so the number is withheld entirely.
    """

    def _record(self):
        return StudentRecord(
            student_id="s",
            completed=[
                CompletedCourse(
                    code="COSC111",
                    term="Fall 2024",
                    grade="A",
                    credits=Decimal(4),
                    provenance=Provenance.STUDENT_CONFIRMED,
                )
            ],
        )

    def test_percent_complete_is_none(self, morgan) -> None:
        result = run_audit(morgan, self._record())
        assert result.coverage is not None and not result.coverage.is_complete
        assert result.percent_complete is None

    def test_credit_progress_is_still_reported(self, morgan) -> None:
        """It needs no requirement blocks - both sides are facts we hold."""
        result = run_audit(morgan, self._record())
        assert result.credit_progress_percent > 0

    def test_credit_progress_may_exceed_one_hundred(self, morgan) -> None:
        """A transfer student can hold more credit than the degree requires."""
        record = StudentRecord(
            student_id="s",
            completed=[
                CompletedCourse(
                    code=f"XXXX{i:03d}",
                    term="Fall 2023",
                    grade="A",
                    credits=Decimal(10),
                    provenance=Provenance.STUDENT_CONFIRMED,
                )
                for i in range(15)
            ],
        )
        result = run_audit(morgan, record)
        assert result.total_credits_earned == Decimal(150)
        assert result.credit_progress_percent > 100

    def test_encoded_requirements_percent_is_scoped_to_encoded_blocks(self, morgan) -> None:
        result = run_audit(morgan, self._record())
        satisfied = sum(1 for b in result.blocks if b.is_complete)
        assert result.encoded_requirements_percent == pytest.approx(
            satisfied / len(result.blocks) * 100
        )

    def test_graduation_still_fails_closed(self, morgan) -> None:
        """Untouched by any of this - an incomplete catalog never says yes."""
        result = run_audit(morgan, self._record())
        assert result.is_graduation_eligible is False


class TestNothingUnderWayIsRecommended:
    """Regression: the plan suggested courses the student was sitting in.

    Recommendations filtered on `passed`, and an in-progress grade is non-passing,
    so a course being taken right now looked exactly like one never attempted.
    Both states disqualify a course from advice, for opposite reasons.
    """

    def _record(self):
        return StudentRecord(
            student_id="s",
            completed=[
                CompletedCourse(
                    code="COSC490",
                    term="Fall 2026",
                    grade="IP",
                    credits=Decimal(3),
                    provenance=Provenance.STUDENT_CONFIRMED,
                ),
                CompletedCourse(
                    code="COSC111",
                    term="Fall 2024",
                    grade="A",
                    credits=Decimal(4),
                    provenance=Provenance.STUDENT_CONFIRMED,
                ),
            ],
        )

    def _plan(self, morgan):
        record = self._record()
        return build_plan(morgan, record, run_audit(morgan, record))

    def test_an_in_progress_course_is_not_recommended(self, morgan) -> None:
        plan = self._plan(morgan)
        assert "COSC490" not in {r.course.code for r in plan.recommended}

    def test_it_is_not_in_the_blocked_list_either(self, morgan) -> None:
        """Blocked means "prerequisites missing", which is a different story."""
        plan = self._plan(morgan)
        assert "COSC490" not in {r.course.code for r in plan.blocked}

    def test_it_is_surfaced_as_under_way(self, morgan) -> None:
        plan = self._plan(morgan)
        assert "COSC490" in {c.code for c in plan.under_way}

    def test_a_caveat_explains_the_omission(self, morgan) -> None:
        plan = self._plan(morgan)
        assert any("in progress" in c for c in plan.caveats)

    def test_in_progress_does_not_unlock_a_prerequisite(self, morgan) -> None:
        """The safeguard that must survive: sitting in a course is not passing it.

        If in-progress had been folded into `passed` to stop the recommendation,
        it would also have made the student look eligible for everything downstream.
        """
        from app.audit.prereq_graph import passed_courses

        assert "COSC490" not in passed_courses(morgan, self._record().completed)

    def test_a_passed_course_is_still_not_recommended(self, morgan) -> None:
        plan = self._plan(morgan)
        assert "COSC111" not in {r.course.code for r in plan.recommended}
