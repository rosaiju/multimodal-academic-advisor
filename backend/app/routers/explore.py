"""What-if simulation and course-unlock endpoints.

Like the rest of the advisor routes, the student id comes from the access token,
never from the body. Both endpoints only READ the stored record: the simulation
audits an in-memory copy and nothing is written back. No advising logic lives
here; it is `app.audit.simulation` and `app.audit.impact`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.audit.impact import UnlockReport, explore_unlocks
from app.audit.record import StudentRecord
from app.audit.simulation import SimulationRequest, SimulationResult, simulate
from app.auth.dependencies import CurrentUser
from app.catalog.loader import CatalogError
from app.catalog.registry import registry
from app.catalog.schema import Program
from app.config import get_settings
from app.ingestion.store import RecordStore, StoredRecordError

router = APIRouter(prefix="/advisor", tags=["advisor"])


def _own_record(student_id: str, program_id: str | None) -> tuple[Program, StudentRecord]:
    try:
        stored = RecordStore(get_settings().student_record_dir).load(student_id)
    except StoredRecordError as exc:
        raise HTTPException(
            status_code=404, detail="No confirmed coursework yet. Upload and confirm a transcript."
        ) from exc
    resolved = program_id or stored.program_id
    if resolved is None:
        raise HTTPException(status_code=400, detail="No program on record; pass program_id.")
    try:
        program = registry.get(resolved)
    except CatalogError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return program, stored.to_student_record()


@router.post("/simulate", response_model=SimulationResult)
def run_simulation(user: CurrentUser, body: SimulationRequest) -> SimulationResult:
    """Audit your record as it would stand after hypothetical courses. Read-only."""
    program, record = _own_record(user.student_id, body.program_id)
    return simulate(program, record, body)


@router.get("/unlocks/{code}", response_model=UnlockReport)
def course_unlocks(user: CurrentUser, code: str, program_id: str | None = None) -> UnlockReport:
    """What a course unlocks, what it needs, and where you stand on each."""
    program, record = _own_record(user.student_id, program_id)
    return explore_unlocks(program, record, code)
