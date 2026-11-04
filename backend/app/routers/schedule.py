"""Semester plan endpoints.

A thin shell over `app.audit.schedule`. No scheduling rule lives in this file, and
nothing in it may import the LLM layer: where a course lands is computed, never
generated.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.audit.schedule import (
    DEFAULT_CREDIT_TARGET,
    SchedulePlan,
    ScheduleValidation,
    TermError,
    generate_schedule,
    validate_schedule,
)
from app.auth.dependencies import AuthorisedStudentId
from app.ingestion.store import StoredRecordError
from app.routers.audit import _program, _store

router = APIRouter(tags=["schedule"])


class ScheduleRequest(BaseModel):
    program_id: str | None = Field(
        default=None, description="Defaults to the program stored on the record."
    )
    start_term: str | None = Field(
        default=None, description="First term to plan, e.g. 'Spring 2027'. Defaults from today."
    )
    credit_target: Decimal = Field(default=DEFAULT_CREDIT_TARGET, ge=1, le=24)
    include_summer: bool = False


class LayoutTerm(BaseModel):
    term: str
    courses: list[str]


class ValidateRequest(BaseModel):
    program_id: str | None = None
    terms: list[LayoutTerm]
    credit_target: Decimal = Field(default=DEFAULT_CREDIT_TARGET, ge=1, le=24)


def _load(student_id: str, program_id: str | None):
    try:
        stored = _store().load(student_id)
    except StoredRecordError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    resolved = program_id or stored.program_id
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail="no program on record and none supplied. Pass program_id.",
        )
    return _program(resolved), stored.to_student_record()


@router.post("/students/{student_id}/schedule", response_model=SchedulePlan)
def make_schedule(student_id: AuthorisedStudentId, body: ScheduleRequest) -> SchedulePlan:
    """Lay the remaining required courses out across future terms."""
    program, record = _load(student_id, body.program_id)
    try:
        return generate_schedule(
            program,
            record,
            start_term=body.start_term,
            credit_target=body.credit_target,
            include_summer=body.include_summer,
        )
    except TermError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/students/{student_id}/schedule/validate", response_model=ScheduleValidation)
def check_schedule(student_id: AuthorisedStudentId, body: ValidateRequest) -> ScheduleValidation:
    """Check a hand-edited layout against the same rules the generator follows."""
    program, record = _load(student_id, body.program_id)
    return validate_schedule(
        program,
        record,
        [(t.term, t.courses) for t in body.terms],
        credit_target=body.credit_target,
    )
