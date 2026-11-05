# Final implementation report — November 4–5, 2026

Branch `integration/nov5-demo` (local, **not pushed, not merged to main**), based on
`main` at `5249737`. Built by a lead plus four agents, each in its own git worktree and
branch, merged by the lead after review and tests.

## Results

| Check | Baseline (`main`) | Now |
|---|---|---|
| Backend tests (pytest) | 908 | **1262 passed** |
| Frontend tests (Vitest) | 11 | **26 passed** |
| ruff / black / oxlint / `npm run build` | clean | clean |
| Catalog doctor | pass | pass |
| Browser walkthrough (12 steps, Chromium) | — | **all pass**, on the real ports with AI mode on |
| Live AI in the browser (image → confirm → 3 questions) | — | **pass** |

## Teammate work integrated

- **Sameer Shiwakoti** — `feature/explain-compare-deadlines` (which already contains
  `feature/semester-planner`): semester planner with drag-and-drop and validation,
  requirement explanations, course comparison, sourced deadlines with `.ics` export.
  Merged once with `--no-ff` (`c43e98a`); his commits `0f9189d` and `3f5e1e9` keep his
  authorship. The only conflicts were additive (his tabs next to Emmanuel's Explorer).
- Emmanuel's what-if/unlock explorer and speech providers, and Joshua's frontend tests
  were already on `main`; both kept and now covered by more tests.

## Features completed or modified

**AI and multimodal (agent/ai + lead)**
- A model now **reads the question and the recent conversation** and picks which engine
  answer it needs (strict JSON, validated intent and catalog codes; falls back to keywords
  on any failure). Fixes follow-ups like "So I'm basically done then, right?".
- **Transcript images** are read by a local vision model (qwen2.5vl:3b): 7/7 rows correct on
  the demo image; rows arrive unverified and need the student's confirmation.
- New engine answers routed from chat: semester plan, course comparison, "why doesn't this
  count", "what if I fail COSC 220".
- **Wellbeing**: a supportive reply built from the student's own numbers; a fixed crisis
  reply with 911 / 988; no diagnosis, no emotion detection, no date promises.
- Credit answer no longer says "you still need 0" when requirements are open.
- `ADVISOR_REPHRASE=false` (lead): model understanding without the slow rephrasing step.
- Lead fix from a live failure: "either COSC459 or COSC490" for two *required* courses is
  now rejected by the consistency check, and the engine says "All of these are needed".
- Security: a missing-record 404 no longer leaks the server's file path.

**Engine (agent/engine)**: retakes earned once, in-progress (REG/INC) not counted as earned,
GPA range when repeat/transfer policy is unknown, residency fixes, `graduation_blockers`,
D-in-a-C-minimum treated as retake in plan/compare/validate, planner shows requirement
names not ids, no unverified stubs scheduled, `graduation_caveat`. Rules documented in
`docs/engine-rules.md`.

**Catalog (agent/catalog)**: 12 prerequisites sourced from Morgan course records
(prerequisite coverage 20 → 32 courses); MATH141 corrected to Precalculus, 4 cr; open
questions re-verified with quotes, Q16–Q21 added. Block count unchanged (9) — no block
could be encoded without guessing.

**Frontend (agent/frontend)**: navigation grouped into setup steps and degree tools; engine
labels on every facts screen; loading/error states; 375 px layout fixed; Playwright
walkthrough (`backend/scripts/browser_walkthrough.py`); advanced fictional transcript.

## Branches and commits

| Branch | Commits |
|---|---|
| agent/engine | ab58eba, 1eef079, 19098df |
| agent/catalog | d5ff0cd |
| agent/ai | d2bb3d3, 3c213df, 6e9de0a, 9580d56, e5d9d2a, abc6bfe, 5d7b36c |
| agent/frontend | b627312, 1f71848, e86697a, fe92e53, 7193253 |
| integration/nov5-demo (lead) | c43e98a, 874056f, 6965a85, 174cd40, 70b3611, 6058c14, e3b9a4d, e30568c, 0c1f378, 4805f9f, 3955ce7, + docs commit |

## Assignment compliance

| Assignment asks | Status |
|---|---|
| Multiple data types | Text questions, **spoken** questions (Deepgram), transcript **images** (vision model), text/PDF transcripts, structured catalog and degree rules |
| "Hear and see the student" | Hears: verified with a human voice 2026-10-06. Sees: the student's documents, verified tonight. No webcam/face analysis — deliberately |
| Accurate feedback on degree progress | Deterministic engine, 1262 tests; accurate within the encoded catalog, which is partial and says so |
| Course suggestions | Recommendations, semester plan, what-if, comparison |
| Emotional issues | Supportive, fact-grounded reply and crisis resources; no diagnosis |
| Reference tech (Vertex/Gemini) | Gemini provider exists; **blocked on credentials** |

Not 100% complete: see `docs/KNOWN_ISSUES.md`.

## Update, November 5 — combined branch `integration/nov5-demo-voice`

Contains everything above plus `origin/main` as of `a380327`, i.e. Emmanuel's PR #16
(live voice advisor) including the two commits he added before merging it.

| Check (run 2026-11-05) | Result |
|---|---|
| Backend tests | **1330 passed** |
| Frontend tests | **55 passed** (10 files) |
| ruff / black / oxlint / build | clean |
| Catalog doctor | pass; `--strict` fails on the known "34 courses in no block" warning, same on `main`, not run by CI |
| Browser walkthrough, 12 steps | **all pass, twice in a row** (after the planner fix below) |
| Text-to-speech (Deepgram Aura) | real MP3, ~1.8 s |
| Live voice turns via API | read-back, confident answer, "list them all", summer, out-of-scope links all correct |
| Gemini | code present (chat only), 17 mocked tests pass; live **blocked: no key**; without one, health says "GEMINI_API_KEY is not set" and the app falls back |

Lead fixes during this integration:
- Routing prompt crashed on PR #16's new `graduate_early` intent → every question fell
  back to keywords. Fixed + test (`d2159ce`).
- PR #16's "list them all" answered from the previous question, but model routing then
  re-routed from "list them all" itself. Routing now uses the question being answered.
  Test + verified live (`8e590de`).
- Semester planner race (Sameer's component): a slower plan or an older check could
  overwrite the screen, showing "1 problem" with nothing red. Seen in the walkthrough,
  reproduced by two failing Vitest tests, fixed (`b1dd21e`).
