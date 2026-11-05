"""Side-by-side comparison of courses, for one student.

"Which elective should I take?" is mostly a question of facts a student cannot see
at a glance: can I take it now, does it count toward anything I still need, what does
it open up, how long is the road to it. This module lays those facts next to each
other and says where they differ.

Everything is derived from the catalog and the student's trusted record. It ranks
nothing and recommends nothing beyond what the facts show: which course to take is
the student's decision, and a human advisor's to discuss.

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/. ***
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.audit.planning import blocks_needing_retake
from app.audit.prereq_graph import passed_courses, remaining_depth, unlocks, unmet_prerequisites
from app.audit.record import StudentRecord
from app.catalog.schema import IN_PROGRESS_GRADES, Program
from app.schemas.audit import AuditResult, BlockStatus
from app.schemas.provenance import Provenance

MIN_COURSES = 2
MAX_COURSES = 4

CourseState = Literal["passed", "in_progress", "eligible_now", "blocked"]


class ServedRequirement(BaseModel):
    block_id: str
    name: str
    #: True when the requirement is still open, so this course would advance it.
    still_open: bool


class CourseFacts(BaseModel):
    code: str
    title: str
    credits: Decimal
    prerequisites: list[str]
    state: CourseState
    missing_prerequisites: list[str] = Field(default_factory=list)
    serves: list[ServedRequirement] = Field(default_factory=list)
    advances_open_requirement: bool
    unlocks: list[str] = Field(default_factory=list)
    #: Terms of chained prerequisites still ahead, this course included.
    remaining_chain: int
    terms_offered: list[str] = Field(default_factory=list)
    offered_as_needed: bool
    #: True when the catalog says nothing about which terms the course runs.
    terms_assumed: bool
    #: Passed for credit, but below the minimum grade of a requirement that lists
    #: it - so it must be taken again. `state` stays "passed" (credit was earned).
    retake_needed: bool = False
    warnings: list[str] = Field(default_factory=list)


class CourseComparison(BaseModel):
    courses: list[CourseFacts]
    #: Plain statements of where the courses differ. Empty when they don't.
    differences: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    provenance: Provenance = Provenance.VERIFIED


class ComparisonError(ValueError):
    """A request the comparison cannot answer."""


def _readable_prereqs(course) -> list[str]:
    out = list(course.prerequisites)
    out.extend(" or ".join(group) for group in course.prerequisite_groups)
    return out


def _facts(
    program: Program,
    record: StudentRecord,
    audit: AuditResult,
    code: str,
    passed: dict[str, str],
    in_progress: set[str],
) -> CourseFacts:
    course = program.course(code)
    assert course is not None
    status = {b.block_id: b.status for b in audit.blocks}

    serves: list[ServedRequirement] = []
    for block in program.requirement_blocks:
        if block.id not in status:
            continue
        if code in {c.code for c in program.courses_for_block(block)}:
            open_ = status[block.id] is not BlockStatus.SATISFIED
            serves.append(ServedRequirement(block_id=block.id, name=block.name, still_open=open_))

    missing = unmet_prerequisites(program, code, passed)
    applied = {a.course.code for b in audit.blocks for a in b.applied}
    retake_for: list[str] = []
    if code in passed and code not in applied and code not in in_progress:
        open_names = {s.name for s in serves if s.still_open}
        retake_for = [
            n for n in blocks_needing_retake(program, code, passed[code]) if n in open_names
        ]
    if code in passed:
        state: CourseState = "passed"
    elif code in in_progress:
        state = "in_progress"
    elif missing:
        state = "blocked"
    else:
        state = "eligible_now"

    warnings: list[str] = []
    if retake_for:
        warnings.append(
            f"Your {passed[code]} is below the minimum grade for {', '.join(retake_for)}, "
            "so this needs to be taken again."
        )
    if course.offered_as_needed:
        warnings.append("Offered irregularly. Confirm it runs before relying on it.")
    if course.title.upper().startswith("UNVERIFIED"):
        warnings.append("The catalog has no verified title or credits for this course.")

    return CourseFacts(
        code=code,
        title=course.title,
        credits=course.credits,
        prerequisites=_readable_prereqs(course),
        state=state,
        missing_prerequisites=missing if state == "blocked" else [],
        serves=serves,
        advances_open_requirement=bool(retake_for)
        or (state not in ("passed", "in_progress") and any(s.still_open for s in serves)),
        unlocks=unlocks(program, code),
        remaining_chain=remaining_depth(program, code, passed),
        terms_offered=[t.value for t in course.terms_offered],
        offered_as_needed=course.offered_as_needed,
        terms_assumed="terms_offered" not in course.model_fields_set,
        warnings=warnings,
        retake_needed=bool(retake_for),
    )


def _join(codes: list[str]) -> str:
    return codes[0] if len(codes) == 1 else ", ".join(codes[:-1]) + " and " + codes[-1]


def _differences(facts: list[CourseFacts]) -> list[str]:
    out: list[str] = []

    can = [f.code for f in facts if f.state == "eligible_now"]
    blocked = [f for f in facts if f.state == "blocked"]
    if can and blocked:
        out.append(f"You can take {_join(can)} now.")
        for f in blocked:
            out.append(f"{f.code} needs {', '.join(f.missing_prerequisites)} first.")
    for f in facts:
        if f.state == "passed" and f.retake_needed:
            out.append(f"You passed {f.code}, but below a requirement's minimum grade - retake it.")
        elif f.state == "passed":
            out.append(f"You've already passed {f.code}.")
        elif f.state == "in_progress":
            out.append(f"You're taking {f.code} now.")

    counts = [f.code for f in facts if f.advances_open_requirement]
    nothing = [
        f.code
        for f in facts
        if not f.advances_open_requirement and f.state not in ("passed", "in_progress")
    ]
    counts = [c for c in counts if c not in {f.code for f in facts if f.retake_needed}]
    if counts and nothing:
        out.append(
            f"{_join(counts)} would advance a requirement you still need. "
            f"{_join(nothing)} wouldn't advance one that's still open."
        )

    unlock_counts = {f.code: len(f.unlocks) for f in facts}
    if len(set(unlock_counts.values())) > 1:
        top = max(unlock_counts.values())
        winners = [c for c, n in unlock_counts.items() if n == top]
        out.append(f"{_join(winners)} unlocks the most later courses ({top}).")

    chains = {
        f.code: f.remaining_chain
        for f in facts
        if f.state not in ("passed", "in_progress") or f.retake_needed
    }
    if len(set(chains.values())) > 1:
        longest = max(chains.values())
        out.append(
            f"{_join([c for c, n in chains.items() if n == longest])} has the longest road "
            f"to it ({longest} terms of prerequisites, itself included)."
        )

    credits = {f.code: f.credits for f in facts}
    if len(set(credits.values())) > 1:
        out.append("Credits differ: " + ", ".join(f"{c} is {n}" for c, n in credits.items()) + ".")
    return out


def compare_courses(
    program: Program,
    record: StudentRecord,
    audit: AuditResult,
    codes: list[str],
) -> CourseComparison:
    """Compare 2 to 4 catalog courses for one student."""
    cleaned = list(dict.fromkeys(c.replace(" ", "").upper() for c in codes))
    if not MIN_COURSES <= len(cleaned) <= MAX_COURSES:
        raise ComparisonError(f"Pick between {MIN_COURSES} and {MAX_COURSES} different courses.")
    unknown = [c for c in cleaned if program.course(c) is None]
    if unknown:
        raise ComparisonError(f"Not in this program's catalog: {', '.join(unknown)}.")

    passed = passed_courses(program, record.completed)
    in_progress = {
        c.code
        for c in record.completed
        if c.is_trusted and c.grade.strip().upper() in IN_PROGRESS_GRADES
    }
    facts = [_facts(program, record, audit, c, passed, in_progress) for c in cleaned]

    caveats: list[str] = []
    if audit.coverage is not None and not audit.coverage.is_complete:
        caveats.append(
            "Requirements still awaiting department clarification aren't encoded, so a "
            "course that counts toward none of the encoded ones may still count toward "
            "one that isn't. Ask an advisor before ruling a course out."
        )
    if any(f.terms_assumed for f in facts):
        caveats.append(
            "The catalog doesn't record which terms most courses run. Check the schedule "
            "of classes before registering."
        )

    return CourseComparison(courses=facts, differences=_differences(facts), caveats=caveats)
