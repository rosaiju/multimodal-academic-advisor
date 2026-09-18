# Catalog Open Questions (2026-2028)

Contradictions between official Morgan State sources that block encoding parts of
`data/catalog/morgan_cosc_bs_2026_2028.yaml`.

**These need a human answer from the department — not more searching.** Every one of
them was found by reading the official 2026-2028 catalog against itself. Searching
harder produces more conflicting pages, not a resolution.

Ask: **Dr. Shuangbao (Paul) Wang**, CS Department Chair, or a COSC academic advisor.

Until a question is answered, the affected requirement block stays out of the YAML.
An absent block reports as "not yet encoded." A guessed block reports a confident
wrong number to a student deciding whether they graduate. The first failure is
recoverable; the second is the one this project exists to prevent.

| # | Question | Source A | Source B | Blocks |
|---|---|---|---|---|
| 1 | Total major credits | 59 (distribution table) | 65 (section header, **same page**) | major groups, credit totals |
| 2 | Group C size | 4 courses (header + slot count) | 3 courses (footnote 3) | `major_group_c` |
| 3 | General education credits | 40 (university gen-ed page) | 44 (CS program page) | all gen-ed blocks |
| 4 | Upper-division residency | ALL junior/senior major courses at Morgan (dept page) | TWO-THIRDS (university graduation page) | `residency` |
| 5 | Orientation course | ORNS 106 | UNIV 101 | orientation block |
| 6 | **COSC 462 prerequisite** | Catalog lists **COSC 459** | COSC 459 does not exist in the 2026-2028 catalog | COSC462 eligibility |

## Q6 is new — found Sep 18, 2026

`COSC 462 Introduction to Database Security` lists exactly one prerequisite,
`COSC 459`, and there is no COSC 459 anywhere in the 2026-2028 catalog. All 37 COSC
courses were harvested; the number is simply absent.

The prerequisite is **dropped** in the YAML rather than encoded, because the schema's
referential-integrity validator rejects the file outright if a prerequisite names a
course that does not exist — correctly. Dropping it means COSC462 currently appears
to have no prerequisite, which is probably also wrong.

Likely a renumbering that the catalog did not follow through (COSC 459 → some current
course, or COSC 462 should point at a database course that was itself renumbered).
Worth asking specifically: *what is the current prerequisite for COSC 462?*

## Also worth raising: CS Navigator serves stale data

`cs.inavigator.ai` — the department's own AI advisor — returns wrong group counts, a
phantom Group D, no quantum track, and no catalog-year field from its public
`/api/curriculum`. Do not use it as a tiebreaker for any question above. It is,
separately, good demo material for why this project verifies its sources.
