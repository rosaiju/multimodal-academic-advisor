"""Guidance endpoints: why a requirement is open, how courses compare, what's due.

Thin shells over `app.audit.explain_audit`, `app.audit.compare` and
`app.audit.deadlines`. Every statement is computed from the catalog, the student's
confirmed record and the sourced calendar. Nothing in this file may import the LLM
layer.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from app.audit.compare import ComparisonError, CourseComparison, compare_courses
from app.audit.deadlines import (
    Calendar,
    CalendarError,
    DeadlineReport,
    build_deadlines,
    load_calendar,
    to_ics,
)
from app.audit.engine import run_audit
from app.audit.explain_audit import BlockExplanation, BlockNotFound, explain_all, explain_block
from app.auth.dependencies import AuthorisedStudentId
from app.config import REPO_ROOT
from app.ingestion.store import StoredRecordError
from app.routers.audit import _program, _store
from app.schemas.audit import MatcherStrategy

router = APIRouter(tags=["guidance"])

CALENDAR_FILE = REPO_ROOT / "data" / "calendar" / "morgan_2026_2027.yaml"


@lru_cache(maxsize=1)
def calendar() -> Calendar:
    """The parsed calendar. Raises CalendarError if the file is malformed."""
    return load_calendar(CALENDAR_FILE)


def _context(student_id: str, program_id: str | None):
    try:
        stored = _store().load(student_id)
    except StoredRecordError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    resolved = program_id or stored.program_id
    if resolved is None:
        raise HTTPException(
            status_code=400, detail="no program on record and none supplied. Pass program_id."
        )
    program = _program(resolved)
    record = stored.to_student_record()
    return program, record, run_audit(program, record, MatcherStrategy.OPTIMAL_BIPARTITE)


@router.get("/students/{student_id}/audit/explain", response_model=list[BlockExplanation])
def explain_every_block(
    student_id: AuthorisedStudentId, program_id: Annotated[str | None, Query()] = None
) -> list[BlockExplanation]:
    """Why each requirement is met or open, in plain language."""
    program, record, audit = _context(student_id, program_id)
    return explain_all(program, record, audit)


@router.get("/students/{student_id}/audit/explain/{block_id}", response_model=BlockExplanation)
def explain_one_block(
    student_id: AuthorisedStudentId,
    block_id: str,
    program_id: Annotated[str | None, Query()] = None,
) -> BlockExplanation:
    program, record, audit = _context(student_id, program_id)
    try:
        return explain_block(program, record, audit, block_id)
    except BlockNotFound as exc:
        raise HTTPException(status_code=404, detail=f"no requirement {block_id!r}") from exc


@router.get("/students/{student_id}/compare", response_model=CourseComparison)
def compare(
    student_id: AuthorisedStudentId,
    codes: Annotated[list[str], Query(description="2 to 4 course codes, e.g. COSC351")],
    program_id: Annotated[str | None, Query()] = None,
) -> CourseComparison:
    """Put 2 to 4 courses side by side, for this student."""
    program, record, audit = _context(student_id, program_id)
    try:
        return compare_courses(program, record, audit, codes)
    except ComparisonError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _deadline_report(student_id: str, program_id: str | None, today: date | None):
    _, _, audit = _context(student_id, program_id)
    try:
        return build_deadlines(calendar(), audit.total_credits_earned, today=today)
    except CalendarError as exc:  # pragma: no cover - the file is checked at startup
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/students/{student_id}/deadlines", response_model=DeadlineReport)
def get_deadlines(
    student_id: AuthorisedStudentId,
    program_id: Annotated[str | None, Query()] = None,
    today: Annotated[date | None, Query(description="Override today's date.")] = None,
) -> DeadlineReport:
    """Registration and commencement dates, personalised to this student's standing."""
    return _deadline_report(student_id, program_id, today)


@router.get("/students/{student_id}/deadlines.ics")
def get_deadlines_ics(
    student_id: AuthorisedStudentId,
    program_id: Annotated[str | None, Query()] = None,
    today: Annotated[date | None, Query()] = None,
) -> Response:
    """The same dates as a calendar file, each with a one-day-before reminder."""
    body = to_ics(_deadline_report(student_id, program_id, today))
    return Response(
        content=body,
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="deadlines.ics"'},
    )
