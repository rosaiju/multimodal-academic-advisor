# Final demo guide — November 5, 2026

Everything below was run and checked on this laptop on 2026-11-04 from the
integration worktree `C:\Users\rohan\advisor-ai-integration`
(branch `integration/nov5-demo`). Nothing here is on GitHub's `main` yet.

**All data shown is fictional.** Say so out loud.

---

## 1. Start (2 minutes before class)

1. Check Ollama is running and has both models:
   ```powershell
   ollama list     # must show qwen2.5:7b and qwen2.5vl:3b
   ```
2. Start both servers with one command (each opens its own window — keep them open):
   ```powershell
   cd C:\Users\rohan\advisor-ai          # or any worktree of the repo
   .\scripts\start-demo.ps1
   ```
   It sets itself up: creates `backend\.venv` from an installed Python 3.11–3.14 if
   missing (or moves a broken one aside), installs backend packages when
   `pyproject.toml` changes, runs `npm install` when `node_modules` is missing, copies
   the main checkout's `backend\.env` into a new worktree (never overwriting, never
   printed), refuses busy ports without stopping anything, and waits until both
   servers answer before printing `Ready`. Stop with `.\scripts\start-demo.ps1 -Stop`
   (stops only the windows it started). Logs: `.demo\backend.log`, `.demo\frontend.log`.
   To choose the Python used for the venv: `$env:ADVISOR_PYTHON = 'C:\path\python.exe'`.
3. Open <http://localhost:5173>, signed out, zoom ~125%.
4. **Warm the models** (the first call loads them, ~20 s): register a throwaway account,
   upload `data\samples\demo_transcript.png`, ask one question. Then sign out and register
   a fresh account for the real demo.

Environment the script sets (AI mode):

| Variable | Value | Why |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | the local model |
| `OLLAMA_CHAT_MODEL` | `qwen2.5:7b` | reads questions |
| `ADVISOR_REPHRASE` | `false` | skips the 1–2 min rephrasing step; the model still chooses the answer |
| `VISION_PROVIDER` | `ollama` | reads transcript images |
| `OLLAMA_VISION_MODEL` | `qwen2.5vl:3b` | local vision model (3.2 GB) |

`DEEPGRAM_API_KEY` and `JWT_SECRET` are already in `backend\.env` (never show that file).

## 2. Fictional data

| File | Student | Use |
|---|---|---|
| `data\samples\demo_transcript.png` | 7 courses, 23 credits | **image** upload — shows vision |
| `data\samples\demo_transcript.txt` | same 7 courses | backup if vision is slow |
| `data\samples\advanced_student_transcript.txt` | 86 credits, a retake, 1 in progress | shows a nearly-finished student |

Account to register: `demo.student.5@morgan.edu` (any new address), any 12+ char password.

## 3. The demo (verified results)

| Step | Do | Expected (as tested) |
|---|---|---|
| 1 | Register | lands on **Upload transcript** |
| 2 | Upload `demo_transcript.png` → **Read transcript** | ~35 s warm (88–105 s cold). 7 rows, all correct, every row marked **Not yet confirmed** / "read from a scanned or photographed document" |
| 3 | Untick a row, re-tick, **Confirm 7 courses** | count goes 6 → 7; nothing is saved before Confirm |
| 4 | **Progress** | 23.00 / 120 credits, 19%, **4 of 9** encoded requirements, COSC220 first (unlocks 10); "Computed by the engine" |
| 5 | **Ask the advisor**: "What do I still need to graduate?" | 3–14 s. Label **Question read by the model**; notice says the engine wrote the answer |
| 6 | Follow-up: "So I'm basically done then, right?" | 6–8 s. Model reads the conversation, answers "not yet" with the open requirements |
| 7 | "I'm overwhelmed and worried I won't graduate on time." | instant. Supportive reply with the student's real numbers, points to advisor and counseling; no diagnosis, no date promise |
| 8 | Mic button → say "Can I take COSC 354?" → **Ask** | words appear while speaking; nothing is sent until Ask |
| 9 | **Semester plan** | term-by-term plan, caveat box, "Last planned term — not a graduation date". Use a course's move menu to put it before its prerequisite → flagged (dragging works too but was not browser-tested) |
| 10 | **What-if & unlocks** | "what if I take COSC 220" re-audits a copy; unlock tree for a course |
| 11 | **Compare courses**, **Deadlines** | side-by-side facts; sourced registration dates; "Add to my calendar" downloads a valid `.ics` |

### What to say about the AI (the hybrid architecture)

> "AI understands and communicates; deterministic code calculates and verifies. The model
> reads a photo of a transcript and reads the student's questions — including follow-ups —
> to decide which answer they need. The engine computes every credit, prerequisite and
> requirement. Nothing the model extracts counts until the student confirms it, and if the
> model ever rewrites an answer, a checker throws it away if any fact changed."

Things the AI does that the engine alone cannot:
- Read a transcript **image** (the engine only parses text).
- Understand "So I'm basically done then, right?" — keywords sent that to the credit
  count before; the model uses the conversation and routes it to remaining requirements.
- Understand loosely worded questions ("lay out my remaining terms", "if I bomb COSC 220").

## 4. Verification status

| Capability | Status |
|---|---|
| Image transcript reading (local qwen2.5vl:3b) | **Verified live** in the browser: 7/7 rows correct |
| Question understanding (local qwen2.5:7b) | **Verified live** in the browser and via API (follow-up, plan, what-if) |
| Model rephrasing + consistency check | Verified live; slow (50–125 s) and often discarded, so **off for the demo** |
| Voice (Deepgram) | Verified with a human voice on 2026-10-06. **Not re-tested tonight** (no microphone in the automated run) — test once before class |
| Gemini | **Blocked**: no `GEMINI_API_KEY`. Request/response code tested with mocks only |
| Anthropic / OpenAI | **Blocked**: no keys |
| Full UI walkthrough (12 steps) | **Verified** in Chromium on the real ports with AI mode on |

## 5. Fallbacks

| Problem | Do this |
|---|---|
| Image upload slow or fails | Upload `demo_transcript.txt` instead — same 7 courses, instant |
| Model slow / Ollama down | Answers still come — the advisor falls back to keywords and says so ("Matched by keywords"). Nothing breaks |
| Laptop overheating | Close the windows, run `.\scripts\start-demo.ps1 -EngineOnly` — no model at all, everything instant |
| Mic not heard | Type the same question — same engine answer |
| "Cannot reach the backend" / 502 | A server window was closed. Run `.\scripts\start-demo.ps1 -Stop`, then `.\scripts\start-demo.ps1` |
| "Port 8000 is already in use" | Another server holds it; the script names the process. Close that window (the script never stops it for you) |
| Lands on Progress instead of Upload | That account already has a record — sign out, register a new address |

---

## 6. Combined branch with Emmanuel's live voice (PR #16)

Branch `integration/nov5-demo-voice`, folder `C:\Users\rohan\advisor-ai-voice`. Same start:
`cd C:\Users\rohan\advisor-ai-voice; .\scripts\start-demo.ps1`. Run only ONE folder's servers
at a time (both use ports 8000/5173).

Extra demo steps (checked 2026-11-04 on the running app, API + browser):

| Do | Expected (as tested) |
|---|---|
| **Read aloud** under any advisor answer | Deepgram speaks it; real MP3 back in ~1.5 s |
| **Talk to the advisor** → say "Can I take COSC 354?" | If speech confidence is low: "Did you mean COSC 354?" first. Confident: "For COSC 354: …" (not eligible, COSC220/COSC241 missing) |
| Say "Should I take summer classes to graduate early?" | Honest answer: can't plan summer terms; 97 credits left; eligible courses; ask an advisor |
| Say "Where do I park on campus?" | Declines, shows curated Morgan links (Registrar, CS dept, catalog) |

Not verified: hands-free mode and barge-in with a real human voice and speakers (use
headphones to stop the advisor's voice re-triggering the mic). Emmanuel's notes say the same.

---

## 7. Gemini writes the answers (branch `feature/gemini-advising`)

Folder `C:\Users\rohan\advisor-ai-gemini`. Put the key in `backend\.env` as
`GEMINI_API_KEY=...` (never commit it, never show the file). Then
`.\scripts\start-demo.ps1` prints `Mode: Gemini writes the answers (checked by the engine)`.
The backend window's first lines include
`advisor answers: provider=gemini model=gemini-3.5-flash-lite api_key=loaded rephrase=on`.

How an answer is made: Gemini reads the question and conversation (routing), the engine
computes the facts and a prepared answer, Gemini writes the reply from those facts, and
the consistency check compares it with the engine. Passed -> shown as **Written by Gemini
· checked by the engine**. Failed -> discarded, engine answer shown as **Fallback ·
Gemini's answer failed the fact check**. Google busy/down -> **Fallback · Gemini
unavailable**. Crisis replies are fixed text and never go to Gemini.

Live results (2026-11-05, fictional student, real Gemini calls): 8-question conversation
via the API: 5 written by Gemini, 3 rejected by the check (dropped figures/codes);
4 browser questions: all 4 written by Gemini; checker also caught Gemini calling two
blocked courses (MATH242, MATH312) available. Google returned 503 "high demand" often;
each is retried twice, then the engine answers.
