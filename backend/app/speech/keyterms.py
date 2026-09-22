"""Catalog course codes, in the form a person says them, as Deepgram key terms.

A general speech model hears "COSC 241" as anything from "Kasich two forty-one"
to "cause C 241". Keyterm prompting biases it toward the exact strings the
advisor understands, and the catalog is the only honest source of those strings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.catalog.schema import Program

_CODE = re.compile(r"^([A-Z]+)(\d+[A-Z]*)$")


def spoken_course_codes(programs: Iterable[Program]) -> list[str]:
    """`COSC241` -> `COSC 241`, plus each subject on its own. Sorted, no duplicates."""
    subjects: set[str] = set()
    codes: set[str] = set()
    for program in programs:
        for course in program.courses:
            match = _CODE.match(course.code.upper())
            if match:
                subjects.add(match[1])
                codes.add(f"{match[1]} {match[2]}")
            else:
                codes.add(course.code)
    return sorted(subjects) + sorted(codes)
