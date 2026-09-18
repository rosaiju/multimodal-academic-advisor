"""Evaluate an `each_of` requirement block against a student record.

An `each_of` block says "one course from Part A, one from Part B", and no single
course may cover both. This module decides whether a student has done that.

Scope: this file evaluates ONE block type. The other block types get their own
evaluators; a general dispatcher is deliberately not built yet.

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/. ***
"""

from __future__ import annotations

from decimal import Decimal

from app.audit.matching import maximum_matching
from app.audit.record import StudentRecord
from app.catalog.schema import Course, EachOfBlock, Program, grade_meets
from app.schemas.audit import (
    AppliedCourse,
    BlockStatus,
    CourseRef,
    RequirementBlockResult,
)


def _course_ref(course: Course) -> CourseRef:
    return CourseRef(
        code=course.code,
        subject=course.subject,
        number=course.number,
        title=course.title,
        credits=course.credits,
    )


def _minimum_credits(program: Program, block: EachOfBlock) -> Decimal:
    """Smallest credit total that could satisfy the block.

    Each group contributes its cheapest option. Groups are course-counted, not
    credit-counted, so this is a derived figure for display and progress bars - it
    is never the thing being tested.
    """
    total = Decimal(0)
    for group in block.groups:
        credits = [c.credits for code in group if (c := program.course(code))]
        total += min(credits) if credits else Decimal(0)
    return total


def evaluate_each_of(
    program: Program,
    block: EachOfBlock,
    record: StudentRecord,
) -> RequirementBlockResult:
    """Decide whether `record` satisfies `block`.

    A completed course counts toward a group only if all of these hold:
      * it is listed in that group,
      * its provenance is trusted for audit (never an unconfirmed AI extraction),
      * its grade meets the block's minimum.

    Eligible courses are then assigned to groups so that each group gets a distinct
    course. The block is SATISFIED only when every group is assigned.
    """
    trusted = [
        c
        for c in record.trusted_courses()
        if grade_meets(c.grade, block.min_grade) and program.course(c.code) is not None
    ]

    # candidates[g] = indices into `trusted` that could satisfy group g
    candidates: list[list[int]] = [
        [i for i, taken in enumerate(trusted) if taken.code in group] for group in block.groups
    ]
    assignment = maximum_matching(candidates)

    applied: list[AppliedCourse] = []
    for group_index in sorted(assignment):
        taken = trusted[assignment[group_index]]
        course = program.course(taken.code)
        assert course is not None  # filtered above
        applied.append(
            AppliedCourse(
                course=_course_ref(course),
                term=taken.term,
                grade=taken.grade,
                credits_applied=course.credits,
                provenance=taken.provenance,
            )
        )

    unmatched = [i for i in range(len(block.groups)) if i not in assignment]
    still_needed = [
        ref
        for i in unmatched
        for code in block.groups[i]
        if (c := program.course(code)) and (ref := _course_ref(c))
    ]

    if block.advisor_approval_required:
        status = BlockStatus.NEEDS_ADVISOR
    elif not unmatched:
        status = BlockStatus.SATISFIED
    elif applied:
        status = BlockStatus.IN_PROGRESS
    else:
        status = BlockStatus.UNMET

    return RequirementBlockResult(
        block_id=block.id,
        name=block.name,
        status=status,
        credits_required=_minimum_credits(program, block),
        credits_applied=sum((a.credits_applied for a in applied), Decimal(0)),
        courses_required=len(block.groups),
        courses_applied=len(applied),
        applied=applied,
        still_needed=still_needed,
        note=block.note,
    )
