"""Maximum bipartite matching, tested independently of any catalog.

The property that matters: a greedy pass can report a requirement unmet that is
actually satisfiable, because it hands a shared course to the first group that
wants it. These tests pin the cases where greedy and optimal disagree.
"""

from __future__ import annotations

from app.audit.matching import maximum_matching


def test_no_groups() -> None:
    assert maximum_matching([]) == {}


def test_group_with_no_candidates_is_unmatched() -> None:
    assert maximum_matching([[]]) == {}


def test_distinct_candidates_all_match() -> None:
    assert maximum_matching([[0], [1], [2]]) == {0: 0, 1: 1, 2: 2}


def test_two_groups_sharing_one_course_match_only_once() -> None:
    """Both groups accept only course 0, so one must go unsatisfied."""
    assert maximum_matching([[0], [0]]) == {0: 0}


def test_reassigns_a_shared_course_to_satisfy_both_groups() -> None:
    """Greedy fails: group 0 grabs course 0, leaving group 1 with nothing.

    Optimal rehouses group 0 onto course 1 so both are satisfied.
    """
    result = maximum_matching([[0, 1], [0]])
    assert len(result) == 2
    assert result[1] == 0
    assert result[0] == 1


def test_longer_augmenting_chain() -> None:
    """Satisfying group 2 requires shifting groups 0 and 1 in turn."""
    result = maximum_matching([[0, 1], [0, 2], [0]])
    assert len(result) == 3
    assert sorted(result.values()) == [0, 1, 2]


def test_every_group_gets_a_distinct_course() -> None:
    result = maximum_matching([[0, 1, 2], [0, 1, 2], [0, 1, 2]])
    assert len(set(result.values())) == len(result) == 3


def test_more_groups_than_courses_matches_what_it_can() -> None:
    result = maximum_matching([[0], [1], [0, 1]])
    assert len(result) == 2
