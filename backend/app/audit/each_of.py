"""Evaluate an `each_of` requirement block against a student record.

An `each_of` block says "one course from Part A, one from Part B", and no single
course may cover both.

The implementation lives in `evaluators.evaluate_each_of_pool`, which every block
type shares so the engine can track which courses each block consumed. This module
keeps the standalone entry point used when evaluating one block on its own.

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/. ***
"""

from __future__ import annotations

from app.audit.evaluators import evaluate_each_of_pool
from app.audit.record import StudentRecord
from app.catalog.schema import EachOfBlock, Program
from app.schemas.audit import RequirementBlockResult


def evaluate_each_of(
    program: Program,
    block: EachOfBlock,
    record: StudentRecord,
) -> RequirementBlockResult:
    """Decide whether `record` satisfies `block`, ignoring all other blocks."""
    result, _consumed = evaluate_each_of_pool(program, block, list(record.completed))
    return result
