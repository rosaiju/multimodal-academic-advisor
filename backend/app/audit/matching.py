"""Maximum bipartite matching.

Requirement groups on one side, a student's completed courses on the other. A
course may satisfy several groups but may be applied to only one, so deciding
whether a block is satisfied is an assignment problem, not a counting problem.

Counting is what makes an audit quietly wrong: three groups with three eligible
courses between them can still be unsatisfiable if all three courses only fit the
same one group. Kuhn's algorithm below finds a maximum assignment if one exists.

Sizes here are tiny (a block has single-digit groups), so the simple augmenting-path
algorithm is the right choice over anything fancier.
"""

from __future__ import annotations


def maximum_matching(candidates: list[list[int]]) -> dict[int, int]:
    """Assign each group at most one distinct course.

    `candidates[g]` lists the indices of courses that could satisfy group `g`.
    Returns ``{group_index: course_index}`` for as many groups as can be satisfied
    simultaneously. A group missing from the result cannot be satisfied without
    taking a course away from another group.
    """
    course_to_group: dict[int, int] = {}

    def augment(group: int, seen: set[int]) -> bool:
        for course in candidates[group]:
            if course in seen:
                continue
            seen.add(course)
            # Free course, or its current holder can be rehoused elsewhere.
            holder = course_to_group.get(course)
            if holder is None or augment(holder, seen):
                course_to_group[course] = group
                return True
        return False

    for group in range(len(candidates)):
        augment(group, set())

    return {g: c for c, g in course_to_group.items()}
