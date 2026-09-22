"""Transcript upload and confirmation endpoints.

The flow this exposes, and the reason it is three calls rather than one:

    POST /ingest/transcript   upload -> extracted rows, nothing stored
    POST /ingest/confirm      student accepts specific rows -> stored record
    GET  /students/{id}/record  what the audit engine will actually see

Uploading stores nothing. A student sees what was read off their document, fixes
what is wrong, and accepts row by row. Collapsing this into a single "upload and
apply" call would be friendlier and would make the provenance rule meaningless.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.catalog.loader import CatalogError
from app.catalog.registry import registry
from app.config import get_settings
from app.ingestion.confirmation import ConfirmationError, confirm_course
from app.ingestion.extractor import UnsupportedDocument, extract_transcript
from app.ingestion.models import ExtractedCourse, ExtractionResult
from app.ingestion.store import RecordStore, StoredRecord, StoredRecordError

router = APIRouter(tags=["ingestion"])


def _store() -> RecordStore:
    return RecordStore(get_settings().student_record_dir)


def _program_or_404(program_id: str | None):
    if program_id is None:
        return None
    try:
        return registry.get(program_id)
    except CatalogError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class UploadResponse(BaseModel):
    """What was read. Explicitly not what was saved."""

    stored: bool = Field(
        default=False,
        description="Always false. Uploading never writes to a student record.",
    )
    summary: str
    extraction: ExtractionResult
    next_step: str = (
        "Review each row, correct anything wrong, then POST the ones you accept to "
        "/ingest/confirm. Nothing counts toward your degree until you do."
    )


class ConfirmItem(BaseModel):
    """One accepted row: what was extracted, plus anything the student changed.

    The extracted row is sent back VERBATIM and edits travel in the optional
    fields beside it. Sending a single pre-edited row instead would make a
    correction indistinguishable from an accurate read, and the record would lose
    the fact that a student had to fix the extractor.
    """

    extracted: ExtractedCourse = Field(description="Exactly as /ingest/transcript returned it")
    term: str | None = Field(default=None, description="Set only to override the extracted term")
    grade: str | None = Field(default=None, description="Set only to override the extracted grade")
    credits: Decimal | None = Field(
        default=None, description="Set only to override the extracted credits"
    )


class ConfirmRequest(BaseModel):
    student_id: str
    program_id: str | None = None
    source_name: str
    extractor: str
    courses: list[ConfirmItem] = Field(description="The rows the student accepted.")
    institution: str | None = Field(
        default=None,
        description="Where these were taken. Needed for residency; absent means unknown.",
    )


class ConfirmResponse(BaseModel):
    student_id: str
    confirmed_now: int
    total_on_record: int
    corrected: list[str] = Field(default_factory=list)


@router.post("/ingest/transcript", response_model=UploadResponse)
async def upload_transcript(
    file: Annotated[UploadFile, File()],
    program_id: Annotated[str | None, Form()] = None,
) -> UploadResponse:
    """Read a transcript and return what was found. Stores nothing.

    `program_id` is optional and is used only to CHECK course codes against the
    catalog, never to correct them.
    """
    settings = get_settings()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"file exceeds {settings.max_upload_bytes} bytes",
        )

    program = _program_or_404(program_id)
    try:
        extraction = extract_transcript(
            data,
            filename=file.filename or "transcript.txt",
            content_type=file.content_type,
            program=program,
        )
    except UnsupportedDocument as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    return UploadResponse(summary=extraction.summary(), extraction=extraction)


@router.post("/ingest/confirm", response_model=ConfirmResponse)
def confirm_courses(request: ConfirmRequest) -> ConfirmResponse:
    """Accept specific rows onto a student's record.

    Each row is promoted to STUDENT_CONFIRMED individually. A row still missing a
    term, grade, or credit value is rejected with a message naming the gap rather
    than being stored with a default.
    """
    if not request.courses:
        raise HTTPException(status_code=400, detail="no courses were submitted")

    _program_or_404(request.program_id)
    stub = ExtractionResult(source_name=request.source_name, extractor=request.extractor)

    confirmed = []
    for item in request.courses:
        try:
            confirmed.append(
                confirm_course(
                    item.extracted,
                    stub,
                    term=item.term,
                    grade=item.grade,
                    credits=item.credits,
                    institution=request.institution,
                )
            )
        except ConfirmationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        record = _store().add_courses(request.student_id, confirmed, program_id=request.program_id)
    except StoredRecordError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ConfirmResponse(
        student_id=record.student_id,
        confirmed_now=len(confirmed),
        total_on_record=len(record.confirmed),
        corrected=[c.course.code for c in confirmed if c.was_corrected],
    )


@router.get("/students/{student_id}/record", response_model=StoredRecord)
def get_record(student_id: str) -> StoredRecord:
    """Everything confirmed for one student, with its full audit trail."""
    try:
        return _store().load(student_id)
    except StoredRecordError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/students/{student_id}/record")
def delete_record(student_id: str) -> dict[str, object]:
    """Delete a student's confirmed coursework."""
    try:
        deleted = _store().delete(student_id)
    except StoredRecordError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"no record for {student_id!r}")
    return {"deleted": True, "student_id": student_id}
