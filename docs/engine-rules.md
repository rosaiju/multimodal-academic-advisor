# Degree audit engine rules

What `backend/app/audit/` decides, and where it declines to decide. Each rule has
tests: `tests/test_engine_hardening.py`, `tests/test_engine_planning.py`,
`tests/test_engine_advanced_student.py`, plus the older `test_engine.py`,
`test_schedule.py` and others.

The engine is deterministic and imports no LLM code (`tests/test_no_llm_in_engine.py`).
Where a Morgan rule is not in the catalog file, the engine reports **NEEDS_ADVISOR**,
a range, or a note. It does not pick a rule on Morgan's behalf.

## Grades (`app/audit/grades.py`)

| Grade | Earns credit | In GPA | Satisfies a block / prerequisite |
|---|---|---|---|
| A, B, C, D (with or without +/-) | yes | yes (4/3/2/1; +/- ignored) | if it meets the block's `min_grade` / course's `min_prereq_grade` |
| F | no | yes (0) | no |
| P, S, CR | yes | no | **NEEDS_ADVISOR**: the catalog doesn't say whether a pass meets a minimum grade |
| W, I, NP, AU | no | no | no |
| IP, REG, INC (in progress) | no | no | no. Counted in `total_credits_in_progress` |
| anything else (WF, NR, ...) | **no** (fails closed) | no | no. Named in `AuditResult.notes` |

## Credit totals

* `total_credits_earned` counts every trusted credit-earning row, catalog or not.
  A course the catalog names counts **once** however many times it was passed.
  Codes outside the catalog are summed row by row, because DegreeWorks gives many
  distinct transfer courses one bucket code (`COSC116TR`).
* `total_credits_applied` counts only credit placed in an encoded block.
* Courses passed twice are listed in `notes`. The catalog does not say which
  courses can be repeated for credit.

## Requirement blocks

* A course is applied to at most one block, and a retake is one course (both
  strategies). GPA and residency blocks consume nothing.
* `OPTIMAL_BIPARTITE` decides every slot block (`all_of`, `n_of`, `each_of`) together.
  A `credits_from` block then takes only what that matching left over, whatever
  its position in the catalog.
* An unconfirmed (`UNVERIFIED_EXTRACTION`) row never counts. It also can't stand in
  for a confirmed attempt at the same course.
* Minimum grades come from the catalog's `min_grade` per block. Morgan encodes C
  for the major, supporting and composition blocks. CT, HH, CI and UNIV101 have no
  minimum in the file, so D counts.

## GPA

* Cumulative GPA uses every trusted A–F row, including Morgan courses the partial
  catalog doesn't name. Major GPA uses the rows whose subject (catalog subject,
  else the code's leading letters) is in `major_subjects`.
* **Not encoded:** whether a retake replaces the earlier grade, and whether
  transfer grades enter the GPA. The engine computes the GPA under every reading
  and reports `cumulative`/`major` (all attempts, all institutions) with
  `*_low`/`*_high` and `gpa.notes`. A GPA block is SATISFIED only if the lowest
  reading meets the minimum and UNMET only if the highest reading misses it.
  Anything in between is NEEDS_ADVISOR.

## Residency

* No residency block is encoded for Morgan, because of open question Q4.
* When one is encoded, only earned credit counts. Every earned row must have an
  `institution`, or the block is NEEDS_ADVISOR. Non-catalog courses at the home
  institution count at their transcript credits. Transfer rows don't count.

## Prerequisites

* Met only by a trusted, finished grade that meets the course's `min_prereq_grade`.
  A failed, withdrawn, pass-graded or in-progress prerequisite is unmet.
* Many non-COSC courses (ENGL102, MATH242, ...) have no sourced prerequisites. The
  schedule lists those courses in a caveat.

## Graduation

`is_graduation_eligible` is `not graduation_blockers`. A blocker is any of these:

* partial catalog coverage (always true for Morgan today);
* fewer applied credits than `total_credits_required`;
* GPA minimums not met under every reading;
* any block not SATISFIED.

## Planning, schedule, compare, explain

* A course passed below a block's minimum (a D in a "C or better" course) is a
  **retake**. The plan recommends it, `compare` sets `retake_needed`, and
  `validate_schedule` allows it with a warning.
* In-progress courses are never recommended or re-planned. The schedule assumes
  they will be passed and says so in a caveat.
* Reasons a student reads use requirement **names**. Block ids stay in `serves`.
* The schedule respects `credit_target` and the `terms_offered` seasons, and puts
  prerequisites in strictly earlier terms. `validate_schedule` reports a course
  placed with or before its prerequisite as an error. Courses marked
  `offered_as_needed` always carry a warning.
* `UNVERIFIED` or zero-credit catalog stubs (MATH141) are never planned. They are
  listed as unscheduled with "confirm with an advisor", and so is whatever depends
  on them.
* `SchedulePlan.last_planned_term` is not a graduation date.
  `coverage_complete` is False on a partial catalog, and `graduation_caveat` is
  always present.

## Not handled (flagged)

* Lecture/lab pairs: the catalog has none (BP is unencoded, Q14).
* Courses repeatable for credit: not in the catalog schema. A second pass is
  counted once and noted.
* +/- grade weighting: stripped, as `grade_meets` does.
