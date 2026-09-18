"""Catalog loading and validation.

These tests exist because a typo in a catalog file must fail loudly at load time.
A silently-skipped requirement produces a wrong degree audit, which is the one
failure this project cannot ship.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.catalog.loader import CatalogError, load_all, load_program
from app.catalog.schema import CreditsFromBlock, NOfBlock, Program, grade_meets

CATALOG_DIR = Path(__file__).resolve().parents[2] / "data" / "catalog"
DEMO = CATALOG_DIR / "demo_university_cs.yaml"


@pytest.fixture(scope="module")
def demo() -> Program:
    return load_program(DEMO)


class TestLoading:
    def test_demo_catalog_loads(self, demo: Program) -> None:
        assert demo.program_id == "demo_cs_bs"
        assert demo.total_credits_required == Decimal(120)
        assert len(demo.courses) == 23

    def test_load_all_keys_by_program_id(self) -> None:
        programs = load_all(CATALOG_DIR)
        assert "demo_cs_bs" in programs

    def test_missing_file_is_fatal(self) -> None:
        with pytest.raises(CatalogError, match="not found"):
            load_program(CATALOG_DIR / "does_not_exist.yaml")


class TestReferentialIntegrity:
    """A course code named anywhere must exist. This is the typo guard."""

    def _minimal(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "program_id": "t",
            "program": "Test",
            "institution": "Test U",
            "catalog_year": "2024-2025",
            "total_credits_required": 120,
            "courses": [{"code": "AAA101", "title": "A", "credits": 3}],
            "requirement_blocks": [
                {"id": "b1", "name": "B", "type": "all_of", "courses": ["AAA101"]}
            ],
        }
        return base | overrides

    def test_valid_minimal_program(self) -> None:
        assert Program.model_validate(self._minimal()).program_id == "t"

    def test_block_naming_unknown_course_is_rejected(self) -> None:
        bad = self._minimal(
            requirement_blocks=[{"id": "b1", "name": "B", "type": "all_of", "courses": ["TYPO999"]}]
        )
        with pytest.raises(ValueError, match="unknown course"):
            Program.model_validate(bad)

    def test_unknown_prerequisite_is_rejected(self) -> None:
        bad = self._minimal(
            courses=[{"code": "AAA101", "title": "A", "credits": 3, "prerequisites": ["NOPE100"]}]
        )
        with pytest.raises(ValueError, match="unknown prerequisite"):
            Program.model_validate(bad)

    def test_duplicate_course_code_is_rejected(self) -> None:
        bad = self._minimal(
            courses=[
                {"code": "AAA101", "title": "A", "credits": 3},
                {"code": "AAA101", "title": "A again", "credits": 3},
            ]
        )
        with pytest.raises(ValueError, match="duplicate course code"):
            Program.model_validate(bad)

    def test_n_of_block_cannot_require_more_than_it_offers(self) -> None:
        bad = self._minimal(
            requirement_blocks=[
                {"id": "b1", "name": "B", "type": "n_of", "n": 5, "courses": ["AAA101"]}
            ]
        )
        with pytest.raises(ValueError, match="exceeds"):
            Program.model_validate(bad)


class TestBlockCourseResolution:
    def test_credits_from_expands_its_filter(self, demo: Program) -> None:
        block = next(b for b in demo.requirement_blocks if b.id == "cs_electives_upper")
        assert isinstance(block, CreditsFromBlock)
        codes = {c.code for c in demo.courses_for_block(block)}

        assert "COSC350" in codes, "upper-division COSC should match the filter"
        assert "COSC320" not in codes, "explicitly excluded"
        assert "COSC220" not in codes, "below number_min"
        assert "MATH312" not in codes, "wrong subject"

    def test_overlap_that_makes_greedy_matching_wrong(self, demo: Program) -> None:
        """COSC350 is eligible for two blocks at once.

        This overlap is the whole reason the matcher must solve an assignment
        problem rather than iterate. Guarding it so nobody 'simplifies' the demo
        catalog and quietly removes the Milestone 1 demo.
        """
        electives = next(b for b in demo.requirement_blocks if b.id == "cs_electives_upper")
        systems = next(b for b in demo.requirement_blocks if b.id == "systems_concentration")
        assert isinstance(systems, NOfBlock)

        in_electives = {c.code for c in demo.courses_for_block(electives)}
        in_systems = {c.code for c in demo.courses_for_block(systems)}
        assert len(in_electives & in_systems) >= 2


class TestGradeComparison:
    @pytest.mark.parametrize(
        ("earned", "minimum", "expected"),
        [
            ("A", "C", True),
            ("C", "C", True),
            ("C+", "C", True),
            ("D", "C", False),
            ("F", "D", False),
            ("W", "D", False),
            ("IP", "C", False),
            ("", "C", False),
            ("Z", "C", False),
        ],
    )
    def test_grade_meets(self, earned: str, minimum: str, expected: bool) -> None:
        assert grade_meets(earned, minimum) is expected
