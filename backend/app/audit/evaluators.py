"""Per-block evaluators.

One function per requirement type. Each takes a *pool* of the student's completed
courses and returns both a result and the pool indices it consumed, so the engine
can enforce the no-double-counting rule across blocks.

A course is eligible for a block only if all of these hold:

* the catalog knows the course (an unknown code on a transcript is ignored),
* its provenance is trusted for audit — never an unconfirmed AI extraction,
* its grade meets the block's minimum.

Where a requirement cannot be decided from the data we actually have, these
return NEEDS_ADVISOR. They never guess.

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/. ***
"""

from __future__ import annotations

from decimal import Decimal

from app.audit.matching import maximum_matching
from app.audit.record import CompletedCourse
from app.catalog.schema import (
    AllOfBlock,
    Course,
    CreditsFromBlock,
    EachOfBlock,
    GpaBlock,
    NOfBlock,
    Program,
    RequirementBlock,
    ResidencyBlock,
    grade_meets,
)
from app.schemas.audit import AppliedCourse, BlockStatus, CourseRef, RequirementBlockResult

#: Standard four-point scale. Morgan states minimums as "2.0 or better", which is
#: this scale. Suffixes are stripped (A- and A+ both count as A), matching
#: `grade_meets` in the catalog schema — see `gpa_points` for why that matters.
GRADE_POINTS: dict[str, Decimal] = {
    "A": Decimal(4),
    "B": Decimal(3),
    "C": Decimal(2),
    "D": Decimal(1),
    "F": Decimal(0),
}


def course_ref(course: Course) -> CourseRef:
    return CourseRef(
        code=course.code,
        subject=course.subject,
        number=course.number,
        title=course.title,
        credits=course.credits,
    )


def eligible_indices(
    program: Program,
    block: RequirementBlock,
    pool: list[CompletedCourse],
    allowed: set[str] | None = None,
) -> list[int]:
    """Pool positions that could legitimately count toward `block`."""
    out: list[int] = []
    for i, taken in enumerate(pool):
        if not taken.is_trusted:
            continue
        if program.course(taken.code) is None:
            continue
        if not grade_meets(taken.grade, block.min_grade):
            continue
        if allowed is not None and taken.code not in allowed:
            continue
        out.append(i)
    return out


def applied_course(program: Program, taken: CompletedCourse) -> AppliedCourse:
    """Build an AppliedCourse. Catalog credits win over whatever the transcript says."""
    course = program.course(taken.code)
    assert course is not None
    return AppliedCourse(
        course=course_ref(course),
        term=taken.term,
        grade=taken.grade,
        credits_applied=course.credits,
        provenance=taken.provenance,
    )


def _result(
    block: RequirementBlock,
    status: BlockStatus,
    applied: list[AppliedCourse],
    *,
    credits_required: Decimal,
    courses_required: int | None = None,
    still_needed: list[CourseRef] | None = None,
) -> RequirementBlockResult:
    if block.advisor_approval_required:
        status = BlockStatus.NEEDS_ADVISOR
    return RequirementBlockResult(
        block_id=block.id,
        name=block.name,
        status=status,
        credits_required=credits_required,
        credits_applied=sum((a.credits_applied for a in applied), Decimal(0)),
        courses_required=courses_required,
        courses_applied=len(applied) if courses_required is not None else None,
        applied=applied,
        still_needed=still_needed or [],
        note=block.note,
    )


def _status(done: int, needed: int) -> BlockStatus:
    if done >= needed:
        return BlockStatus.SATISFIED
    return BlockStatus.IN_PROGRESS if done else BlockStatus.UNMET


def _refs(program: Program, codes: list[str]) -> list[CourseRef]:
    return [course_ref(c) for code in codes if (c := program.course(code))]


# --------------------------------------------------------------------------- #
# all_of
# --------------------------------------------------------------------------- #
def evaluate_all_of(
    program: Program, block: AllOfBlock, pool: list[CompletedCourse]
) -> tuple[RequirementBlockResult, list[int]]:
    """Every listed course is required.

    `credits_required` is DERIVED by summing the listed courses, and may differ from
    a credit total the catalog prints for the same section. Morgan's "Supporting
    Courses 11 credits" sums to 15 here, because the catalog counts starred MATH241
    under General Education instead. The stated totals are disputed (open question
    Q1) and deliberately not encoded, so the derived figure is what we can defend -
    but it is not the catalog's number, and a UI should not present it as one.
    """
    required = list(dict.fromkeys(block.courses))
    eligible = eligible_indices(program, block, pool, allowed=set(required))

    consumed: list[int] = []
    applied: list[AppliedCourse] = []
    satisfied: set[str] = set()
    for i in eligible:
        code = pool[i].code
        if code in satisfied:
            continue  # a retake does not satisfy the requirement twice
        satisfied.add(code)
        consumed.append(i)
        applied.append(applied_course(program, pool[i]))

    missing = [c for c in required if c not in satisfied]
    credits_required = sum(
        (c.credits for code in required if (c := program.course(code))), Decimal(0)
    )
    return (
        _result(
            block,
            _status(len(satisfied), len(required)),
            applied,
            credits_required=credits_required,
            courses_required=len(required),
            still_needed=_refs(program, missing),
        ),
        consumed,
    )


# --------------------------------------------------------------------------- #
# n_of
# --------------------------------------------------------------------------- #
def evaluate_n_of(
    program: Program, block: NOfBlock, pool: list[CompletedCourse]
) -> tuple[RequirementBlockResult, list[int]]:
    choices = set(block.courses)
    eligible = eligible_indices(program, block, pool, allowed=choices)

    consumed: list[int] = []
    applied: list[AppliedCourse] = []
    used_codes: set[str] = set()
    for i in eligible:
        if len(consumed) >= block.n:
            break
        if pool[i].code in used_codes:
            continue
        used_codes.add(pool[i].code)
        consumed.append(i)
        applied.append(applied_course(program, pool[i]))

    remaining = [c for c in block.courses if c not in used_codes]
    cheapest = sorted(
        (c.credits for code in block.courses if (c := program.course(code))),
    )[: block.n]
    return (
        _result(
            block,
            _status(len(consumed), block.n),
            applied,
            credits_required=sum(cheapest, Decimal(0)),
            courses_required=block.n,
            still_needed=_refs(program, remaining) if len(consumed) < block.n else [],
        ),
        consumed,
    )


# --------------------------------------------------------------------------- #
# each_of
# --------------------------------------------------------------------------- #
def evaluate_each_of_pool(
    program: Program, block: EachOfBlock, pool: list[CompletedCourse]
) -> tuple[RequirementBlockResult, list[int]]:
    eligible = eligible_indices(program, block, pool)

    # candidates[g] = positions within `eligible` that could satisfy group g
    candidates: list[list[int]] = [
        [j for j, i in enumerate(eligible) if pool[i].code in group] for group in block.groups
    ]
    assignment = maximum_matching(candidates)

    consumed = [eligible[assignment[g]] for g in sorted(assignment)]
    applied = [applied_course(program, pool[i]) for i in consumed]

    unmatched = [g for g in range(len(block.groups)) if g not in assignment]
    still_needed = _refs(
        program, list(dict.fromkeys(code for g in unmatched for code in block.groups[g]))
    )

    credits_required = Decimal(0)
    for group in block.groups:
        credits = [c.credits for code in group if (c := program.course(code))]
        credits_required += min(credits) if credits else Decimal(0)

    return (
        _result(
            block,
            _status(len(assignment), len(block.groups)),
            applied,
            credits_required=credits_required,
            courses_required=len(block.groups),
            still_needed=still_needed,
        ),
        consumed,
    )


# --------------------------------------------------------------------------- #
# credits_from
# --------------------------------------------------------------------------- #
def evaluate_credits_from(
    program: Program, block: CreditsFromBlock, pool: list[CompletedCourse]
) -> tuple[RequirementBlockResult, list[int]]:
    allowed = {c.code for c in program.courses_for_block(block)}
    eligible = eligible_indices(program, block, pool, allowed=allowed)

    # Largest first, so the block consumes as few courses as possible and leaves
    # more of the pool available to later blocks.
    eligible.sort(key=lambda i: program.course(pool[i].code).credits, reverse=True)

    consumed: list[int] = []
    applied: list[AppliedCourse] = []
    total = Decimal(0)
    used_codes: set[str] = set()
    for i in eligible:
        if total >= block.credits_required:
            break
        if pool[i].code in used_codes:
            continue
        used_codes.add(pool[i].code)
        consumed.append(i)
        applied.append(applied_course(program, pool[i]))
        total += program.course(pool[i].code).credits

    status = (
        BlockStatus.SATISFIED
        if total >= block.credits_required
        else (BlockStatus.IN_PROGRESS if total > 0 else BlockStatus.UNMET)
    )
    remaining = sorted(allowed - used_codes)
    return (
        _result(
            block,
            status,
            applied,
            credits_required=block.credits_required,
            still_needed=_refs(program, remaining) if status is not BlockStatus.SATISFIED else [],
        ),
        consumed,
    )


# --------------------------------------------------------------------------- #
# gpa / residency - decided from the record, not from course assignment
# --------------------------------------------------------------------------- #
def gpa_points(courses: list[CompletedCourse]) -> Decimal | None:
    """Quality-point GPA over courses carrying a numeric grade.

    Returns None when nothing gradeable is present — the caller must treat that as
    "unknown", never as 0.0. Suffixes are stripped, so A- counts as A. That is the
    same simplification `grade_meets` already makes; if Morgan weights +/- grades,
    this under- and over-states by up to a third of a point and must be revisited.
    """
    quality = Decimal(0)
    credits = Decimal(0)
    for c in courses:
        grade = c.grade.strip().upper().rstrip("+-")
        if grade not in GRADE_POINTS:
            continue  # W, I, IP, NP, AU carry no quality points
        quality += GRADE_POINTS[grade] * c.credits
        credits += c.credits
    if credits == 0:
        return None
    return (quality / credits).quantize(Decimal("0.01"))


def evaluate_gpa(
    program: Program, block: GpaBlock, pool: list[CompletedCourse]
) -> tuple[RequirementBlockResult, list[int]]:
    trusted = [c for c in pool if c.is_trusted and program.course(c.code) is not None]
    if block.scope == "major":
        majors = {s.upper() for s in program.major_subjects}
        trusted = [c for c in trusted if program.course(c.code).subject in majors]

    actual = gpa_points(trusted)
    if actual is None:
        status = BlockStatus.UNMET
    else:
        status = BlockStatus.SATISFIED if actual >= block.min_gpa else BlockStatus.UNMET

    # GPA blocks consume no courses: a course counts toward GPA and toward a
    # requirement block at the same time. That is not double-counting.
    return (
        _result(block, status, [], credits_required=Decimal(0)),
        [],
    )


def evaluate_residency(
    program: Program, block: ResidencyBlock, pool: list[CompletedCourse]
) -> tuple[RequirementBlockResult, list[int]]:
    """Residency needs to know WHERE each course was taken.

    `CompletedCourse.institution` is optional and usually absent, because a
    transcript row does not always say. When it is missing the honest answer is
    NEEDS_ADVISOR — not an assumption that everything was taken in residence, which
    would silently pass a transfer student who has not met the requirement.
    """
    trusted = [c for c in pool if c.is_trusted and program.course(c.code) is not None]
    if any(c.institution is None for c in trusted) or not trusted:
        result = _result(block, BlockStatus.NEEDS_ADVISOR, [], credits_required=Decimal(0))
        return (
            result.model_copy(
                update={
                    "credits_required": block.min_credits_at_institution,
                    "note": block.note
                    or "Cannot be evaluated: the record does not say where these "
                    "courses were taken.",
                }
            ),
            [],
        )

    here = program.institution.strip().lower()
    earned = sum(
        (program.course(c.code).credits for c in trusted if c.institution.strip().lower() == here),
        Decimal(0),
    )
    status = (
        BlockStatus.SATISFIED
        if earned >= block.min_credits_at_institution
        else (BlockStatus.IN_PROGRESS if earned else BlockStatus.UNMET)
    )
    result = _result(block, status, [], credits_required=block.min_credits_at_institution)
    return result.model_copy(update={"credits_applied": earned}), []
