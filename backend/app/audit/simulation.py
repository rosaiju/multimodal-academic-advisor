"""What-if simulation: audit a copy of the record with hypothetical courses added.

The stored record is never touched. `simulate()` builds a NEW `StudentRecord`
(the model is frozen, so a mutation would raise) containing the real coursework
plus the hypothetical courses, runs the SAME `run_audit()` and `build_plan()` the
dashboard uses on both versions, and reports the difference. Nothing is
persisted, and nothing in here estimates: every number is an audit result.

Two things are assumed and said out loud in `warnings`:

* a hypothetical course is passed with `assumed_grade` (default C, the usual
  minimum for a prerequisite), and GPA is not projected;
* in `same_term` mode (the default) every course is checked against the CURRENT
  record only - a student cannot take COSC 281 and its prerequisite in the same
  term - while `completed` mode answers "if I had passed all of these", applying
  a course once its prerequisites are met by the record plus earlier ones.

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/. ***
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.audit.engine import run_audit
from app.audit.planning import build_plan
from app.audit.prereq_graph import (
    eligible_courses,
    is_eligible,
    passed_courses,
    unmet_prerequisites,
)
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.schema import IN_PROGRESS_GRADES, Course, Program
from app.schemas.audit import AuditResult, BlockStatus, CourseRef, MatcherStrategy

MAX_HYPOTHETICAL_COURSES = 12

SkipReason = Literal["unknown_course", "already_completed", "missing_prerequisites", "duplicate"]


class SimulationRequest(BaseModel):
    courses: list[str] = Field(min_length=1, max_length=MAX_HYPOTHETICAL_COURSES)
    mode: Literal["same_term", "completed"] = "same_term"
    assumed_grade: str = Field(default="C", pattern=r"^[A-D][+-]?$")
    program_id: str | None = None


class SkippedCourse(BaseModel):
    code: str
    reason: SkipReason
    detail: str
    missing_prerequisites: list[str] = Field(default_factory=list)


class Snapshot(BaseModel):
    credits_earned: Decimal
    credits_remaining: Decimal
    credit_progress_percent: float
    percent_complete: float | None
    graduation_eligible: bool


class BlockChange(BaseModel):
    block_id: str
    name: str
    status_before: BlockStatus
    status_after: BlockStatus
    courses_applied_before: int | None
    courses_applied_after: int | None
    credits_applied_before: Decimal
    credits_applied_after: Decimal


class StillBlocked(BaseModel):
    course: CourseRef
    missing_prerequisites: list[str]


class SimulationResult(BaseModel):
    mode: Literal["same_term", "completed"]
    assumed_grade: str
    applied: list[CourseRef]
    skipped: list[SkippedCourse]
    before: Snapshot
    after: Snapshot
    credits_added: Decimal
    newly_satisfied_blocks: list[BlockChange]
    changed_blocks: list[BlockChange]
    newly_unlocked: list[CourseRef]
    still_blocked: list[StillBlocked]
    recommended_after: list[CourseRef]
    warnings: list[str]
    #: Always true. The API returns it so a client (and a test) can see the
    #: stored record was not the thing that was audited.
    simulated: bool = True


def canonical_code(raw: str) -> str:
    """'cosc 220' / 'COSC-220' -> 'COSC220'."""
    return "".join(ch for ch in raw.upper() if ch.isalnum())


def _ref(course: Course) -> CourseRef:
    return CourseRef(
        code=course.code,
        subject=course.subject,
        number=course.number,
        title=course.title,
        credits=course.credits,
    )


def _snapshot(audit: AuditResult) -> Snapshot:
    remaining = max(audit.total_credits_required - audit.total_credits_earned, Decimal(0))
    return Snapshot(
        credits_earned=audit.total_credits_earned,
        credits_remaining=remaining,
        credit_progress_percent=round(audit.credit_progress_percent, 1),
        percent_complete=(
            None if audit.percent_complete is None else round(audit.percent_complete, 1)
        ),
        graduation_eligible=audit.is_graduation_eligible,
    )


def _select(
    program: Program,
    record: StudentRecord,
    request: SimulationRequest,
) -> tuple[list[Course], list[SkippedCourse]]:
    """Which hypothetical courses can honestly be applied, and why the rest cannot."""
    passed = passed_courses(program, record.completed)
    skipped: list[SkippedCourse] = []
    pending: list[Course] = []
    seen: set[str] = set()

    for raw in request.courses:
        code = canonical_code(raw)
        course = program.course(code)
        if code in seen:
            skipped.append(SkippedCourse(code=code, reason="duplicate", detail="listed twice"))
        elif course is None:
            skipped.append(
                SkippedCourse(
                    code=code,
                    reason="unknown_course",
                    detail="not in the encoded catalog, so it cannot be simulated",
                )
            )
        elif code in passed:
            skipped.append(
                SkippedCourse(
                    code=code,
                    reason="already_completed",
                    detail="already completed on your record; nothing would change",
                )
            )
        else:
            pending.append(course)
        seen.add(code)

    grade = request.assumed_grade
    applied: list[Course] = []
    assumed = dict(passed)

    def take(course: Course) -> None:
        applied.append(course)
        assumed[course.code] = grade

    if request.mode == "same_term":
        eligible_now = [c for c in pending if is_eligible(program, c.code, passed)]
        for course in eligible_now:
            take(course)
        leftover = [c for c in pending if c not in eligible_now]
    else:
        leftover = list(pending)
        progressed = True
        while leftover and progressed:
            progressed = False
            for course in list(leftover):
                if is_eligible(program, course.code, assumed):
                    take(course)
                    leftover.remove(course)
                    progressed = True

    basis = passed if request.mode == "same_term" else assumed
    chosen = {c.code for c in applied}
    for course in leftover:
        missing = unmet_prerequisites(program, course.code, basis)
        detail = "prerequisites are not met by your record"
        if request.mode == "same_term" and chosen.intersection(
            part for m in missing for part in m.split(" or ")
        ):
            detail += "; a prerequisite taken in the same term does not count"
        skipped.append(
            SkippedCourse(
                code=course.code,
                reason="missing_prerequisites",
                detail=detail,
                missing_prerequisites=missing,
            )
        )
    return applied, skipped


def simulate(
    program: Program, record: StudentRecord, request: SimulationRequest
) -> SimulationResult:
    """Audit `record` as it would stand after `request.courses`. Read-only."""
    applied, skipped = _select(program, record, request)
    hypothetical = {c.code for c in applied}

    # Drop an in-progress entry for a course the scenario passes, so it is not
    # counted twice; everything else on the record is carried over untouched.
    kept = [
        c
        for c in record.completed
        if not (c.code in hypothetical and c.grade.strip().upper() in IN_PROGRESS_GRADES)
    ]
    added = [
        CompletedCourse(
            code=c.code,
            term="Hypothetical",
            grade=request.assumed_grade,
            credits=c.credits,
            institution=None,
        )
        for c in applied
    ]
    simulated_record = record.model_copy(update={"completed": [*kept, *added]})

    strategy = MatcherStrategy.OPTIMAL_BIPARTITE
    before = run_audit(program, record, strategy=strategy)
    after = run_audit(program, simulated_record, strategy=strategy)
    plan_after = build_plan(program, simulated_record, after)

    passed_before = passed_courses(program, record.completed)
    passed_after = passed_courses(program, simulated_record.completed)
    eligible_before = {c.code for c in eligible_courses(program, passed_before)}
    unlocked = [
        c
        for c in eligible_courses(program, passed_after)
        if c.code not in eligible_before and c.code not in hypothetical
    ]

    before_blocks = {b.block_id: b for b in before.blocks}
    changes: list[BlockChange] = []
    for block in after.blocks:
        old = before_blocks[block.block_id]
        if (
            old.status is block.status
            and old.courses_applied == block.courses_applied
            and old.credits_applied == block.credits_applied
        ):
            continue
        changes.append(
            BlockChange(
                block_id=block.block_id,
                name=block.name,
                status_before=old.status,
                status_after=block.status,
                courses_applied_before=old.courses_applied,
                courses_applied_after=block.courses_applied,
                credits_applied_before=old.credits_applied,
                credits_applied_after=block.credits_applied,
            )
        )

    warnings: list[str] = []
    if applied:
        warnings.append(
            f"Assumes you pass {', '.join(sorted(hypothetical))} with at least a "
            f"{request.assumed_grade}. GPA is not projected."
        )
    if after.coverage is not None and not after.coverage.is_complete:
        warnings.append(
            "The encoded catalog is still partial, so requirements it does not describe "
            "are absent from this comparison, not failed."
        )
    for course in applied:
        if course.offered_as_needed:
            warnings.append(
                f"{course.code} is listed as offered irregularly - confirm it runs before "
                "relying on it."
            )

    snapshot_before, snapshot_after = _snapshot(before), _snapshot(after)
    return SimulationResult(
        mode=request.mode,
        assumed_grade=request.assumed_grade,
        applied=[_ref(c) for c in applied],
        skipped=skipped,
        before=snapshot_before,
        after=snapshot_after,
        credits_added=snapshot_after.credits_earned - snapshot_before.credits_earned,
        newly_satisfied_blocks=[
            c
            for c in changes
            if c.status_after is BlockStatus.SATISFIED
            and c.status_before is not BlockStatus.SATISFIED
        ],
        changed_blocks=changes,
        newly_unlocked=[_ref(c) for c in unlocked],
        still_blocked=[
            StillBlocked(course=r.course, missing_prerequisites=r.missing_prerequisites)
            for r in plan_after.blocked
        ],
        recommended_after=[r.course for r in plan_after.recommended],
        warnings=warnings,
    )
