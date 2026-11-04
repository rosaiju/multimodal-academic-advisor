# UI walkthrough (browser test)

`backend/scripts/browser_walkthrough.py` drives a real Chromium through the full
student journey and screenshots every step. It is the pre-demo smoke test.

## What it checks

1. Register a throwaway fictional account (`walkthrough-<random>@example.test`).
2. Upload `data/samples/demo_transcript.txt`; 7 rows are read.
3. Review: untick a row (6 of 7), retick it (7 of 7).
4. Confirm; land on **Progress**.
5. Progress shows `23.00 of 120 required credits earned`, the "Computed by the
   engine" label, and the same block counts the engine's `/audit/summary` returns.
   No text implies a graduation date.
6. **Ask the advisor**: "What should I take next semester?" gets an answer with
   the "Computed by the engine" label.
7. **What-if & unlocks**: what-if on COSC 220 and the unlock explorer both answer.
8. **Semester plan**: the caveat is shown, and moving a course into a term before
   its prerequisite is flagged as an error by the backend validator.
9. **Compare courses**: COSC220 vs COSC281.
10. **Deadlines**: dates with sources; "Add to my calendar" downloads a valid `.ics`.
11. Every tab at 375px wide: the page never scrolls sideways.
12. Sign out.

## Running it

It needs Playwright for Python and a Chromium build. It does not start servers.

```bash
# backend (from backend/), on a spare port, with data kept out of the repo:
USER_DIR=/tmp/wt/accounts STUDENT_RECORD_DIR=/tmp/wt/records \
  .venv/Scripts/python -m uvicorn app.main:app --port 8004

# frontend (from frontend/), proxying /api to that backend:
VITE_API_TARGET=http://127.0.0.1:8004 npx vite --port 5174

# the walkthrough:
py backend/scripts/browser_walkthrough.py --base-url http://localhost:5174 --out shots/
```

Exit code 0 means every step passed. If you run it against the normal data
folders, delete the account and record files it prints at the end.

## Sample transcripts

| File | Student | Shape |
|---|---|---|
| `data/samples/demo_transcript.txt` | first-year, 23 credits | the walkthrough's input |
| `data/samples/demo_transcript_in_progress.txt` | same, plus COSC220 in progress | |
| `data/samples/advanced_student_transcript.txt` | 86 credits over six terms | one retake (COSC281 F, then B), one in progress (COSC459) |

All three are fictional. The advanced transcript parses with every row
high-confidence; its only extraction warning is the expected retake notice.
