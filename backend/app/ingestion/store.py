"""Student record storage.

One JSON file per student under the record directory. Files, not a database,
because the whole point of this project is that a student's coursework is
inspectable and auditable: a person can open the file and see exactly what the
system believes and where each row came from.

Every stored course keeps its source document, extractor, raw transcript line, and
any correction the student made. That trail is what lets someone answer "why does
the system think I took this?" months later.

*** NO LLM CODE. ***
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.audit.record import StudentRecord
from app.ingestion.confirmation import ConfirmedCourse
from app.schemas.provenance import Provenance

logger = logging.getLogger(__name__)

#: Student ids become filenames, so they must not be able to escape the directory.
_SAFE_ID = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class StoredRecordError(RuntimeError):
    """Raised when a record cannot be read, written, or safely addressed."""


class StoredRecord(BaseModel):
    """What one student's file holds."""

    model_config = ConfigDict(frozen=True)

    student_id: str
    program_id: str | None = Field(
        default=None, description="Which degree this student is audited against"
    )
    confirmed: list[ConfirmedCourse] = Field(default_factory=list)

    def to_student_record(self) -> StudentRecord:
        """The engine-facing view. Confirmed coursework only, by construction."""
        return StudentRecord(
            student_id=self.student_id,
            completed=[c.course for c in self.confirmed],
        )

    @property
    def corrected_courses(self) -> list[ConfirmedCourse]:
        """Rows the student had to fix. Useful for spotting a bad extractor."""
        return [c for c in self.confirmed if c.was_corrected]


def _validate_id(student_id: str) -> str:
    if not student_id or not set(student_id) <= _SAFE_ID:
        raise StoredRecordError(
            f"unsafe student id {student_id!r}: use letters, digits, hyphen, underscore"
        )
    return student_id


class RecordStore:
    """Reads and writes student records in one directory."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def path_for(self, student_id: str) -> Path:
        return self.directory / f"{_validate_id(student_id)}.json"

    def exists(self, student_id: str) -> bool:
        return self.path_for(student_id).is_file()

    def save(self, record: StoredRecord) -> Path:
        """Write atomically, so an interrupted write cannot truncate a record."""
        path = self.path_for(record.student_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        temp.replace(path)
        logger.info(
            "saved record for %s: %d confirmed course(s)",
            record.student_id,
            len(record.confirmed),
        )
        return path

    def load(self, student_id: str) -> StoredRecord:
        path = self.path_for(student_id)
        if not path.is_file():
            raise StoredRecordError(f"no record for student {student_id!r} at {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StoredRecordError(f"{path.name}: corrupt record file: {exc}") from exc

        record = StoredRecord.model_validate(raw)
        untrusted = [
            c.course.code
            for c in record.confirmed
            if c.course.provenance is not Provenance.STUDENT_CONFIRMED
        ]
        if untrusted:
            # A stored file is editable by anything on disk. Refuse rather than
            # feed an unconfirmed row into an audit because it was in the folder.
            raise StoredRecordError(
                f"{path.name}: {', '.join(untrusted)} not marked student_confirmed. "
                "A record file must contain only confirmed coursework."
            )
        return record

    def add_courses(
        self,
        student_id: str,
        courses: list[ConfirmedCourse],
        *,
        program_id: str | None = None,
    ) -> StoredRecord:
        """Append confirmed coursework, replacing any earlier row for the same code.

        Replacing rather than appending duplicates means a re-uploaded transcript
        does not double a student's coursework. A genuine retake is two rows with
        the same code in ONE upload, and the audit engine already counts it once.
        """
        existing = self.load(student_id) if self.exists(student_id) else None
        merged: dict[str, ConfirmedCourse] = {}
        if existing is not None:
            merged = {c.course.code: c for c in existing.confirmed}
        for entry in courses:
            merged[entry.course.code] = entry

        record = StoredRecord(
            student_id=student_id,
            program_id=program_id or (existing.program_id if existing else None),
            confirmed=sorted(merged.values(), key=lambda c: c.course.code),
        )
        self.save(record)
        return record

    def list_students(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(p.stem for p in self.directory.glob("*.json"))

    def delete(self, student_id: str) -> bool:
        path = self.path_for(student_id)
        if not path.is_file():
            return False
        path.unlink()
        return True
