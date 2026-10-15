"""Course unlock / impact explorer: what a course opens up, and what it needs.

Built entirely on `prereq_graph` (`unlocks`, `unmet_prerequisites`,
`passed_courses`) - the same functions the planner uses - so this can never
disagree with a recommendation about who is eligible for what.

For one course it returns the course's own prerequisite tree (what you need),
the courses it directly unlocks, the courses that depend on it further down, and
for each of those whether THIS student meets the rest of the prerequisites.

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/. ***
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.audit.prereq_graph import is_eligible, passed_courses, unlocks, unmet_prerequisites
from app.audit.record import StudentRecord
from app.audit.simulation import canonical_code
from app.catalog.schema import IN_PROGRESS_GRADES, Course, Program
from app.schemas.audit import CourseRef

StudentStatus = Literal["completed", "in_progress", "eligible", "blocked"]

#: Safety net for a malformed catalog. Real chains are a handful deep.
MAX_DEPTH = 10


class PrereqNode(BaseModel):
    """One node of a prerequisite tree. An `any_of` node has no code of its own:
    its children are alternatives and one of them is enough."""

    code: str | None = None
    title: str | None = None
    status: StudentStatus | None = None
    any_of: bool = False
    children: list[PrereqNode] = Field(default_factory=list)


class UnlockEntry(BaseModel):
    course: CourseRef
    status: StudentStatus
    #: Prerequisites the student is missing for this course today.
    missing_prerequisites: list[str]
    #: Prerequisites still missing if the student completed the explored course.
    #: Empty means "that course is the only thing standing in the way".
    missing_after: list[str]
    #: Chain of codes from the explored course down to this one.
    path: list[str]
    depth: int


class UnlockReport(BaseModel):
    code: str
    known: bool
    course: CourseRef | None = None
    status: StudentStatus | None = None
    prerequisite_tree: PrereqNode | None = None
    direct_unlocks: list[UnlockEntry] = Field(default_factory=list)
    downstream_unlocks: list[UnlockEntry] = Field(default_factory=list)
    #: Edges of the explored subgraph (prerequisite -> course) for drawing.
    edges: list[tuple[str, str]] = Field(default_factory=list)


def _ref(course: Course) -> CourseRef:
    return CourseRef(
        code=course.code,
        subject=course.subject,
        number=course.number,
        title=course.title,
        credits=course.credits,
    )


def _status(
    program: Program, code: str, passed: dict[str, str], in_progress: set[str]
) -> StudentStatus:
    if code in passed:
        return "completed"
    if code in in_progress:
        return "in_progress"
    return "eligible" if is_eligible(program, code, passed) else "blocked"


def _tree(
    program: Program,
    code: str,
    passed: dict[str, str],
    in_progress: set[str],
    trail: frozenset[str],
    depth: int,
) -> PrereqNode:
    course = program.course(code)
    node = PrereqNode(
        code=code,
        title=course.title if course else None,
        status=_status(program, code, passed, in_progress) if course else None,
    )
    if course is None or code in trail or depth >= MAX_DEPTH:
        return node
    below = trail | {code}
    node.children = [
        _tree(program, p, passed, in_progress, below, depth + 1) for p in course.prerequisites
    ]
    for group in course.prerequisite_groups:
        node.children.append(
            PrereqNode(
                any_of=True,
                children=[_tree(program, p, passed, in_progress, below, depth + 1) for p in group],
            )
        )
    return node


def explore_unlocks(program: Program, record: StudentRecord, raw_code: str) -> UnlockReport:
    """Everything `raw_code` opens up, and whether this student can get there."""
    code = canonical_code(raw_code)
    course = program.course(code)
    if course is None:
        return UnlockReport(code=code, known=False)

    passed = passed_courses(program, record.completed)
    in_progress = {
        c.code
        for c in record.completed
        if c.is_trusted and c.grade.strip().upper() in IN_PROGRESS_GRADES
    }
    # The student as they would be after completing `code`; used for "missing_after".
    after = {**passed, code: course.min_prereq_grade or "D"}

    def entry(target: str, path: list[str]) -> UnlockEntry:
        found = program.course(target)
        assert found is not None  # `unlocks()` only returns catalog courses
        return UnlockEntry(
            course=_ref(found),
            status=_status(program, target, passed, in_progress),
            missing_prerequisites=unmet_prerequisites(program, target, passed),
            missing_after=unmet_prerequisites(program, target, after),
            path=path,
            depth=len(path) - 1,
        )

    direct: list[UnlockEntry] = []
    downstream: list[UnlockEntry] = []
    edges: list[tuple[str, str]] = []
    seen = {code}
    frontier = [(code, [code])]
    while frontier:
        parent, path = frontier.pop(0)
        if len(path) > MAX_DEPTH:
            continue
        for child in unlocks(program, parent):
            edges.append((parent, child))
            if child in seen:
                continue
            seen.add(child)
            item = entry(child, [*path, child])
            (direct if item.depth == 1 else downstream).append(item)
            frontier.append((child, [*path, child]))

    return UnlockReport(
        code=code,
        known=True,
        course=_ref(course),
        status=_status(program, code, passed, in_progress),
        prerequisite_tree=_tree(program, code, passed, in_progress, frozenset(), 0),
        direct_unlocks=direct,
        downstream_unlocks=downstream,
        edges=edges,
    )
