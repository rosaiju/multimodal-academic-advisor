"""The real Morgan State COSC B.S. catalog (2026-2028).

Unlike demo_university_cs.yaml, this file describes a real degree that real students
will be audited against. These tests guard the properties that, if broken, would
produce a confidently wrong answer to "can I graduate?"

Two kinds of test live here:

* Data invariants - things the official sources state and we transcribed.
* PROVISIONAL-status assertions - things deliberately NOT encoded because two
  official Morgan pages contradict each other (docs/catalog-open-questions.md).
  These assert an absence on purpose. When someone encodes one of those blocks,
  the matching test should fail and be updated as part of that work, rather than
  a guess quietly becoming a degree requirement.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.catalog.loader import load_program
from app.catalog.schema import Program

CATALOG = Path(__file__).resolve().parents[2] / "data" / "catalog" / "morgan_cosc_bs_2026_2028.yaml"


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(CATALOG)


class TestIdentity:
    def test_loads_and_validates(self, morgan: Program) -> None:
        assert morgan.program_id == "morgan_cosc_bs_2026_2028"
        assert morgan.institution == "Morgan State University"

    def test_catalog_year_is_the_biennial_edition(self, morgan: Program) -> None:
        """Morgan publishes biennial catalogs. '2026-2027' would be a wrong edition."""
        assert morgan.catalog_year == "2026-2028"

    def test_only_undisputed_credit_total_is_encoded(self, morgan: Program) -> None:
        """[PLANNER] and [SEQ] both print TOTAL 120. The major subtotal is disputed
        (65 vs 59) and must stay unencoded until the department resolves it."""
        assert morgan.total_credits_required == Decimal(120)


class TestProvenance:
    def test_every_block_cites_a_source(self, morgan: Program) -> None:
        """A requirement a human cannot re-check is not usable in a degree audit.

        Every figure in this catalog has at least one official page that disagrees
        with it, so 'where did this come from' is not optional here.
        """
        unsourced = [b.id for b in morgan.requirement_blocks if not b.source]
        assert unsourced == []

    def test_sources_name_a_specific_page(self, morgan: Program) -> None:
        """A source must identify WHICH official page, not just say 'the catalog'."""
        for block in morgan.requirement_blocks:
            assert block.source is not None
            assert any(tag in block.source for tag in ("[PLANNER]", "[SEQ]", "[GENED]")), block.id


class TestCourseLayer:
    def test_course_count(self, morgan: Program) -> None:
        """55 courses named by [PLANNER], plus the MATH141 prerequisite-only stub."""
        assert len(morgan.courses) == 56
        assert len([c for c in morgan.courses if c.subject == "COSC"]) == 41

    def test_cosc459_exists_and_is_required(self, morgan: Program) -> None:
        """COSC 459 Database Design is a REQUIRED major course.

        An earlier pass concluded it did not exist, because a brute-force course-ID
        sweep never reached it. Both [PLANNER] and [SEQ] list it. This test exists so
        that mistake cannot recur silently.
        """
        course = morgan.course("COSC459")
        assert course is not None
        assert course.title == "Database Design"
        assert course.credits == Decimal(3)
        block = next(b for b in morgan.requirement_blocks if b.id == "major_required_courses")
        assert "COSC459" in block.courses

    def test_univ101_is_the_orientation_course(self, morgan: Program) -> None:
        """[PLANNER] and [SEQ] both name UNIV 101. ORNS 106 appears in neither."""
        assert morgan.course("UNIV101") is not None
        assert morgan.course("ORNS106") is None

    def test_no_dangling_prerequisites(self, morgan: Program) -> None:
        """Schema enforces this at load; asserted here so the intent is explicit."""
        known = {c.code for c in morgan.courses}
        for course in morgan.courses:
            for code in course.prerequisites:
                assert code in known, f"{course.code} -> {code}"

    def test_prerequisite_grade_is_c_where_sourced(self, morgan: Program) -> None:
        """Morgan requires C or better in prerequisites; D would silently pass students."""
        for course in morgan.courses:
            if course.prerequisites:
                assert course.min_prereq_grade == "C", course.code

    def test_unsourced_prerequisites_are_absent_not_guessed(self, morgan: Program) -> None:
        """34 courses have no prerequisite source consulted yet.

        They must carry NO prerequisites rather than plausible ones. An invented
        prerequisite blocks a student from a course they are actually eligible for.
        """
        sourced = {c.code for c in morgan.courses if c.prerequisites}
        assert len(sourced) == 20
        assert morgan.course("COSC459").prerequisites == []


class TestMath141Stub:
    def test_stub_is_zero_credit_and_marked(self, morgan: Program) -> None:
        """MATH141 is the only course here not named by [PLANNER] - it appears only
        as a COSC241 prerequisite. A guessed credit value would flow into audit
        totals; zero fails loudly instead."""
        course = morgan.course("MATH141")
        assert course is not None
        assert course.credits == Decimal(0)
        assert "UNVERIFIED" in course.title

    def test_stub_satisfies_no_requirement_block(self, morgan: Program) -> None:
        counted: set[str] = set()
        for block in morgan.requirement_blocks:
            counted.update(c.code for c in morgan.courses_for_block(block))
        assert "MATH141" not in counted

    def test_stub_is_the_only_zero_credit_course(self, morgan: Program) -> None:
        zero = [c.code for c in morgan.courses if c.credits == 0]
        assert zero == ["MATH141"]


class TestProvisionalStatus:
    """Asserts what is deliberately NOT encoded. See the module docstring."""

    def test_disputed_elective_groups_absent(self, morgan: Program) -> None:
        """[PLANNER] and [SEQ] disagree on both membership and count for every group."""
        ids = {b.id for b in morgan.requirement_blocks}
        for disputed in (
            "major_group_a",
            "major_group_b",
            "major_group_c",
            "major_group_d",
            "complementary_studies",
            "residency",
        ):
            assert disputed not in ids, (
                f"{disputed} was encoded - was its open question actually resolved? "
                "See docs/catalog-open-questions.md"
            )

    def test_no_block_asserts_a_disputed_credit_total(self, morgan: Program) -> None:
        """The disputed figures are 59/65 (major) and 40/44 (gen ed). No credits_from
        block may encode one until the department picks a number."""
        for block in morgan.requirement_blocks:
            demanded = getattr(block, "credits_required", None)
            assert demanded not in (Decimal(59), Decimal(65), Decimal(40), Decimal(44)), block.id

    def test_composition_block_defers_to_a_human(self, morgan: Program) -> None:
        """n_of cannot express 'one from Part A AND one from Part B', so the block
        over-accepts and must not be reported as automatically satisfied."""
        block = next(b for b in morgan.requirement_blocks if b.id == "gen_ed_composition")
        assert block.advisor_approval_required
