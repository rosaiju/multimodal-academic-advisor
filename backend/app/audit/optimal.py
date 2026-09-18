"""Global course-to-requirement assignment.

Greedy walks the blocks in catalog order and lets each take what it can. That
under-reports whenever an early block spends the only course a later block could
have used. The classic case in `demo_university_cs.yaml`: PHYS211 satisfies both
the mathematics elective and the laboratory science requirement. A student with
PHYS211 and MATH312 has genuinely done both, but greedy gives PHYS211 to the math
elective (it comes first) and then reports science unmet.

This module decides all assignments at once instead.

Every course-consuming block is expanded into SLOTS - one slot per course the
block still needs - and each slot lists the courses that could fill it. Maximum
bipartite matching then fills as many slots as can be filled simultaneously.

What is optimised: the number of requirement SLOTS filled. That is not identical
to the number of BLOCKS completed - filling two slots of a three-slot block and
one of a one-slot block scores the same as finishing the one-slot block and
leaving the other at one. Preferring whole-block completion needs min-cost flow
and a policy decision about which requirement matters more, which is a question
for an advisor, not an implementation detail. Slot maximisation never does worse
than greedy, and that is the claim being made here.

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/. ***
"""

from __future__ import annotations

from app.audit.evaluators import eligible_indices
from app.audit.matching import maximum_matching
from app.audit.record import CompletedCourse
from app.catalog.schema import (
    AllOfBlock,
    EachOfBlock,
    NOfBlock,
    Program,
    RequirementBlock,
    grade_meets,
)

#: Block types expanded into slots. `credits_from` is quantity-based rather than
#: slot-based and is resolved afterwards, from whatever the matching left over.
SLOT_BLOCKS = (AllOfBlock, NOfBlock, EachOfBlock)

_GRADE_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}


def representative_indices(pool: list[CompletedCourse]) -> dict[str, int]:
    """One pool position per course code - the best passing grade for it.

    Collapsing retakes before matching is what stops a course taken twice from
    filling two different requirement slots. Two entries for COSC350 are still one
    COSC350 as far as the degree is concerned.
    """
    best: dict[str, int] = {}
    for i, taken in enumerate(pool):
        rank = _GRADE_RANK.get(taken.grade.strip().upper().rstrip("+-"))
        if rank is None:
            continue
        current = best.get(taken.code)
        if current is None:
            best[taken.code] = i
            continue
        current_rank = _GRADE_RANK.get(pool[current].grade.strip().upper().rstrip("+-"), 99)
        if rank < current_rank:
            best[taken.code] = i
    return best


def _slot_candidates(
    program: Program,
    block: RequirementBlock,
    pool: list[CompletedCourse],
    reps: dict[str, int],
) -> list[list[int]]:
    """Expand one block into slots; each slot lists pool indices that could fill it."""
    usable = {
        code: i
        for code, i in reps.items()
        if program.course(code) is not None
        and pool[i].is_trusted
        and grade_meets(pool[i].grade, block.min_grade)
    }

    match block:
        case AllOfBlock():
            # One slot per required course; only that exact course can fill it.
            return [
                [usable[code]] if code in usable else [] for code in dict.fromkeys(block.courses)
            ]
        case NOfBlock():
            options = [usable[c] for c in dict.fromkeys(block.courses) if c in usable]
            return [list(options) for _ in range(block.n)]
        case EachOfBlock():
            return [[usable[c] for c in group if c in usable] for group in block.groups]
    return []  # pragma: no cover


def assign_optimally(
    program: Program,
    pool: list[CompletedCourse],
) -> dict[str, list[int]]:
    """Decide, across every slot-based block at once, which courses go where.

    Returns ``{block_id: [pool indices assigned to it]}``. Blocks absent from the
    result received nothing. `credits_from`, `gpa` and `residency` blocks are not
    included - the engine handles those separately.
    """
    reps = representative_indices(pool)

    slot_owner: list[str] = []
    candidates: list[list[int]] = []
    for block in program.requirement_blocks:
        if not isinstance(block, SLOT_BLOCKS):
            continue
        for slot in _slot_candidates(program, block, pool, reps):
            slot_owner.append(block.id)
            candidates.append(slot)

    matched = maximum_matching(candidates)

    assignment: dict[str, list[int]] = {}
    for slot_index, pool_index in matched.items():
        assignment.setdefault(slot_owner[slot_index], []).append(pool_index)
    return assignment


__all__ = [
    "SLOT_BLOCKS",
    "assign_optimally",
    "eligible_indices",
    "representative_indices",
]
