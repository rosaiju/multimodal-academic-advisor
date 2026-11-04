"""Course recommendations.

Turns an audit into advice: what is done, what is left, and what to take next.

Every recommendation here is DERIVED, not generated. A course is suggested because
the catalog says it satisfies an unmet requirement and the student is eligible for
it — reasons a human advisor could check line by line. The LLM layer may later
phrase this for a student; it may not decide it.

Ranking rules, applied in order and all deterministic:

1. **It must count.** A course that serves no unmet requirement is never
   recommended, however sensible it looks.
2. **The student must be eligible now.** Suggesting a course whose prerequisites
   are unmet wastes a term.
3. **Longest remaining chain first.** A course with three terms of prerequisites
   behind it has to start now or it moves graduation back a year. This is the
   single most valuable thing advising gets right.
4. **Then what it unlocks**, then fewest options — a requirement with one
   remaining way to satisfy it is more urgent than one with six.

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/. ***
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.audit.prereq_graph import (
    passed_courses,
    remaining_depth,
    unlocks,
    unmet_prerequisites,
)
from app.audit.record import StudentRecord
from app.catalog.schema import IN_PROGRESS_GRADES, Program, grade_meets
from app.schemas.audit import AuditResult, BlockStatus, CourseRef
from app.schemas.provenance import Provenance

#: A full-time term. Used only to group suggestions, never to cap what is offered.
DEFAULT_TERM_CREDITS = Decimal(15)


class RequirementGap(BaseModel):
    """One requirement a student has not finished, and what would finish it."""

    block_id: str
    name: str
    status: BlockStatus
    courses_still_needed: int | None = None
    credits_still_needed: Decimal | None = None
    options: list[CourseRef] = Field(
        default_factory=list, description="Courses that could satisfy the remainder"
    )
    note: str | None = None


class Recommendation(BaseModel):
    """One course to consider, with the reasons it was chosen."""

    course: CourseRef
    serves: list[str] = Field(description="Requirement block ids this would advance")
    eligible_now: bool
    missing_prerequisites: list[str] = Field(default_factory=list)
    remaining_chain: int = Field(
        description="Terms of chained prerequisites still ahead, including this course"
    )
    unlocks_count: int = Field(description="Courses that list this as a prerequisite")
    reasons: list[str] = Field(description="Plain-English justification, catalog-derived")
    warnings: list[str] = Field(default_factory=list)

    #: Always VERIFIED - derived from catalog data by deterministic rules.
    provenance: Provenance = Provenance.VERIFIED


class AdvisingPlan(BaseModel):
    """The full advising picture for one student."""

    student_id: str
    program_id: str
    catalog_year: str

    completed: list[CourseRef] = Field(default_factory=list)
    completed_credits: Decimal = Decimal(0)

    gaps: list[RequirementGap] = Field(default_factory=list)
    recommended: list[Recommendation] = Field(default_factory=list)
    blocked: list[Recommendation] = Field(
        default_factory=list,
        description="Would count, but prerequisites are not met yet.",
    )
    under_way: list[CourseRef] = Field(
        default_factory=list,
        description="Confirmed as in progress right now. Not recommended - the "
        "student is already sitting in them - and not yet counted either.",
    )

    #: Carried from the audit. Advice off a partial catalog is partial advice.
    coverage: str | None = None
    caveats: list[str] = Field(default_factory=list)


def blocks_needing_retake(program: Program, code: str, grade: str) -> list[str]:
    """Requirement blocks that list `code` but whose minimum `grade` does not meet.

    A D in COSC111 earns credit, and Morgan's major block asks for a C. The course
    is "passed" for credit and still has to be taken again for the requirement.
    Returns block names, in catalog order; empty when no block needs a retake.
    """
    out: list[str] = []
    for block in program.requirement_blocks:
        if not hasattr(block, "courses") and getattr(block, "course_filter", None) is None:
            continue  # gpa / residency blocks list no courses
        if code not in {c.code for c in program.courses_for_block(block)}:
            continue
        if not grade_meets(grade, block.min_grade):
            out.append(block.name)
    return out


def _gap(program: Program, block_result) -> RequirementGap:
    still_needed = None
    if block_result.courses_required is not None and block_result.courses_applied is not None:
        still_needed = max(block_result.courses_required - block_result.courses_applied, 0)

    credits_left = None
    if block_result.credits_required and block_result.status is not BlockStatus.SATISFIED:
        credits_left = max(block_result.credits_required - block_result.credits_applied, Decimal(0))

    return RequirementGap(
        block_id=block_result.block_id,
        name=block_result.name,
        status=block_result.status,
        courses_still_needed=still_needed,
        credits_still_needed=credits_left,
        options=list(block_result.still_needed),
        note=block_result.note,
    )


def build_plan(
    program: Program,
    record: StudentRecord,
    audit: AuditResult,
    *,
    limit: int = 8,
) -> AdvisingPlan:
    """Produce advice from a completed audit.

    Takes the audit rather than recomputing one, so the plan and the progress
    report can never disagree about what a student has finished.
    """
    passed = passed_courses(program, record.completed)

    # Courses the student is SITTING IN. Deliberately separate from `passed`:
    # an unfinished course must not satisfy a prerequisite, but recommending one a
    # student is already enrolled in is equally wrong advice. Filtering only on
    # `passed` did exactly that, because an IP grade is non-passing.
    in_progress_codes = {
        c.code
        for c in record.completed
        if c.is_trusted and c.grade.strip().upper() in IN_PROGRESS_GRADES
    }

    completed_refs = [a.course for b in audit.blocks for a in b.applied]
    applied_codes = {r.code for r in completed_refs}
    block_min = {b.id: b.min_grade for b in program.requirement_blocks}
    block_names = {b.block_id: b.name for b in audit.blocks}
    #: Courses passed for credit but below a still-open block's minimum grade:
    #: code -> (grade on record, minimum it falls short of).
    retakes: dict[str, tuple[str, str]] = {}
    completed_credits = sum((r.credits for r in completed_refs), Decimal(0))

    unfinished = [
        b
        for b in audit.blocks
        if b.status in (BlockStatus.UNMET, BlockStatus.IN_PROGRESS, BlockStatus.NEEDS_ADVISOR)
    ]
    gaps = [_gap(program, b) for b in unfinished]

    # Which unmet blocks each candidate course would advance.
    serves: dict[str, list[str]] = {}
    #: Courses that some unfinished block requires outright, as opposed to listing
    #: them as one option among several ("pick one of these five").
    required_outright: set[str] = set()
    for block in unfinished:
        outright = (
            block.courses_required is not None and len(block.still_needed) <= block.courses_required
        )
        for ref in block.still_needed:
            if ref.code in in_progress_codes or ref.code in applied_codes:
                continue
            if ref.code in passed:
                minimum = block_min.get(block.block_id, "D")
                if grade_meets(passed[ref.code], minimum):
                    continue
                # Passed for credit, below this block's minimum: a retake. Before
                # this, `passed` alone excluded it, so a student with a D in a
                # "C or better" course was never told to take it again.
                retakes.setdefault(ref.code, (passed[ref.code], minimum))
            serves.setdefault(ref.code, []).append(block.block_id)
            if outright:
                required_outright.add(ref.code)

    recommended: list[Recommendation] = []
    blocked: list[Recommendation] = []

    for code, block_ids in serves.items():
        course = program.course(code)
        if course is None:
            continue

        missing = unmet_prerequisites(program, code, passed)
        eligible = not missing
        chain = remaining_depth(program, code, passed)
        if code in retakes:
            chain = max(chain, 1)  # remaining_depth calls a passed course done
        unlocked = unlocks(program, code)

        # Students read these. Block NAMES here; ids stay in the structured `serves`.
        names = [block_names.get(b, b) for b in block_ids]
        reasons = [
            f"required by {len(block_ids)} unfinished requirement"
            + ("s" if len(block_ids) > 1 else "")
            + f": {', '.join(names)}"
        ]
        if code in retakes:
            have, need = retakes[code]
            reasons.append(
                f"retake: your grade of {have} is below the {need} this requirement needs"
            )
        if chain > 1:
            reasons.append(
                f"{chain} terms of prerequisites still ahead - starting later delays graduation"
            )
        if unlocked:
            reasons.append(f"unlocks {len(unlocked)} later course(s): {', '.join(unlocked[:4])}")
        if not eligible:
            reasons.append("prerequisites not met yet: " + ", ".join(missing))

        warnings: list[str] = []
        if course.offered_as_needed:
            warnings.append(
                "the catalog lists this as offered irregularly - confirm it runs before "
                "relying on it"
            )

        entry = Recommendation(
            course=CourseRef(
                code=course.code,
                subject=course.subject,
                number=course.number,
                title=course.title,
                credits=course.credits,
            ),
            serves=sorted(block_ids),
            eligible_now=eligible,
            missing_prerequisites=missing,
            remaining_chain=chain,
            unlocks_count=len(unlocked),
            reasons=reasons,
            warnings=warnings,
        )
        (recommended if eligible else blocked).append(entry)

    def rank(entry: Recommendation) -> tuple:
        # Longest chain first, then most unlocked, then courses a block requires
        # outright ahead of mere options (otherwise a long "pick one" list wins
        # every tie alphabetically and crowds required courses out), then most
        # requirements served, then code for a stable order.
        return (
            -entry.remaining_chain,
            -entry.unlocks_count,
            entry.course.code not in required_outright,
            -len(entry.serves),
            entry.course.code,
        )

    recommended.sort(key=rank)
    blocked.sort(key=rank)

    under_way = [
        CourseRef(
            code=course.code,
            subject=course.subject,
            number=course.number,
            title=course.title,
            credits=course.credits,
        )
        for code in sorted(in_progress_codes)
        if (course := program.course(code)) is not None
    ]

    caveats: list[str] = []
    if under_way:
        caveats.append(
            f"{len(under_way)} course(s) are in progress and are left out of the "
            "recommendations below. They are not counted as done either - they "
            "satisfy nothing until a final grade lands."
        )
    if audit.coverage is not None and not audit.coverage.is_complete:
        caveats.append(
            "This plan covers only the requirements currently encoded in the catalog. "
            "Requirements still awaiting department clarification are absent, not met - "
            "check with an advisor before relying on it."
        )
    if not recommended and blocked:
        caveats.append(
            "Nothing is available to take right now: every remaining course has "
            "prerequisites still outstanding."
        )

    return AdvisingPlan(
        student_id=record.student_id,
        program_id=program.program_id,
        catalog_year=program.catalog_year,
        completed=sorted(completed_refs, key=lambda r: r.code),
        completed_credits=completed_credits,
        under_way=sorted(under_way, key=lambda r: r.code),
        gaps=gaps,
        recommended=recommended[:limit],
        blocked=blocked[:limit],
        coverage=audit.coverage.summary if audit.coverage is not None else None,
        caveats=caveats,
    )
