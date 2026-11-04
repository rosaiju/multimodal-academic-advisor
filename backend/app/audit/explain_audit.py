"""Why is this requirement not done?

Turns one block of an audit into a plain explanation a student can check line by
line: what the block asks for, what counted, what did NOT count and why, which
options are open to them right now, and what to do first.

Every sentence is derived from the catalog and the student's trusted record. The
reasons are the ones an advisor would give ("your D is below the C this block
requires", "COSC220 is in progress, so it counts once a grade lands", "that course
was already spent on another requirement"), and none is generated, so none can be
wrong in the way a model's explanation can.

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/. ***
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.audit.planning import build_plan
from app.audit.prereq_graph import passed_courses, unmet_prerequisites
from app.audit.record import StudentRecord
from app.catalog.explain import describe_requirement
from app.catalog.schema import IN_PROGRESS_GRADES, Program, grade_meets
from app.schemas.audit import AuditResult, BlockStatus
from app.schemas.provenance import Provenance


class CountedCourse(BaseModel):
    code: str
    title: str
    term: str
    grade: str
    credits: Decimal


class NotCounted(BaseModel):
    """A course on the record that is relevant to this block but did not count."""

    code: str
    reason: str


class OpenOption(BaseModel):
    code: str
    title: str
    credits: Decimal
    eligible_now: bool
    #: You're sitting in it now. It is not an option to take, and not yet counted.
    in_progress: bool = False
    missing_prerequisites: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BlockExplanation(BaseModel):
    block_id: str
    name: str
    status: BlockStatus
    requirement: str
    headline: str
    counted: list[CountedCourse] = Field(default_factory=list)
    not_counted: list[NotCounted] = Field(default_factory=list)
    options: list[OpenOption] = Field(default_factory=list)
    next_step: str | None = None
    note: str | None = None
    #: Always VERIFIED: derived from catalog data and the confirmed record.
    provenance: Provenance = Provenance.VERIFIED


class BlockNotFound(LookupError):
    """The audit has no block with that id."""


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _headline(block, result, audit: AuditResult) -> str:
    block_type = block.type
    if result.status is BlockStatus.SATISFIED:
        return "Done. This requirement is met."
    if result.status is BlockStatus.NEEDS_ADVISOR:
        return "The catalog leaves this one to an advisor, so it can't be called met or unmet."
    if block_type == "gpa":
        have = audit.gpa.major if block.scope == "major" else audit.gpa.cumulative
        need = block.min_gpa
        if have is None:
            return f"A {block.scope} GPA of {need} is required, and yours can't be computed yet."
        return f"Your {block.scope} GPA is {have}; this requirement needs {need}."
    if block_type == "residency":
        return (
            f"{result.credits_applied} of {result.credits_required} required credits "
            "completed at this institution."
        )
    if result.courses_required is not None and result.courses_applied is not None:
        left = max(result.courses_required - result.courses_applied, 0)
        done = result.courses_applied
        base = f"{_plural(left, 'more course')} needed ({done} of {result.courses_required} done)."
    else:
        left = max(result.credits_required - result.credits_applied, Decimal(0))
        base = (
            f"{left} more credits needed ({result.credits_applied} of {result.credits_required})."
        )
    if result.status is BlockStatus.IN_PROGRESS:
        base += " Courses you're taking now will count once final grades are in."
    return base


def explain_block(
    program: Program,
    record: StudentRecord,
    audit: AuditResult,
    block_id: str,
) -> BlockExplanation:
    result = next((b for b in audit.blocks if b.block_id == block_id), None)
    block = next((b for b in program.requirement_blocks if b.id == block_id), None)
    if result is None or block is None:
        raise BlockNotFound(block_id)

    min_grade = getattr(block, "min_grade", "D")
    counted_codes = {a.course.code for a in result.applied}
    candidates = {c.code for c in program.courses_for_block(block)}

    # Where each counted course went, so "already used elsewhere" can name the block.
    spent_on: dict[str, str] = {}
    for other in audit.blocks:
        if other.block_id == block_id:
            continue
        for applied in other.applied:
            spent_on.setdefault(applied.course.code, other.name)

    not_counted: list[NotCounted] = []
    seen: set[str] = set()
    for row in record.completed:
        if row.code not in candidates or row.code in counted_codes or row.code in seen:
            continue
        grade = row.grade.strip().upper()
        if not row.is_trusted:
            reason = "Not confirmed yet, so it can't count."
        elif grade in IN_PROGRESS_GRADES:
            reason = "In progress. It counts once a final grade is recorded."
        elif not grade_meets(row.grade, min_grade):
            reason = f"Your grade of {row.grade} is below the {min_grade} this requirement needs."
        elif row.code in spent_on:
            reason = f"Already counted toward {spent_on[row.code]}. A course counts once."
        else:
            reason = "Passed, but not needed here."
        not_counted.append(NotCounted(code=row.code, reason=reason))
        seen.add(row.code)

    passed = passed_courses(program, record.completed)
    in_progress_now = {
        r.code
        for r in record.completed
        if r.is_trusted and r.grade.strip().upper() in IN_PROGRESS_GRADES
    }
    options: list[OpenOption] = []
    for ref in result.still_needed:
        course = program.course(ref.code)
        if course is None:
            continue
        missing = unmet_prerequisites(program, ref.code, passed)
        warnings = (
            ["Offered irregularly. Confirm it runs before relying on it."]
            if course.offered_as_needed
            else []
        )
        options.append(
            OpenOption(
                code=ref.code,
                title=ref.title,
                credits=ref.credits,
                eligible_now=not missing and ref.code not in in_progress_now,
                in_progress=ref.code in in_progress_now,
                missing_prerequisites=missing,
                warnings=warnings,
            )
        )
    options.sort(key=lambda o: (o.in_progress, not o.eligible_now, o.code))

    next_step: str | None = None
    if result.status is not BlockStatus.SATISFIED and options:
        plan = build_plan(program, record, audit, limit=10_000)
        pick = next((r for r in plan.recommended if block_id in r.serves), None)
        if pick is not None:
            next_step = f"Take {pick.course.code} ({pick.course.title}) next. " + pick.reasons[0]
        else:
            first = next((o for o in options if o.missing_prerequisites), None)
            if first is not None:
                next_step = (
                    f"Nothing here is open to you yet. {first.code} needs "
                    + ", ".join(first.missing_prerequisites)
                    + " first."
                )

    counted = [
        CountedCourse(
            code=a.course.code,
            title=a.course.title,
            term=a.term,
            grade=a.grade,
            credits=a.credits_applied,
        )
        for a in result.applied
    ]

    return BlockExplanation(
        block_id=block_id,
        name=result.name,
        status=result.status,
        requirement=describe_requirement(program, block),
        headline=_headline(block, result, audit),
        counted=counted,
        not_counted=not_counted,
        options=options,
        next_step=next_step,
        note=result.note,
    )


def explain_all(
    program: Program, record: StudentRecord, audit: AuditResult
) -> list[BlockExplanation]:
    return [explain_block(program, record, audit, b.block_id) for b in audit.blocks]
