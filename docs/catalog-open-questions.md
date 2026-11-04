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

**Re-verified 2026-11-04.** `[PLANNER]`, `[SEQ]` and `[GENED]` were re-fetched (raw
HTML, one request at a time) and every open question below was checked against them
again; each now carries a "Re-checked 2026-11-04" line. Course records were re-read
for prerequisites. No page was blocked except two transient HTTP 202 WAF refusals
during the course-record scan, which cleared after a 75 s pause.

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

**Re-checked 2026-11-04, still open.** `[PLANNER]` verbatim: distribution table row
"Required Courses for the Computer Science Major 59 credits"; section heading
"Required Courses for Computer Science Major 65 credits". The section under that
heading prints the twelve required courses (39 cr) plus **nine** elective rows
(2 × Group A, 3 × Group B, **4** × Group C = 27 cr) = 66, or 62 without starred
COSC 111. Neither 65 nor 59 falls out of the rows the heading itself prints.

### Q2 — Group C: three courses or four?

Same page contradicts itself:
- Section heading: **"Group C Electives Students must choose four (4) courses."**
- Footnote 3: **"COSC Group C Electives must be three (3) courses chosen from the Group C Electives listed"**

`[SEQ]` shows **four** Group C slots. Note that Q1's arithmetic only reaches 59 if the
answer is **three**, so Q1 and Q2 are the same question asked twice.

**Re-checked 2026-11-04, still open.** Tally of official statements: **four** —
`[PLANNER]` Group C heading ("Students must choose four (4) courses."), `[PLANNER]`'s
own required-courses list and its "General COSC Track" (four "COSC Group C Elective"
rows each), and `[SEQ]` footnote 3 ("Choose four (4) courses from the following
Group C electives"). **Three** — `[PLANNER]` footnote 3 and the 59-credit
distribution figure. The majority says four; the distribution arithmetic says three.
Not decidable from the sources.

### Q4 — Upper-division residency

Department page: ALL junior/senior major courses must be taken at Morgan.
University graduation page: TWO-THIRDS. Neither figure appears in `[PLANNER]` or
`[SEQ]`, so no residency block is encoded at all.

**Re-checked 2026-11-04, still open.** `[PLANNER]`, `[SEQ]` and `[GENED]` contain no
residency statement. The department and graduation pages are `content.php` pages
behind the WAF challenge, so they were not re-fetched.

### Q7 — Elective group membership differs between two official pages

| Group | `[PLANNER]` | `[SEQ]` footnotes |
|---|---|---|
| A | COSC 210, 215, 238, 239, 243, 251, 241; CLCO 261 | COSC 238, 239, 243, 251; CLCO 261 |
| B | COSC 310, 315, 320, 323, 332, 338, 345, 358, 383, 385, 386; MATH 313 | COSC 320, 323, 332, 338, 383, 385; MATH 313; **EEGR 317** |
| C | COSC 410, 415, 460, 470, 472, 474, 480, 486, 491, 498, 499; CLCO 401, 471; EEGR 463 | COSC 470, 472, 460, 480, 491, 498, 499; **CLCO 411**, 471 |

`[SEQ]` names **EEGR 317** and **CLCO 411**, which `[PLANNER]` never mentions.
`[PLANNER]` lists the quantum-track courses (210, 215, 310, 315, 410, 415, 386, 486)
and EEGR 463, which `[SEQ]` omits. **Which list is binding?**

**Re-checked 2026-11-04, still open; two details the table above did not capture:**

- `[PLANNER]`'s Group C list prints **"COSC 470 - Artificial Intelligence ... OR ...
  COSC 472 - Introduction to Machine Learning"** — an OR between two Group C options,
  which suggests a student may count only one of them. `[SEQ]` lists both with no OR.
- **COSC 241 and COSC 243 swap roles between the two pages** — see Q16.

### Q8 — Elective group counts differ

| Group | `[PLANNER]` says | `[SEQ]` shows |
|---|---|---|
| A | choose two (2) | 3 slots |
| B | choose three (3) | 2 slots |
| C | four (4) / three (3) — see Q2 | 4 slots |
| D | — not mentioned — | 1 slot |

**Re-checked 2026-11-04, still open.** `[SEQ]` footnotes verbatim: "Choose three (3)
courses from the following Group A electives", "Choose two (2) courses from the
following Group B electives", "Choose four (4) courses from the following Group C
electives", "Choose one (1) course from the following Group D electives". `[PLANNER]`
footnotes 1-3: two, three, three; its group headings: two, three, four. Both pages total
nine or ten elective courses, but split them differently — see also Q17 (tracks).

### Q9 — Group D exists in one source only

`[SEQ]` Fourth Year Semester Two includes a **Group D Elective**, with a footnote:
*"MGBU 326, INSS 391, 494, EEGR 481, 483, or 300–400 level COSC course not previously taken."*

`[PLANNER]` has no Group D anywhere.

**Re-checked 2026-11-04, still open.** `[SEQ]` footnote 4 verbatim: "Choose one (1)
course from the following Group D electives: MGBU 326, INSS 391, INSS 494,EEGR 481,
EEGR 483, XXXX - 300-400 level COSC course not previously taken". Group D is real;
whether it is *required* is the question, since the page that defines the degree
(`[PLANNER]`) never mentions it.

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

**Re-checked 2026-11-04, unchanged.** Same subtotals; listed rows still sum to 119
(Y3 S2's "COSC Group B Elective" row prints no credit value and is counted as 3).
`[SEQ]` also omits COSC 201 (1 cr, required by `[PLANNER]` Supporting Courses) and
the 6-credit Natural Science Complementary Studies entirely.

### Q12 — General education IM and MQ: the COSC 111 / MATH 241 substitutions — catalog side SETTLED 2026-11-04; blocked only on the engine

Not encoded: `gen_ed_information_literacy` (IM, 3 cr) and `gen_ed_mathematics` (MQ, 3 cr).

`[GENED]` lists, for IM, COSC 110, INSS 141 and "Computer literacy course required by
the major/discipline", and for MQ, MATH 109, 110, 113 and "MQ course required by the
major/discipline". For a CS major those last options are COSC 111 and MATH 241
(`[PLANNER]`; see Q3). `[GENED]` also states a course may satisfy identical
requirements in up to two areas — general education and the major — "The credits earned
for the course count only once toward the total 120."

**The audit engine cannot express that.** It applies a course to at most one block
(`app/audit/engine.py`, rule 1), and COSC 111 and MATH 241 are already required by
`major_required_courses` and `supporting_courses`. Tried with both matcher strategies:
a student holding only COSC 111 gets the major block credit and IM stays UNMET; the
same for MATH 241 and MQ. So listing them in the IM/MQ blocks does nothing, and leaving
them out tells nearly every CS student a satisfied requirement is unmet — the normal
path for this major is the substitution, not COSC 110 / MATH 109.

Two ways forward, neither of which is a catalog edit:

1. Engine support for a course counting toward two blocks (the two-area rule) — a change
   to `app/audit/`, owned by the audit work, with the 120-credit total still counted once.
2. A department answer that the substitution should be modelled differently.

**Ask:** *does the two-area rule apply to COSC 111 -> IM and MATH 241 -> MQ for a CS major,
and are those the intended substitutions?*

**Re-checked 2026-11-04 — the sources answer the catalog half of this.** `[PLANNER]`
tags the two rows explicitly: **"COSC 111 - Introduction to Computer Science I IM \*"**
and **"MATH 241 - Calculus I MQ \*"**, with the note "\* denotes a department required
supporting/major course which may fulfill a general education requirement and must be
completed with a grade of 'C' or higher". `[SEQ]` First Year Semester One carries the
same IM \* and MQ \* tags. `[GENED]` prints, verbatim: "Subject to specific requirements
for the various majors, minors and certificates, students may use a course(s) to satisfy
identical course requirements in up to two of the following areas: 1) general education
requirements; 2) requirements for the major; ... The credits earned for the course count
only once toward the total 120 credits". So: yes, COSC 111 -> IM and MATH 241 -> MQ are
the intended substitutions, and the two-area rule is what makes them legal. Also
verified: MQ's printed rule is "Must be MATH 109 or Above"; IM's options are COSC 110
(3), INSS 141 (3); MQ's are MATH 109 (4), MATH 110 (3), MATH 113 (4).

**Nothing left to ask the department here.** The blocks stay out only because the
engine cannot yet let one course satisfy two blocks; see "Engine capabilities needed"
at the end of this file. Note the `*` also means the substitution needs a **C**.

### Q13 — General education AH: "two courses from different disciplines" and a foreign-language wildcard

Not encoded: Arts and Humanities (AH, 6 cr).

`[GENED]`: "Students must select two courses from different disciplines in the AH
distribution area." Two things block it:

- **"Different disciplines" is undefined.** Same course prefix is the obvious reading, but
  the page never says so, and the engine has no distinct-discipline rule. An `n_of` of 2
  would mark PHIL 102 + PHIL 220 satisfied, which the page's own wording rules out.
- **The list contains a rule, not a course:** "Foreign Language 102 or higher 3 credits."
  Without it, a student who took a language course is told AH is unmet.

The other 18 options (ART 308 through WGST 301) are clear.

**Ask:** *is "discipline" the course prefix, and which foreign-language courses count?*

**Re-checked 2026-11-04, still open.** `[GENED]` wording unchanged; 18 course rows plus
the "Foreign Language 102 or higher 3 credits." row. `[PLANNER]` confirms the shape:
two rows "XXXX - AH General Education Req. 3 credits AH". See "Partial encoding tried
and rejected" below.

### Q14 — General education BP: lab pairing and the 7-credit total

Not encoded: Biological and Physical Sciences (BP, 7 cr).

`[GENED]`: "two courses from the BP distribution areas. At least one course must be
lab-based." Lab-based options print lecture and lab as **separate rows** (CHEM 101, 3 cr
and CHEM 101L, 1 cr; PHYS 203 and 203L; and so on) but BIOL 101 is a single 4-credit row.
It is not stated whether a "course" is the lecture+lab pair, nor how two courses reach
exactly 7 credits (4 + 3 works; two lab-based courses is 8). The engine has no "at least
one from this subset" rule either.

**Ask:** *does a lecture/lab pair count as one course, and is 4 + 3 the intended shape?*

**Re-checked 2026-11-04 — the 4 + 3 shape is SETTLED, the rest is still open.**
`[PLANNER]` prints the BP slots as **"XXXX - BP General Education Req. 4 credits with
lab BP"** and **"XXXX - BP General Education Req. 3 credits no lab req. BP"**; `[SEQ]`
prints "BP General Education Req. 4 credits (BP)" (Y3 S1) and "BP General Education
Req. 3 credits no lab req. BP" (Y3 S2). So the intended shape is one 4-credit lab-based
course plus one 3-credit course. That implies a 3-credit lecture + 1-credit lab pair
(e.g. CHEM 101 + CHEM 101L) is "a course" for this purpose, but no page says so in
words, and PHYS 205/206 (4 cr) + 205L/206L (1 cr) make 5, which fits neither slot.
Still open: *does PHYS 205 + 205L count as the lab course, and is the excess credit
simply absorbed?* The engine also still lacks an "at least one from this subset" rule.

### Q15 — General education SB: "two courses from different disciplines"

Not encoded: Social and Behavioral Sciences (SB, 6 cr).

Same wording and same problem as Q13: `[GENED]` requires "two courses from different
disciplines", and the list has several courses per prefix (HIST 101-130, 140; ECON 211,
212; POSC 201, 206; PSYC 101, 111). An `n_of` of 2 would accept HIST 101 + HIST 102.
The course list itself is clear (22 courses).

**Ask:** *same as Q13 — is "discipline" the course prefix?*

**Re-checked 2026-11-04, still open.** `[GENED]` wording and the 22-course list are
unchanged (the heading is printed without its roman numeral VII). `[PLANNER]` shows two
rows "XXXX - SB General Education Req. 3 credits SB".

#### Partial encoding of AH / BP / SB — tried 2026-11-04 and rejected

The course lists and counts are verified, so a partial encoding was built: AH and SB as
`n_of` 2, BP as `credits_from` 7, all with `advisor_approval_required: true` so the
engine never says SATISFIED for a rule it cannot check. It was backed out before commit
because, with today's engine:

1. `advisor_approval_required` forces NEEDS_ADVISOR **even when nothing has been
   taken**, so an empty record shows three categories as "needs advisor" instead of
   unmet (`tests/test_engine.py::test_empty_record_produces_a_clean_unmet_audit`).
2. It adds 80 courses and ~18 subjects; the speech keyterm list grows to 192 terms,
   over the 150 budget `tests/test_transcribe_api.py` enforces for Deepgram.
3. Three non-lab BP courses (9 cr) would reach 7 credits with no lab at all.

It becomes encodable without any guess once the engine has the capabilities listed at
the end of this file. The verified course lists are in `[GENED]`, transcribed exactly
as in the existing CT/HH/CI blocks.

### Q16 — COSC 241 and COSC 243: which one is required? (new 2026-11-04)

The two official pages swap them:

| | `[PLANNER]` | `[SEQ]` |
|---|---|---|
| COSC 243 Computer Organization and Architecture | **Required** (under "Required Courses for Computer Science Major") **and** a Group A option | Group A option only (footnote 1); no fixed row |
| COSC 241 Computer Systems and Digital Logic | Group A option only | **Fixed row**, Second Year Semester One |

Two more facts bear on it. `[COURSE]` coid=62105, COSC 354 Operating Systems (required
by both pages): "Prerequisite(s) COSC 220 and COSC 241 completed with grades of 'C' or
higher" — so every CS major must take COSC 241 in practice. And COSC 243 listed as both
required and a Group A option on the same page means it would count twice unless one of
those listings is an error.

**Encoded meanwhile:** COSC 243 stays in `major_required_courses`, because `[PLANNER]` —
the degree-requirements page — prints it under that heading. The block's `source` no
longer claims `[SEQ]` agrees (it said "All twelve also appear in [SEQ]", which was true
only in the sense that COSC 243 appears in a Group A footnote). COSC 241 is not added as
required: no requirements page says it is; it is reached through COSC 354's prerequisite.

**Ask:** *for the 2026-2028 catalog, is COSC 243 or COSC 241 the required course, and can
the other count as a Group A elective?*

### Q17 — `[PLANNER]` prints three tracks; nothing says how a student is assigned one (new 2026-11-04)

Under the required-courses list, `[PLANNER]` prints three alternative elective layouts:

- **General COSC Track** — 2 × Group A, 3 × Group B, 4 × Group C.
- **Quantum Science and Engineering Track** — COSC 210, 215, 310, 315, 410, 415 named,
  plus one Group B and one Group C elective.
- **Cybersecurity AI Track** — COSC 251, 323, 358, 470, 472, 474 named, plus one Group A
  and one Group B elective.

`[SEQ]` and `[GENED]` mention no tracks, and nothing on a transcript or DegreeWorks audit
in this repo identifies a student's track. Each track is a different elective block, so
Q7/Q8 cannot be encoded until this is answered too.

**Ask:** *are the tracks official options, how is a student's track recorded, and is the
General COSC Track the default?*

### Q18 — Natural Science Complementary Studies: "20-499" and the SCMNS course list (new 2026-11-04)

`[PLANNER]`: "Students must complete six credits in the Complementary Studies Program
which are required of all majors in the School of Computer, Mathematical and Natural
Sciences (SCMNS). Options for satisfying this requirement are outlined under the section
on the SCMNS." Note: "Complementary Studies are two courses chosen from any 20-499 course
in the SCMNS".

Not encodable from reachable pages: "20-499" is almost certainly 200-499 but is printed
as 20; the SCMNS section that lists the options is a `content.php` page behind the WAF;
and which subject prefixes belong to SCMNS is not stated on any reachable page. A
`credits_from` block with a subject filter is the obvious shape once those are known.
`[SEQ]` omits the requirement entirely.

**Ask:** *is it 200-499, and which departments' courses count (e.g. is COSC itself
excluded)?*

### Q19 — The 1-credit physical activity / FIN 101 requirement (new 2026-11-04)

`[PLANNER]`: "XXXX - Phys. Activity or FIN 101 or MIND 101 or DSVG 101 or FACS 102
1 credit". `[SEQ]` First Year Semester Two: "XXXX - Phys. ED. Activity or FIN 101
1 credit". This is the +1 in Q3's 44-credit reconciliation, so the requirement is real.
Not encoded: "Phys. Activity" names no course, `[SEQ]` lists only FIN 101 beside it, and
no `[COURSE]` record for FIN 101, MIND 101, DSVG 101 or FACS 102 was read, so titles
and credits would be guesses.

**Ask:** *which courses count as "Phys. Activity", and are MIND 101, DSVG 101 and FACS 102
valid for CS majors (they are on `[PLANNER]` but not `[SEQ]`)?*

### Q20 — Senior Departmental Comprehensive Examination (new 2026-11-04)

`[PLANNER]`, School-wide Requirements: "To qualify for graduation, students must have
satisfied ... Passed the Senior Departmental Comprehensive Examination". Unambiguous,
but it is not a course, and no block type can express a non-course milestone, so it is
not encoded. Nothing to ask the department; it needs an engine capability (below).

### Q21 — What does an "I" / "INC" (incomplete) grade mean? (new 2026-11-04)

`app/catalog/schema.py` treats **"I"** as NON_PASSING (like F) but **"INC"** as
IN_PROGRESS_GRADES (registered, not finished). Both are left as they are: no page read
for this catalog (`[PLANNER]`, `[SEQ]`, `[GENED]`, any `[COURSE]` record) defines the
incomplete grade, and Morgan's grading-policy section is a `content.php` page behind
the WAF challenge, so it could not be checked. Picking one meaning would be a guess.

**Ask (registrar):** *is an Incomplete recorded as "I" or "INC", does it count as attempted
credit, and does it convert to F after a deadline?*

## Engine capabilities needed (for the audit owner, `app/audit/`)

Verified rules the catalog cannot express today. Proposed shapes only — nothing below
exists in `app/catalog/schema.py`.

1. **One course in two blocks (Q12, IM/MQ).** Rule: `[GENED]` "students may use a
   course(s) to satisfy identical course requirements in up to two of the following
   areas ... The credits earned for the course count only once". Proposed: a block flag
   `shares_courses_with: [major_required_courses]` (or `may_double_count: true`) that
   lets the block see courses already applied to the named block, without adding their
   credits to `total_credits_applied` a second time. Then:
   `gen_ed_information_literacy: n_of 1 [COSC110, INSS141, COSC111], min_grade C for COSC111`.
2. **Distinct disciplines (Q13, Q15, AH/SB).** Rule: "two courses from different
   disciplines". Proposed: `n_of` field `distinct_subjects: true` — at most one applied
   course per subject prefix. Only usable after the department confirms that discipline
   means prefix.
3. **At least one from a subset (Q14, BP).** Rule: "At least one course must be
   lab-based." Proposed: `credits_from` field `min_from: {courses: [...lab-based...], n: 1}`
   plus a way to treat a lecture+lab pair (CHEM 101 + CHEM 101L) as one unit, e.g.
   `units: [[CHEM101, CHEM101L], [BIOL101], ...]`.
4. **NEEDS_ADVISOR only when there is something to review.** Today
   `advisor_approval_required` forces NEEDS_ADVISOR even with nothing applied. If it
   applied only when the block would otherwise be SATISFIED or IN_PROGRESS (UNMET stays
   UNMET), AH/BP/SB could ship now as advisor-confirmed partial blocks.
5. **Milestone block (Q20).** Proposed: `type: milestone` with an id the student record
   can carry as passed/not passed; NEEDS_ADVISOR when the record is silent.

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

### Q11 — COSC 349 prerequisite is catalog-year dependent

`[COURSE]` for COSC 349 Computer Networks states two prerequisites, verbatim:

> "COSC 243 completed with a grade of 'C' or higher **for Fall 2024** COSC 112 only
> completed with a grade of 'C' or higher **for Spring 2025 moving forward**"

The YAML encodes **COSC 112**, on the reading that a 2026-2028 catalog falls under
"Spring 2025 moving forward." That reading is almost certainly right, but it is *our
reading of one source*, not a second source confirming it — and it is the only place
in this file where a prerequisite was chosen rather than transcribed.

It is flagged here rather than left silent because getting it backwards has a real
cost: requiring COSC 243 when the catalog does not would block a student from a course
they are eligible for. Low risk, cheap to confirm.

**Ask:** *for a student on the 2026-2028 catalog, is COSC 349's prerequisite COSC 112 or COSC 243?*
