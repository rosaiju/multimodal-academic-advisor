"""In-memory catalog registry, populated once at application startup."""

from __future__ import annotations

from pathlib import Path

from app.catalog.loader import CatalogError, load_all
from app.catalog.schema import Program


class CatalogRegistry:
    """Holds every loaded program. Read-only after `load()`."""

    def __init__(self) -> None:
        self._programs: dict[str, Program] = {}
        self._loaded_from: Path | None = None

    def load(self, directory: str | Path) -> None:
        self._programs = load_all(directory)
        self._loaded_from = Path(directory)

    @property
    def is_loaded(self) -> bool:
        return bool(self._programs)

    def get(self, program_id: str) -> Program:
        try:
            return self._programs[program_id]
        except KeyError:
            known = ", ".join(sorted(self._programs)) or "<none loaded>"
            raise CatalogError(
                f"unknown program {program_id!r}. Loaded programs: {known}"
            ) from None

    def list_programs(self) -> list[Program]:
        return sorted(self._programs.values(), key=lambda p: (p.institution, p.program))


#: Process-wide singleton. `app.main` calls `.load()` during startup.
registry = CatalogRegistry()
