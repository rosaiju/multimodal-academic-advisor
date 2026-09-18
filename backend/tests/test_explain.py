"""Requirements explorer: plain-English rendering and the catalog doctor's checks."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.catalog.doctor import check_program, main
from app.catalog.explain import (
    describe_block,
    describe_program,
    describe_requirement,
    prerequisite_depth,
    prerequisite_tree,
)
from app.catalog.loader import load_program
from app.catalog.schema import Program

CATALOG_DIR = Path(__file__).resolve().parents[2] / "data" / "catalog"
DEMO = CATALOG_DIR / "demo_university_cs.yaml"


@pytest.fixture(scope="module")
def demo() -> Program:
    return load_program(DEMO)


def block(program: Program, block_id: str):
    return next(b for b in program.requirement_blocks if b.id == block_id)


class TestRequirementText:
    """Every block type must render a sentence. A missing case is a silent gap in
    the UI, so each type is asserted explicitly rather than by a loop."""

    def test_all_of(self, demo: Program) -> None:
        text = describe_requirement(demo, block(demo, "core_cs_lower"))
        assert text.startswith("All of:")
        assert "COSC111" in text

    def test_n_of(self, demo: Program) -> None:
        text = describe_requirement(demo, block(demo, "systems_concentration"))
        assert text.startswith("Any 2 of:")

    def test_credits_from_filter_is_described_not_enumerated(self, demo: Program) -> None:
        text = describe_requirement(demo, block(demo, "cs_electives_upper"))
        assert "9 credits from COSC" in text
        assert "300 or above" in text
        assert "excluding" in text and "COSC320" in text

    def test_gpa(self, demo: Program) -> None:
        assert describe_requirement(demo, block(demo, "major_gpa")) == ("Major GPA of at least 2.0")

    def test_residency_names_the_institution(self, demo: Program) -> None:
        text = describe_requirement(demo, block(demo, "residency"))
        assert "30 credits" in text and "Demo University" in text

    def test_every_block_renders(self, demo: Program) -> None:
        for b in demo.requirement_blocks:
            assert describe_requirement(demo, b).strip()


class TestDescribeBlock:
    def test_eligible_courses_are_resolved(self, demo: Program) -> None:
        summary = describe_block(demo, block(demo, "cs_electives_upper"))
        codes = {c.code for c in summary.eligible_courses}
        assert "COSC350" in codes
        assert "COSC320" not in codes

    def test_advisor_approval_flag_survives(self, demo: Program) -> None:
        """The catalog defers this block to a human. The flag must reach the UI, or
        the system would silently present a guess as settled fact."""
        assert describe_block(demo, block(demo, "gen_ed_humanities")).advisor_approval_required

    def test_counts_reported_per_block_type(self, demo: Program) -> None:
        assert describe_block(demo, block(demo, "core_cs_lower")).courses_required == 5
        assert describe_block(demo, block(demo, "systems_concentration")).courses_required == 2
        assert describe_block(demo, block(demo, "cs_electives_upper")).credits_required == 9


class TestDescribeProgram:
    def test_summary_matches_the_catalog(self, demo: Program) -> None:
        s = describe_program(demo)
        assert s.program_id == "demo_cs_bs"
        assert s.total_credits_required == Decimal(120)
        assert len(s.blocks) == len(demo.requirement_blocks)

    def test_explicitly_demanded_credits_fit_inside_the_degree(self, demo: Program) -> None:
        s = describe_program(demo)
        assert Decimal(0) < s.course_requirement_credits <= s.total_credits_required

    def test_output_is_marked_verified(self, demo: Program) -> None:
        """Catalog data is VERIFIED by construction — no model produced it."""
        assert describe_program(demo).provenance == "verified"


class TestPrerequisiteTree:
    def test_leaf_course_has_no_prerequisites(self, demo: Program) -> None:
        tree = prerequisite_tree(demo, "COSC111")
        assert tree["code"] == "COSC111"
        assert tree["prerequisites"] == []
        assert prerequisite_depth(demo, "COSC111") == 1

    def test_chain_expands_transitively(self, demo: Program) -> None:
        # COSC490 <- COSC450 <- COSC220 <- COSC112 <- COSC111
        assert prerequisite_depth(demo, "COSC490") == 5

    def test_unknown_course_degrades_instead_of_raising(self, demo: Program) -> None:
        assert prerequisite_tree(demo, "NOPE999")["title"] == "(unknown course)"

    def test_cycle_is_truncated_not_infinite(self) -> None:
        """A malformed catalog must still render. Guarding against a hang, which
        would be far harder to debug than a visible cycle marker."""
        cyclic = Program.model_validate(
            {
                "program_id": "c",
                "program": "Cyclic",
                "institution": "X",
                "catalog_year": "2024-2025",
                "total_credits_required": 120,
                "courses": [
                    {"code": "AAA101", "title": "A", "credits": 3, "prerequisites": ["BBB101"]},
                    {"code": "BBB101", "title": "B", "credits": 3, "prerequisites": ["AAA101"]},
                ],
                "requirement_blocks": [
                    {"id": "b", "name": "B", "type": "all_of", "courses": ["AAA101"]}
                ],
            }
        )
        tree = prerequisite_tree(cyclic, "AAA101")
        assert tree["prerequisites"][0]["prerequisites"][0]["cycle"] is True


class TestDoctorChecks:
    def test_demo_catalog_is_clean(self, demo: Program) -> None:
        assert check_program(demo) == []

    def test_orphan_course_is_reported(self) -> None:
        program = Program.model_validate(
            {
                "program_id": "o",
                "program": "Orphan",
                "institution": "X",
                "catalog_year": "2024-2025",
                "total_credits_required": 120,
                "courses": [
                    {"code": "AAA101", "title": "A", "credits": 3},
                    {"code": "ZZZ999", "title": "Unreachable", "credits": 3},
                ],
                "requirement_blocks": [
                    {"id": "b", "name": "B", "type": "all_of", "courses": ["AAA101"]}
                ],
            }
        )
        assert any("ZZZ999" in w for w in check_program(program))

    def test_over_constrained_catalog_is_reported(self) -> None:
        program = Program.model_validate(
            {
                "program_id": "x",
                "program": "Too much",
                "institution": "X",
                "catalog_year": "2024-2025",
                "total_credits_required": 10,
                "courses": [{"code": "AAA101", "title": "A", "credits": 3}],
                "requirement_blocks": [
                    {
                        "id": "b",
                        "name": "B",
                        "type": "credits_from",
                        "credits_required": 99,
                        "courses": ["AAA101"],
                    }
                ],
            }
        )
        assert any("over-constrained" in w for w in check_program(program))


class TestDoctorCli:
    def test_exits_zero_on_the_demo_catalog(self, capsys) -> None:
        assert main([str(DEMO)]) == 0
        assert "BS Computer Science" in capsys.readouterr().out

    def test_exits_nonzero_on_a_missing_file(self, capsys) -> None:
        assert main([str(CATALOG_DIR / "nope.yaml")]) == 1
        assert "FAILED" in capsys.readouterr().err

    def test_exits_nonzero_on_malformed_yaml(self, tmp_path: Path, capsys) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("program_id: x\ncourses: [oops\n", encoding="utf-8")
        assert main([str(bad)]) == 1
        capsys.readouterr()

    def test_course_flag_prints_a_chain(self, capsys) -> None:
        assert main([str(DEMO), "--course", "COSC490"]) == 0
        out = capsys.readouterr().out
        assert "COSC450" in out and "chain depth: 5" in out

    def test_course_flag_rejects_unknown_course(self, capsys) -> None:
        assert main([str(DEMO), "--course", "NOPE999"]) == 1
        capsys.readouterr()

    def test_directory_mode_loads_every_catalog(self, capsys) -> None:
        assert main([str(CATALOG_DIR)]) == 0
        assert "demo_cs_bs" in capsys.readouterr().out


class TestWindowsConsoleSafety:
    """The doctor CLI must print on a default Windows console.

    cp1252 is the default encoding there and raises UnicodeEncodeError on
    box-drawing characters and em-dashes. This bit us once: the tests passed
    because pytest captures as UTF-8, while the real terminal crashed.
    """

    def test_doctor_source_is_pure_ascii(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "app" / "catalog" / "doctor.py").read_text(
            encoding="utf-8"
        )
        offenders = [
            (i, line.strip())
            for i, line in enumerate(source.splitlines(), 1)
            if any(ord(ch) > 127 for ch in line)
        ]
        assert not offenders, f"non-ASCII in doctor.py would crash cp1252: {offenders}"

    def test_output_encodes_as_cp1252(self, capsys) -> None:
        main([str(DEMO)])
        main([str(DEMO), "--course", "COSC490"])
        captured = capsys.readouterr()
        (captured.out + captured.err).encode("cp1252")
