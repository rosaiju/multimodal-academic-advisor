"""The real Morgan State COSC catalog (2026-2028).

Unlike demo_university_cs.yaml, this file describes a real degree that real students
will be audited against. These tests guard the properties that, if broken, would
produce a confidently wrong answer to "can I graduate?"

They also pin the file's PROVISIONAL status. Several requirement blocks are
deliberately absent pending answers from the department (docs/catalog-open-questions.md).
The tests below assert that absence on purpose: when someone encodes those blocks,
these tests should fail and be updated as part of that work, rather than the blocks
being filled in from a guess and nobody noticing.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.catalog.loader import load_program
from app.catalog.schema import Program

CATALOG = Path(__file__).resolve().parents[2] / "data" / "catalog" / "morgan_cosc_bs_2026_2028.yaml"

#: Stubs standing in for the un-harvested MATH subject. See the YAML header.
MATH_STUBS = {"MATH141", "MATH241", "MATH242"}


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
        """120 is the university baccalaureate minimum and is not disputed.

        The disputed figures (major 59 vs 65, gen ed 40 vs 44) must stay unencoded
        until the department resolves them.
        """
        assert morgan.total_credits_required == Decimal(120)


class TestCourseLayer:
    def test_all_37_cosc_courses_present(self, morgan: Program) -> None:
        cosc = [c for c in morgan.courses if c.subject == "COSC"]
        assert len(cosc) == 37

    def test_no_dangling_prerequisites(self, morgan: Program) -> None:
        """Schema enforces this at load; asserted here so the intent is explicit."""
        known = {c.code for c in morgan.courses}
        for course in morgan.courses:
            for code in course.prerequisites:
                assert code in known, f"{course.code} -> {code}"

    def test_cosc462_prerequisite_is_dropped_not_invented(self, morgan: Program) -> None:
        """COSC 462's catalog prerequisite COSC 459 does not exist (open question 6).

        It must stay dropped. If someone 'fixes' this by pointing COSC462 at a
        plausible substitute, that is a guess about a real degree requirement and
        this test should stop them.
        """
        course = morgan.course("COSC462")
        assert course is not None
        assert course.prerequisites == []
        assert morgan.course("COSC459") is None

    def test_irregular_courses_are_flagged(self, morgan: Program) -> None:
        """'AS NEEDED' / 'FALL OR SPRING' courses must not claim a reliable schedule."""
        flagged = [c.code for c in morgan.courses if c.offered_as_needed]
        # 22 "AS NEEDED" + 5 "FALL OR SPRING"; only 10 of 37 COSC courses run
        # reliably every term.
        assert len(flagged) == 27
        assert "COSC456" in flagged  # AS NEEDED
        assert "COSC470" in flagged  # FALL OR SPRING
        assert "COSC111" not in flagged  # FALL/SPRING - genuinely every term

    def test_prerequisite_grade_is_c_where_the_catalog_says_so(self, morgan: Program) -> None:
        """Morgan requires C or better in prerequisites; D would silently pass students."""
        for course in morgan.courses:
            if course.prerequisites:
                assert course.min_prereq_grade == "C", course.code


class TestMathStubs:
    def test_stubs_are_zero_credit(self, morgan: Program) -> None:
        """A guessed credit value would flow into audit totals. Zero fails loudly."""
        for code in MATH_STUBS:
            course = morgan.course(code)
            assert course is not None, code
            assert course.credits == Decimal(0)
            assert "UNVERIFIED" in course.title

    def test_stubs_satisfy_no_requirement_block(self, morgan: Program) -> None:
        """Stubs exist only for prerequisite integrity - they must never count."""
        counted: set[str] = set()
        for block in morgan.requirement_blocks:
            counted.update(c.code for c in morgan.courses_for_block(block))
        assert not (MATH_STUBS & counted)


class TestProvisionalStatus:
    """These assert what is deliberately NOT encoded yet. See the module docstring."""

    def test_disputed_blocks_absent(self, morgan: Program) -> None:
        ids = {b.id for b in morgan.requirement_blocks}
        for disputed in ("major_group_a", "major_group_b", "major_group_c", "residency"):
            assert disputed not in ids, (
                f"{disputed} was encoded - was its open question actually resolved? "
                "See docs/catalog-open-questions.md"
            )

    def test_capstone_defers_to_a_human(self, morgan: Program) -> None:
        """COSC490 is gated on chair permission, which the schema cannot express."""
        block = next(b for b in morgan.requirement_blocks if b.id == "cs_capstone")
        assert block.advisor_approval_required
