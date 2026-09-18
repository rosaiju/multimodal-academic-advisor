# Catalog Open Questions (2026-2028)

Contradictions between official Morgan State sources that block encoding parts of
`data/catalog/morgan_cosc_bs_2026_2028.yaml`.

**These need a human answer from the department — not more searching.** Every one was
found by reading the official 2026-2028 catalog against itself. Searching harder
produces more conflicting pages, not a resolution.

Ask: **Dr. Shuangbao (Paul) Wang**, CS Department Chair, or a COSC academic advisor.

## Sources

| Tag | URL | What it is |
|---|---|---|
| `[PLANNER]` | `preview_degree_planner.php?catoid=28&poid=6521` | "Computer Science, B.S." degree requirements — primary source |
| `[SEQ]` | `preview_degree_planner.php?catoid=28&poid=6630` | "Computer Science Curriculum Sequence" — suggested 4-year plan |
| `[GENED]` | `preview_degree_planner.php?catoid=28&poid=6522` | University General Education Requirements |
| `[COURSE]` | `preview_course_nopop.php?catoid=28&coid=N` | Individual course records — prerequisite text only |

All on `catalog.morgan.edu`. `preview_program.php`, `content.php` and
`preview_entity.php` sit behind an AWS WAF JS challenge and are not reachable.

Until a question is answered, the affected requirement block stays out of the YAML.
An absent block reports "not yet encoded." A guessed block reports a confident wrong
number to a student deciding whether they graduate. The first is recoverable.

---

## Open

### Q1 — Major credit total: 65 or 59?

`[PLANNER]` heading reads **"Required Courses for Computer Science Major 65 credits"**.
The figure 59 appears in the distribution table.

Neither reconciles cleanly with the course list. Tallying the twelve required courses
gives 39 credits; adding Group A (2×3) + Group B (3×3) + Group C gives 63 with three
Group C courses, or 66 with four.

**Evidence for 59:** the starred-course rule. `[PLANNER]` marks MATH 241 and COSC 111
with `*` — *"a department required supporting/major course which may fulfill a general
education requirement."* The "Supporting Courses 11 credits" heading only reconciles
if starred MATH 241 (4 cr) is counted under General Education instead: 4+3+3+1 = 11. ✓

Applying that same rule to the major — excluding starred COSC 111 (4 cr) — gives
35 + 6 + 9 + 9 = **59**, with Group C at three courses.

This is an inference, not a source statement. It is offered as the most likely
reading, **not encoded as fact**. Worth asking directly: *is the 65 in the major
heading a typo?*

### Q2 — Group C: three courses or four?

Same page contradicts itself:
- Section heading: **"Group C Electives Students must choose four (4) courses."**
- Footnote 3: **"COSC Group C Electives must be three (3) courses chosen from the Group C Electives listed"**

`[SEQ]` shows **four** Group C slots. Note that Q1's arithmetic only reaches 59 if the
answer is **three**, so Q1 and Q2 are the same question asked twice.

### Q4 — Upper-division residency

Department page: ALL junior/senior major courses must be taken at Morgan.
University graduation page: TWO-THIRDS. Neither figure appears in `[PLANNER]` or
`[SEQ]`, so no residency block is encoded at all.

### Q7 — Elective group membership differs between two official pages

| Group | `[PLANNER]` | `[SEQ]` footnotes |
|---|---|---|
| A | COSC 210, 215, 238, 239, 243, 251, 241; CLCO 261 | COSC 238, 239, 243, 251; CLCO 261 |
| B | COSC 310, 315, 320, 323, 332, 338, 345, 358, 383, 385, 386; MATH 313 | COSC 320, 323, 332, 338, 383, 385; MATH 313; **EEGR 317** |
| C | COSC 410, 415, 460, 470, 472, 474, 480, 486, 491, 498, 499; CLCO 401, 471; EEGR 463 | COSC 470, 472, 460, 480, 491, 498, 499; **CLCO 411**, 471 |

`[SEQ]` names **EEGR 317** and **CLCO 411**, which `[PLANNER]` never mentions.
`[PLANNER]` lists the quantum-track courses (210, 215, 310, 315, 410, 415, 386, 486)
and EEGR 463, which `[SEQ]` omits. **Which list is binding?**

### Q8 — Elective group counts differ

| Group | `[PLANNER]` says | `[SEQ]` shows |
|---|---|---|
| A | choose two (2) | 3 slots |
| B | choose three (3) | 2 slots |
| C | four (4) / three (3) — see Q2 | 4 slots |
| D | — not mentioned — | 1 slot |

### Q9 — Group D exists in one source only

`[SEQ]` Fourth Year Semester Two includes a **Group D Elective**, with a footnote:
*"MGBU 326, INSS 391, 494, EEGR 481, 483, or 300–400 level COSC course not previously taken."*

`[PLANNER]` has no Group D anywhere.

**This also corrects an earlier note in this repo.** CS Navigator's phantom Group D was
cited as evidence that its API was stale. Group D is real — it appears in Morgan's own
published curriculum sequence. CS Navigator's other discrepancies still stand, but this
particular one was our error, not theirs.

### Q10 — `[SEQ]` semester subtotals do not match their own course lists

| Semester | Courses listed | Stated subtotal |
|---|---|---|
| Y2 S1 | 13 | **16** |
| Y2 S2 | 15 | **16** |
| Y3 S1 | 16 | **15** |
| Y3 S2 | 15 | **16** |
| Y4 S2 | 15 | **12** |

Stated subtotals sum to 120; the listed courses sum to 119. Y2 S1 looks like it is
missing a 3-credit course outright. Low priority — `[SEQ]` is advisory, not binding —
but it is a sign the page is not maintained carefully, which bears on Q7/Q8/Q9.

---

## Resolved

### Q3 — General education 40 vs 44 — RESOLVED, both correct

`[GENED]` totals **40**: IM 3 + EC 6 + CT 3 + MQ 3 + AH 6 + BP 7 + SB 6 + HH 3 + CI 3.

`[PLANNER]` totals **44** for "General Education and University Requirements" because
it counts four extra credits:

| Source of difference | Credits |
|---|---|
| COSC 111 (4 cr) substituted for the IM category (3 cr) | +1 |
| MATH 241 (4 cr) substituted for the MQ category (3 cr) | +1 |
| UNIV 101 — a *University* requirement, not general education | +1 |
| Physical Activity / FIN 101 / MIND 101 / DSVG 101 / FACS 102 | +1 |

40 + 4 = 44. ✓ No contradiction — the two pages count different things. Confirmed by
the same starred-course rule that makes Supporting Courses total 11 (see Q1).

### Q5 — ORNS 106 vs UNIV 101 — RESOLVED

**UNIV 101 University First-Year Seminar, 1 credit.** Named by `[PLANNER]` under
General Education and University Requirements, and placed by `[SEQ]` in First Year
Semester One. **ORNS 106 appears in neither source.** Encoded as `first_year_seminar`.

### Q6 — COSC 459 "does not exist" — WITHDRAWN, this was our error

COSC 459 exists. It is **COSC 459 Database Design, 3 credits**, and it is a *required*
major course in both `[PLANNER]` and `[SEQ]`.

The earlier claim came from a brute-force course-ID sweep that was treated as complete
when it was not: its log shows it hit an AWS WAF block at `coid=62112` and resumed past
the COSC band. Sixteen other COSC courses named by `[PLANNER]` (201, 210, 215, 310, 315,
320, 323, 338, 386, 410, 415, 472, 474, 480, 486) were missing from it for the same reason.

COSC 462's prerequisite on COSC 459 was valid all along. **Nothing was wrong with the
catalog here; the harvest method was wrong.** This is why the course layer is now built
from the degree-requirement pages rather than by enumerating IDs.
