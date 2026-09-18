"""Read-only catalog endpoints.

SCOPE: this router only reads catalog data. It never touches a student record, a
degree audit, or the LLM. Person 4 owns the chat, audit, and student routers plus
all of the frontend; this file is a deliberate carve-out for the catalog layer and
should stay small.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.catalog.explain import (
    CourseSummary,
    ProgramSummary,
    describe_program,
    prerequisite_depth,
    prerequisite_tree,
)
from app.catalog.loader import CatalogError
from app.catalog.registry import registry

router = APIRouter(prefix="/catalog", tags=["catalog"])


def _program(program_id: str):
    try:
        return registry.get(program_id)
    except CatalogError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{program_id}/requirements", response_model=ProgramSummary)
def get_requirements(program_id: str) -> ProgramSummary:
    """Every requirement block for a program, stated in plain English.

    This is official catalog data, not a model's summary of it. Each block reports
    what it asks for and which courses could satisfy it — but says nothing about
    whether any particular student has met it.
    """
    return describe_program(_program(program_id))


@router.get("/{program_id}/courses", response_model=list[CourseSummary])
def list_courses(program_id: str, subject: str | None = None) -> list[CourseSummary]:
    """Every course in the catalog, optionally filtered by subject."""
    program = _program(program_id)
    courses = program.courses
    if subject:
        wanted = subject.upper()
        courses = [c for c in courses if c.subject == wanted]
    return [CourseSummary.of(c) for c in courses]


@router.get("/{program_id}/courses/{code}/prerequisites")
def get_prerequisites(program_id: str, code: str) -> dict:
    """The prerequisite tree for one course, expanded for display."""
    program = _program(program_id)
    course_code = code.upper()
    if program.course(course_code) is None:
        raise HTTPException(status_code=404, detail=f"unknown course {course_code!r}")
    return {
        "tree": prerequisite_tree(program, course_code),
        "depth": prerequisite_depth(program, course_code),
    }
