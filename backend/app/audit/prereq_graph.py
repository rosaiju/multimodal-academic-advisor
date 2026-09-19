"""Prerequisite graph analysis.

`app/catalog/explain.py` expands prerequisites for DISPLAY. This module does the
real work: who is eligible for what, what a course unlocks, and how many terms of
chained prerequisites still stand between a student and a course.

The distinction that matters for advising: a course a student cannot take yet is
not a recommendation, it is a trap. And a course with a long chain behind it has
to be started early or it pushes graduation back a year, which is the single most
useful thing an advisor tells anyone.

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/. ***
"""

from __future__ import annotations

from app.audit.record import CompletedCourse
from app.catalog.schema import Course, Program, grade_meets


def passed_courses(
    program: Program,
    completed: list[CompletedCourse],
    *,
    min_grade: str = "D",
) -> dict[str, str]:
    """Course code -> best trusted grade, for courses the catalog knows.

    Only trusted coursework counts, so an unconfirmed transcript extraction can no
    more make a student eligible for a course than it can satisfy a requirement.
    """
    best: dict[str, str] = {}
    for taken in completed:
        if not taken.is_trusted or program.course(taken.code) is None:
            continue
        if not grade_meets(taken.grade, min_grade):
            continue
        current = best.get(taken.code)
        if current is None or grade_meets(taken.grade, current):
            best[taken.code] = taken.grade
    return best


def unmet_prerequisites(program: Program, code: str, passed: dict[str, str]) -> list[str]:
    """Prerequisites of `code` the student has not satisfied.

    Honours the course's own `min_prereq_grade`: Morgan requires C or better in
    most prerequisites, and a D that would satisfy a requirement block does not
    make you eligible for the next course.
    """
    course = program.course(code)
    if course is None:
        return []

    missing: list[str] = []
    for required in course.prerequisites:
        grade = passed.get(required)
        if grade is None or not grade_meets(grade, course.min_prereq_grade):
            missing.append(required)

    # Each OR-group needs at least one member satisfied.
    for group in course.prerequisite_groups:
        if not any(
            (grade := passed.get(option)) is not None
            and grade_meets(grade, course.min_prereq_grade)
            for option in group
        ):
            missing.append(" or ".join(group))

    return missing


def is_eligible(program: Program, code: str, passed: dict[str, str]) -> bool:
    """True when every prerequisite is satisfied. Unknown courses are not eligible."""
    return program.course(code) is not None and not unmet_prerequisites(program, code, passed)


def unlocks(program: Program, code: str) -> list[str]:
    """Courses that name `code` as a prerequisite, directly."""
    out: list[str] = []
    for course in program.courses:
        direct = set(course.prerequisites)
        for group in course.prerequisite_groups:
            direct.update(group)
        if code in direct:
            out.append(course.code)
    return sorted(out)


def chain_depth(program: Program, code: str, _seen: frozenset[str] | None = None) -> int:
    """Longest prerequisite chain ending at `code`, counting `code` itself.

    COSC111 is depth 1; a course three prerequisites deep is depth 4. Cycles are
    truncated rather than raising, so a malformed catalog still yields a number.
    """
    seen = _seen or frozenset()
    course = program.course(code)
    if course is None or code in seen:
        return 0

    nested = seen | {code}
    options: list[str] = list(course.prerequisites)
    for group in course.prerequisite_groups:
        options.extend(group)
    if not options:
        return 1
    return 1 + max(chain_depth(program, p, nested) for p in options)


def remaining_depth(
    program: Program, code: str, passed: dict[str, str], _seen: frozenset[str] | None = None
) -> int:
    """Terms of chained prerequisites still ahead, given what is already done.

    This, not `chain_depth`, is what drives advice. A student who has finished
    COSC220 faces a much shorter road to COSC456 than the raw catalog depth
    suggests, and telling them otherwise is discouraging and wrong.
    """
    seen = _seen or frozenset()
    course = program.course(code)
    if course is None or code in seen:
        return 0
    if code in passed:
        return 0

    nested = seen | {code}
    missing = unmet_prerequisites(program, code, passed)
    concrete = [m for m in missing if " or " not in m]
    if not concrete:
        return 1
    return 1 + max(remaining_depth(program, m, passed, nested) for m in concrete)


def eligible_courses(program: Program, passed: dict[str, str]) -> list[Course]:
    """Every catalog course the student could register for now and has not passed."""
    return [
        c for c in program.courses if c.code not in passed and is_eligible(program, c.code, passed)
    ]
