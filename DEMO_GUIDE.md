# Demo guide — 3-minute in-class script

COSC 490 Progress Report 1. Multimodal Academic Advisor.

Longer reference — every question the advisor can answer, all known limitations,
full troubleshooting table — is in [`docs/demo-reference.md`](docs/demo-reference.md).
This file is only the script.

> **All data shown is synthetic.** `data/samples/demo_transcript.txt` is a
> fabricated transcript for a fictional student. Say so out loud — the project's
> whole argument is about handling academic records carefully.

**No API key is needed.** Every answer below comes from the degree engine. An
optional live-model segment (local Ollama, still no key) is at the end.

---

## Before you walk in (5 minutes, not part of the 3)

Two terminals from the repo root.

```bash
# Terminal 1 — backend
cd backend
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000   # Windows
# .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000     # macOS/Linux

# Terminal 2 — frontend
cd frontend
npm run dev
```

First time on a machine, install first: `pip install -e ".[dev,ingestion]"` in
`backend/`, `npm ci` in `frontend/`.

Pre-flight checklist:

```bash
curl http://127.0.0.1:8000/health          # catalog_loaded: true, 2 programs
curl http://127.0.0.1:8000/advisor/health  # degraded: true is CORRECT, see below
```

- [ ] `auth_secret_is_ephemeral` is `false` — otherwise a backend restart signs you out mid-demo
- [ ] <http://localhost:5173> loads the sign-in screen, **signed out**
- [ ] Have `data/samples/demo_transcript.txt` findable in the file picker
- [ ] Browser zoom ~125% so the back row can read it

**Which account to demo with.** Register a throwaway one live — it takes about
fifteen seconds and it is the only way to guarantee you land on Upload, which is
where the story starts. The existing `rosai2@morgan.edu` account already has a
confirmed record, so signing in as it jumps straight to the Dashboard and skips
the upload and review screens entirely.

```
Email:    demo.student.1@morgan.edu      (bump the number each rehearsal)
Name:     Demo Student
Password: any 12+ characters you will remember for ten minutes
```

**To rehearse twice:** register a new throwaway address, or reset the record on
the one you used via `DELETE /students/{id}/record`. Accounts are single JSON
files in `backend/accounts/`, records in `backend/student_records/`; both are
gitignored. Delete throwaway files there when you are done — but list the
directory first and never use a wildcard, and **leave `rosai2@morgan.edu` alone.**

---

## 0:00 – 0:20 — The thesis

> "An AI advisor that guesses your degree progress is worse than no advisor. So
> in ours, the language model is **not allowed** to compute anything. A
> deterministic Python engine reads the real Morgan State 2026-2028 catalog and
> computes the audit. The model is only ever allowed to reword what the engine
> already decided."

Have <http://localhost:5173> on screen, signed out.

---

## 0:20 – 1:10 — The UI: upload, review, confirm

1. Click **Create an account** and register the throwaway address. You land on Upload.
2. Program: **BS Computer Science — Morgan State University (2026-2028)**.
3. Choose `data/samples/demo_transcript.txt`, click **Read transcript**.

Seven rows appear, each marked *Read exactly* and *Not yet confirmed*.

> **Say this:** "The upload stored nothing — the response literally says
> `stored: false`. There is deliberately no 'accept all' button, and the backend
> has no bulk-accept endpoint; a test asserts one never appears. A course reaches
> the degree audit because a person looked at it, not because a regex was
> confident."

Untick one row, point out the count change, re-tick it, then click
**Confirm 7 courses**.

---

## 1:10 – 1:50 — The dashboard

| What is on screen | Value |
|---|---|
| Credits earned | **23.00 of 120** |
| Credit progress | **19.2%** |
| Degree completion % | **deliberately absent** |
| Requirement blocks satisfied | **4 of 6 encoded** |
| Recommended next | COSC220, COSC281, COSC349, COSC201, COSC243 |
| Not yet available | COSC354, COSC352 (both need COSC220) |

> **Say this — this is the most important 20 seconds:** "It refuses to print a
> degree-completion percentage. Our encoded catalog is partial: six requirement
> blocks are verified, and the ones still awaiting department clarification are
> *absent, not failed*. A percentage there would be a confident lie, so there's a
> paragraph explaining the gap instead."

---

## 1:50 – 2:30 — The advisor

Click **Ask the advisor**. Use the suggestion chips; do not type if you can click.

**1. "What should I take next semester?"** — six eligible courses, each with the
reason and how many later courses it unlocks. COSC220 leads because it unlocks
six. Ends with what is *not* yet eligible and why.

**2. "What is the capital of France?"** — it refuses, says it will not guess, and
lists what it can answer.

> **Say this:** "Note the label under each answer: **Computed by the engine**.
> No model was involved at all — that is why this demo runs with zero API keys,
> which matters because our student credit applications are still pending. When a
> model *is* configured the label changes to *Phrased by \<model\>*, and if its
> reply changes any fact - a credit figure, a course's eligibility, a missing
> prerequisite, the catalog warning - we throw the reply away and show the
> engine's answer."

If time is short, drop question 1 and keep the refusal.

**Voice (optional - needs `DEEPGRAM_API_KEY` in `backend/.env`, use Chrome).** Without the key there is no mic button and nothing else changes. Click the mic once and say
*"Can I take computer science two forty three?"*. The words appear as you speak,
and 1.5 s after you stop, the box reads **"Can I take COSC 243?"** - converted
because COSC 243 is in the catalog. Point out that it was **not** sent: the
student checks it first, the same rule as the transcript review screen. Then
press Ask.

> Deepgram has been checked live only with a synthesized voice (Sep 22). Try
> it once with your own voice before class.

---

## 2:30 – 3:00 — The code

Open `backend/tests/test_no_llm_in_engine.py` on screen.

> **Say this:** "The claim is only credible if it's enforced mechanically. This
> test parses the import graph of `app/catalog/` and `app/audit/` and fails the
> build if anyone imports the AI layer into them. A second test does the same for
> `app/advisor/facts.py` and `answers.py`, which is what guarantees the no-key
> path keeps working. The comment in it says: if this test fails, don't add an
> exception — move the code."

Close on the numbers:

> "784 tests pass. CI runs them on Python 3.11 and 3.14 and builds the frontend
> on Node 22 and 24, on every pull request. Ten pull requests merged; `main` runs
> the whole thing from a fresh clone."

---

## Optional: the live model (adds ~2 minutes, needs Ollama)

Verified on the dev laptop on 2026-10-06 with `qwen2.5:7b`. Expect 15-85 s per
answer on a laptop CPU, so ask one or two questions only, and warm the model up
before class (ask anything once).

Start the backend with the model switched on instead of plain Terminal 1:

```powershell
cd backend
ollama list                                   # confirm qwen2.5:7b is pulled
$env:LLM_PROVIDER="ollama"; $env:OLLAMA_CHAT_MODEL="qwen2.5:7b"; $env:OLLAMA_TIMEOUT_SECONDS="180"
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`curl http://127.0.0.1:8000/advisor/health` should now say
`"llm_available": true, "degraded": false`.

Upload **`data/samples/demo_transcript_in_progress.txt`** instead (same fictional
student plus COSC 220 in progress: 23.00 earned, 4.00 in progress). Then ask:

1. **"What courses am I taking right now?"** - COSC 220, *not counted yet*, and
   it would unblock COSC 352 and 354.
2. **"Can I take COSC 354?"** - not yet: COSC 220 (in progress now) and COSC 241.

Point at the label under each answer. *Phrased by qwen2.5:7b* means the model's
wording passed the consistency check. *Computed by the engine* with a grey note
like "drops the warning that the encoded catalog is partial" means the model's
wording was **caught and thrown away**. Either outcome is a good demo; say which
one happened.

> **Say this:** "The first time we connected a real model, it told a test
> student 'You need 105 more credits - that's 12.5% of the total credits
> required.' Every course code was real, so our original guard let it through.
> 12.5% was progress made, not credits missing. So now every reply is checked
> against the engine's own answer: credits, eligibility, blockers, the catalog
> warning. If anything moved, the student sees the engine's answer instead."

If Ollama is slow or not running, nothing breaks: answers fall back to the
engine with a note saying the model was unavailable or timed out.

---

## If it breaks

| Symptom | Fix |
|---|---|
| "Cannot reach the backend" | Backend died. Restart it, confirm `/health`, refresh. |
| Signed out unexpectedly | `JWT_SECRET` unset and backend restarted. Sign in again. |
| Port in use | `netstat -ano \| findstr :8000` then `taskkill /PID <pid> /F` |
| Advisor says it is "running on the degree engine" | **Not a fault.** That is the no-key mode and the answers are correct. |
| Ollama set but `llm_available: false` | The model is not pulled. `ollama list`, then set `OLLAMA_CHAT_MODEL` to one that is. |
| Live answer never arrives | CPU inference is slow; after `OLLAMA_TIMEOUT_SECONDS` it falls back to the engine. Ask fewer questions live. |
| A 404 in the browser console at sign-in | **Expected.** The app probes `/students/{id}/record` to pick Upload vs Dashboard. |

Full troubleshooting: [`docs/demo-reference.md`](docs/demo-reference.md).
