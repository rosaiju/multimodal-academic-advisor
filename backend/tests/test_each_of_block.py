"""Paired OR-groups: `each_of`.

The motivating case is Morgan's English Composition requirement — "Part A:
ENGL 101 or ENGL 111; Part B: ENGL 102 or ENGL 112". Under `n_of` with n=2 over
all four courses, a student who took ENGL 101 and ENGL 111 satisfied the block
while having done no Part B at all. That is a wrong graduation answer produced by
a correct-looking catalog, which is the failure this project exists to prevent.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.catalog.explain import describe_requirement
from app.catalog.schema import Course, EachOfBlock, Program


def _program(*blocks: EachOfBlock) -> Program:
    return Program(
        program_id="t",
        program="T",
        institution="T",
        catalog_year="2026-2028",
        total_credits_required=Decimal(120),
        courses=[
            Course(code=c, title=c, credits=Decimal(3))
            for c in ("ENGL101", "ENGL111", "ENGL102", "ENGL112", "OTHER100")
        ],
        requirement_blocks=list(blocks),
    )


def _composition(**kw) -> EachOfBlock:
    return EachOfBlock(
        id="comp",
        name="Composition",
        groups=[["ENGL101", "ENGL111"], ["ENGL102", "ENGL112"]],
        **kw,
    )


class TestShape:
    def test_flattens_for_referential_integrity(self) -> None:
        assert _composition().courses == ["ENGL101", "ENGL111", "ENGL102", "ENGL112"]

    def test_flattened_list_deduplicates(self) -> None:
        block = EachOfBlock(id="b", name="B", groups=[["A", "B"], ["B", "C"]])
        assert block.courses == ["A", "B", "C"]

    def test_courses_required_is_group_count(self) -> None:
        """Two groups means two courses, regardless of how many options each lists."""
        from app.catalog.explain import describe_block

        summary = describe_block(_program(_composition()), _composition())
        assert summary.courses_required == 2


class TestValidation:
    def test_rejects_empty_group(self) -> None:
        with pytest.raises(ValidationError, match="group 1 is empty"):
            EachOfBlock(id="b", name="B", groups=[["A"], []])

    def test_rejects_repeated_course_within_a_group(self) -> None:
        with pytest.raises(ValidationError, match="repeats a course"):
            EachOfBlock(id="b", name="B", groups=[["A", "A"]])

    def test_rejects_unsatisfiable_block(self) -> None:
        """Two groups cannot both be satisfied by the one course they share."""
        with pytest.raises(ValidationError, match="unsatisfiable"):
            EachOfBlock(id="b", name="B", groups=[["A"], ["A"]])

    def test_rejects_mismatched_group_names(self) -> None:
        with pytest.raises(ValidationError, match="group_names"):
            EachOfBlock(id="b", name="B", groups=[["A"], ["B"]], group_names=["only one"])

    def test_requires_at_least_one_group(self) -> None:
        with pytest.raises(ValidationError):
            EachOfBlock(id="b", name="B", groups=[])

    def test_overlapping_groups_are_allowed_when_satisfiable(self) -> None:
        """A course in two groups is legal - the audit engine assigns it to one.

        Legal because catalogs really do this; the distinctness rule is enforced at
        assignment time, not here.
        """
        block = EachOfBlock(id="b", name="B", groups=[["A", "B"], ["B", "C"]])
        assert len(block.groups) == 2

    def test_unknown_course_is_caught_by_the_program_validator(self) -> None:
        with pytest.raises(ValidationError, match="unknown course 'NOPE999'"):
            _program(EachOfBlock(id="b", name="B", groups=[["ENGL101"], ["NOPE999"]]))


class TestDescription:
    def test_uses_group_names_when_given(self) -> None:
        block = _composition(group_names=["Part A", "Part B"])
        text = describe_requirement(_program(block), block)
        assert text == (
            "One course from each of: Part A (ENGL101 or ENGL111); " "Part B (ENGL102 or ENGL112)"
        )

    def test_falls_back_to_numbered_groups(self) -> None:
        block = _composition()
        assert "Group 1 (ENGL101 or ENGL111)" in describe_requirement(_program(block), block)


class TestEligibleCourses:
    def test_every_group_member_is_eligible(self) -> None:
        program = _program(_composition())
        eligible = {c.code for c in program.courses_for_block(_composition())}
        assert eligible == {"ENGL101", "ENGL111", "ENGL102", "ENGL112"}
        assert "OTHER100" not in eligible
