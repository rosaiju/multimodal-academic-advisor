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

from app.audit.engine import MatcherStrategy, run_audit
from app.audit.planning import build_plan
from app.audit.record import CompletedCourse, StudentRecord
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
        """55 courses named by [PLANNER], the MATH141 prerequisite-only stub, and the
        16 general education courses that only [GENED] names (CT 6, HH 5, CI 5)."""
        assert len(morgan.courses) == 72
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

    def test_composition_block_enforces_both_parts(self, morgan: Program) -> None:
        """Composition is Part A + Part B, not "any two of four".

        This was an n_of block flagged for advisor review because the schema could
        not express the pairing. It is now each_of, which enforces it outright.
        """
        block = next(b for b in morgan.requirement_blocks if b.id == "gen_ed_composition")
        assert block.type == "each_of"
        assert block.groups == [["ENGL101", "ENGL111"], ["ENGL102", "ENGL112"]]
        assert block.group_names == ["Part A", "Part B"]
        assert not block.advisor_approval_required

    def test_composition_rejects_two_courses_from_one_part(self, morgan: Program) -> None:
        """The exact bug each_of was built to fix: ENGL101 + ENGL111 is not enough."""
        block = next(b for b in morgan.requirement_blocks if b.id == "gen_ed_composition")
        part_a, part_b = block.groups
        assert set(part_a).isdisjoint(part_b), "parts must be distinct for the pairing to bind"
        assert len(block.groups) == 2


# General education, transcribed from [GENED]. These lists are copied from the page,
# not from the YAML, so a typo in either place fails the test.
GEN_ED_ENCODED = {
    "gen_ed_critical_thinking": (
        "III. Critical Thinking - (CT)",
        ["ARCH105", "COMM300", "ENGR110", "MHTC340", "PHIL109", "PHIL119"],
    ),
    "gen_ed_health": (
        "VIII. Health and Healthful Living - (HH)",
        ["HLTH103", "HLTH200", "HLTH203", "HLTH300", "NUSC160"],
    ),
    "gen_ed_contemporary_issues": (
        "IX. Contemporary and Global Issues, Ideas and Values - (CI)",
        ["AFST350", "HIST350", "HIST360", "MENA100", "WGST201"],
    ),
}


def _audit(program: Program, *codes: str, grade: str = "A") -> dict[str, str]:
    """Block id -> status name for a student who completed `codes`."""
    record = StudentRecord(
        student_id="gen-ed-test",
        completed=[
            CompletedCourse(
                code=code,
                term="Fall 2025",
                grade=grade,
                credits=program.course(code).credits,
            )
            for code in codes
        ],
    )
    result = run_audit(program, record, MatcherStrategy.OPTIMAL_BIPARTITE)
    return {b.block_id: b.status.name for b in result.blocks}


class TestGeneralEducationData:
    @pytest.mark.parametrize("block_id", GEN_ED_ENCODED)
    def test_course_list_matches_the_catalog_page(self, morgan: Program, block_id: str) -> None:
        block = next(b for b in morgan.requirement_blocks if b.id == block_id)
        _, expected = GEN_ED_ENCODED[block_id]
        assert block.type == "n_of"
        assert block.n == 1
        assert sorted(block.courses) == sorted(expected)

    @pytest.mark.parametrize("block_id", GEN_ED_ENCODED)
    def test_block_cites_its_category_heading(self, morgan: Program, block_id: str) -> None:
        block = next(b for b in morgan.requirement_blocks if b.id == block_id)
        heading, _ = GEN_ED_ENCODED[block_id]
        assert block.source is not None
        assert "[GENED]" in block.source
        assert heading in block.source

    @pytest.mark.parametrize("block_id", GEN_ED_ENCODED)
    def test_every_option_is_the_three_credits_the_category_demands(
        self, morgan: Program, block_id: str
    ) -> None:
        """CT, HH and CI each print '3 Credits Required' and every option is 3 credits."""
        block = next(b for b in morgan.requirement_blocks if b.id == block_id)
        for code in block.courses:
            assert morgan.course(code).credits == Decimal(3), code

    def test_gen_ed_courses_carry_no_invented_prerequisites(self, morgan: Program) -> None:
        """[GENED] says to check the catalog or Websis for prerequisites; none was read."""
        for _, codes in GEN_ED_ENCODED.values():
            for code in codes:
                assert morgan.course(code).prerequisites == [], code

    def test_honors_titles_use_the_normalized_dash(self, morgan: Program) -> None:
        assert morgan.course("PHIL119").title == "Critical Thinking - Honors"
        assert "(CI)" not in morgan.course("HIST350").title
        assert "(CI)" not in morgan.course("HIST360").title


class TestGeneralEducationAudit:
    def test_nothing_taken_leaves_every_category_unmet(self, morgan: Program) -> None:
        statuses = _audit(morgan)
        for block_id in GEN_ED_ENCODED:
            assert statuses[block_id] == "UNMET", block_id

    @pytest.mark.parametrize(
        ("block_id", "course"),
        [(block_id, code) for block_id, (_, codes) in GEN_ED_ENCODED.items() for code in codes],
    )
    def test_any_one_listed_course_satisfies_its_category(
        self, morgan: Program, block_id: str, course: str
    ) -> None:
        assert _audit(morgan, course)[block_id] == "SATISFIED"

    def test_a_course_from_another_category_does_not_satisfy(self, morgan: Program) -> None:
        """HLTH 200 is a Health option; it must not quietly count as Critical Thinking."""
        statuses = _audit(morgan, "HLTH200")
        assert statuses["gen_ed_health"] == "SATISFIED"
        assert statuses["gen_ed_critical_thinking"] == "UNMET"
        assert statuses["gen_ed_contemporary_issues"] == "UNMET"

    def test_engl101_engl102_and_one_course_from_each_category(self, morgan: Program) -> None:
        statuses = _audit(morgan, "ENGL101", "ENGL102", "PHIL109", "HLTH203", "WGST201")
        for block_id in ("gen_ed_composition", *GEN_ED_ENCODED):
            assert statuses[block_id] == "SATISFIED", block_id

    def test_composition_still_needs_both_parts_alongside_the_new_blocks(
        self, morgan: Program
    ) -> None:
        statuses = _audit(morgan, "ENGL101", "PHIL109", "HLTH203", "WGST201")
        assert statuses["gen_ed_composition"] != "SATISFIED"
        assert statuses["gen_ed_critical_thinking"] == "SATISFIED"

    def test_an_in_progress_course_is_not_a_satisfied_category(self, morgan: Program) -> None:
        """A course being taken now earns no credit toward the category until it is done."""
        assert _audit(morgan, "PHIL109", grade="IP")["gen_ed_critical_thinking"] != "SATISFIED"


class TestGeneralEducationDoesNotCrowdOutTheMajor:
    def test_required_courses_outrank_pick_one_options(self, morgan: Program) -> None:
        """Gen-ed 'pick one of N' options tie with required major courses on chain and
        unlocks. Alphabetical tie-breaking put AFST 350 ahead of COSC 243 and pushed
        required courses off the advisor's short list."""
        record = StudentRecord(student_id="plan-test", completed=[])
        plan = build_plan(morgan, record, run_audit(morgan, record), limit=100)
        order = [r.course.code for r in plan.recommended]
        options = {c for _, codes in GEN_ED_ENCODED.values() for c in codes}
        required = [c for c in order if c not in options]
        first_option = min(order.index(c) for c in options if c in order)
        # Every course the major requires outright and is eligible now comes first.
        for code in ("COSC243", "COSC351", "COSC458"):
            if code in order:
                assert order.index(code) < first_option, code
        assert required, "sanity: the plan recommends major courses"


class TestGeneralEducationProvisional:
    """Categories deliberately NOT encoded. See Q12-Q15 in docs/catalog-open-questions.md.

    [GENED] is unambiguous about each of them; what is missing is a way to express
    it without giving a student a confidently wrong answer. When one is encoded,
    the matching assertion should fail and be replaced with real tests.
    """

    @pytest.mark.parametrize(
        ("block_id", "question"),
        [
            ("gen_ed_information_literacy", "Q12"),
            ("gen_ed_mathematics", "Q12"),
            ("gen_ed_arts_humanities", "Q13"),
            ("gen_ed_sciences", "Q14"),
            ("gen_ed_social_sciences", "Q15"),
        ],
    )
    def test_unencoded_category_stays_absent(
        self, morgan: Program, block_id: str, question: str
    ) -> None:
        ids = {b.id for b in morgan.requirement_blocks}
        assert block_id not in ids, (
            f"{block_id} was encoded - was {question} in docs/catalog-open-questions.md "
            "actually resolved?"
        )

    def test_the_substitution_courses_are_not_listed_under_gen_ed(self, morgan: Program) -> None:
        """COSC 111 and MATH 241 are already required by the major. The engine applies a
        course to one block only, so listing them under IM/MQ would be dead data (Q12)."""
        gen_ed = [b for b in morgan.requirement_blocks if b.id.startswith("gen_ed_")]
        for block in gen_ed:
            assert "COSC111" not in block.courses, block.id
            assert "MATH241" not in block.courses, block.id

    def test_major_courses_are_not_displaced_by_gen_ed(self, morgan: Program) -> None:
        """A student with COSC 111 and MATH 241 keeps the major credit they earned."""
        statuses = _audit(morgan, "COSC111", "MATH241")
        assert statuses["major_required_courses"] != "UNMET"
        assert statuses["supporting_courses"] != "UNMET"
