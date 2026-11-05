# Known issues — November 4, 2026

| # | Issue | Impact | Suggested next action |
|---|---|---|---|
| 1 | No hosted model key (Gemini, Anthropic, OpenAI) | All live AI is the local Ollama models; Gemini code is tested with mocks only | Add `GEMINI_API_KEY` and run `backend/scripts/live_llm_smoke.py --provider gemini` |
| 2 | Local rephrasing is slow (50–125 s) and ~2 in 6 replies pass the consistency check | Demo runs with `ADVISOR_REPHRASE=false` | Use a hosted model, or a smaller local one for rephrasing |
| 3 | The consistency check compares facts (numbers, codes, eligibility, titles, caveats), not full meaning | A reworded answer can still mislead in ways it does not test. One such case found live tonight ("either COSC459 or COSC490" for two required courses) is now caught | Keep adding recorded live replies as tests |
| 4 | Catalog is partial: IM, MQ, AH, BP, SB gen eds; electives Groups A–D; tracks; residency; comprehensive exam not encoded | "4 of 9 encoded requirements" is not degree completion; graduation eligibility is never asserted | Department answers to Q1–Q2, Q4, Q7–Q10, Q13–Q21 in `docs/catalog-open-questions.md`; engine capabilities listed there |
| 5 | MATH141 (Precalculus) is planned for students who already passed Calculus I | Looks odd in the planner. It follows the sourced catalog: COSC241's record lists MATH141, and placement rules are not encoded | Ask the department whether Calculus I satisfies COSC241's MATH141 prerequisite |
| 6 | GPA is a low/high range | Morgan's repeat-course and transfer-grade GPA rules are not encoded; the engine shows the range rather than guess | Encode once the policy is sourced |
| 7 | Wellbeing and crisis routing are keyword-only (by design) | A distressed message without those words gets a normal academic answer | Broaden phrases carefully; keep it out of model routing |
| 8 | Vision tested on one clean, typed, synthetic image | Phone photos and real scans untested | Try a photographed printout before relying on it |
| 9 | Voice not re-tested after tonight's merges | The voice code was not changed, but the advisor it feeds was | Test once with the mic before class |
| 10 | Drag-and-drop in the planner not driven by the browser test (the move menu was) | Low | Add a Playwright drag step |
| 11 | "Based on:" under advisor answers shows raw block ids (`major_required_courses`) | Cosmetic | Map citations to block names in AdvisorChat |
| 12 | No Docker Compose or PostgreSQL in the repo (the mission brief assumed both) | Data is JSON files under `backend/accounts` and `backend/student_records`; fine for a demo | Only add if deployment is required |
| 13 | Integration branch is local only | Teammates cannot see it until pushed | Push `integration/nov5-demo` and open a PR when approved |
| 14 | Live voice (PR #16): hands-free and barge-in not tested with a human voice; echo may re-trigger the mic on speakers | Could interrupt itself mid-answer | Use headphones; test once before class |
| 15 | Live voice relaxes the consistency check (`brief=True`): spoken summaries may omit codes and figures, still never invent or distort | Reviewer sign-off needed | Team review on PR #16 |
