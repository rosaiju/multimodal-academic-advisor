"""Load and validate catalog YAML files.

Load failures are loud and fatal at startup. A silently-skipped catalog file would
mean the audit engine reports wrong progress, which is the one failure mode this
project cannot have.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.catalog.schema import Program

logger = logging.getLogger(__name__)


class CatalogError(RuntimeError):
    """Raised when a catalog file is missing, malformed, or internally inconsistent."""


def load_program(path: str | Path) -> Program:
    """Parse and fully validate one catalog file."""
    path = Path(path)
    if not path.is_file():
        raise CatalogError(f"catalog file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CatalogError(f"{path.name}: invalid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise CatalogError(f"{path.name}: top level must be a mapping, got {type(raw).__name__}")

    try:
        program = Program.model_validate(raw)
    except ValidationError as exc:
        raise CatalogError(f"{path.name}: does not match the catalog schema:\n{exc}") from exc

    logger.info(
        "loaded catalog %s (%s, %s): %d courses, %d requirement blocks",
        program.program_id,
        program.institution,
        program.catalog_year,
        len(program.courses),
        len(program.requirement_blocks),
    )
    return program


def load_all(directory: str | Path) -> dict[str, Program]:
    """Load every *.yaml / *.yml in `directory`, keyed by program_id.

    Raises on the first bad file rather than starting up with a partial catalog.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise CatalogError(f"catalog directory not found: {directory}")

    files = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    if not files:
        raise CatalogError(f"no catalog files in {directory}")

    programs: dict[str, Program] = {}
    for path in files:
        program = load_program(path)
        if program.program_id in programs:
            raise CatalogError(
                f"{path.name}: duplicate program_id {program.program_id!r} "
                f"(already defined in another file)"
            )
        programs[program.program_id] = program
    return programs
