# Demo guide

A scripted run of the COSC 490 Multimodal Academic Advisor, from a cold machine
to the AI advisor answering questions.

> **All data in this demo is synthetic.** `data/samples/demo_transcript.txt` is a
> fabricated transcript for a fictional student, written for this project. No
> real student record is used anywhere in this repository. Say so out loud when
> demoing — the system's whole argument is about being careful with academic
> records.

**You do not need an API key.** The advisor answers every question below with no
model configured. See [Why no API key is needed](#why-no-api-key-is-needed).

---

## 1. Start it

Two terminals, both from the repo root.

**Terminal 1 — backend**

```bash
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev,ingestion]"     # Windows
# .venv/bin/python -m pip install -e ".[dev,ingestion]"       # macOS / Linux

.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Terminal 2 — frontend**

```bash
cd frontend
npm ci
npm run dev
```

Open **<http://localhost:5173>**.

Start the backend first. The frontend proxies `/api/*` to port 8000, so if the
backend is not up the first page load shows a "cannot reach the backend" message
until you refresh.

**Check it is healthy** before the professor is watching:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/advisor/health
```

`/health` should report `"catalog_loaded": true` and two programs.
`/advisor/health` reports whether a model is available — `"degraded": true` just
means answers come from the degree engine, which is the supported mode.

---

## 2. Environment variables

**Every one of these is optional.** The backend runs with no `.env` at all.

| Variable | Why you'd set it |
|---|---|
| `JWT_SECRET` | Unset means a new signing key per process, so **every backend restart signs you out**. Set it before a demo. |
| `LLM_PROVIDER` | `anthropic` \| `openai` \| `gemini` \| `ollama`. Default `anthropic`. |
| `GEMINI_API_KEY` | Lets Gemini phrase the advisor's answers. |
| `OLLAMA_BASE_URL` / `OLLAMA_CHAT_MODEL` | For a local model with no key and no credits. |
| `DEEPGRAM_API_KEY` | Shows the mic in the advisor panel. Unset means no mic, and typing works as before. |

The file must be **`backend/.env`**. A `.env` at the repo root is silently
ignored — no error, no warning.

```bash
cd backend
cp ../.env.example .env        # Windows PowerShell: copy ..\.env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste as JWT_SECRET
```

**Never commit a key.** CI fails any PR that adds one.

---

## 3. Create a demo account

Safest path: **use the UI**. On first load, click *Create an account* and use a
throwaway address.

```
Email:    demo.student@morgan.edu
Name:     Demo Student
Password: (anything 12+ characters you will remember for 20 minutes)
```

The account lives in `backend/accounts/` as a single JSON file, gitignored. To
clear demo accounts afterwards, delete the files in `backend/accounts/` and
`backend/student_records/`.

> These directories have **no version history and no undo**. Delete files in them
> only when you know what they are, and never with a wildcard against a
> directory you have not listed first.

---

## 4. Walkthrough

### Step 1 — Upload (30 seconds)

Choose **BS Computer Science — Morgan State University (2026-2028)**, select
`data/samples/demo_transcript.txt`, click **Read transcript**.

> **The point to make:** the upload stored nothing. The response literally says
> `stored: false`. Nothing touches the degree audit until a human confirms it.

### Step 2 — Review and confirm

Seven rows appear, each marked **Read exactly** and **Not yet confirmed**.

> **The point to make:** there is deliberately no "accept all" button, and the
> backend has no bulk-accept endpoint — a test asserts one never appears. A
> course reaches a degree audit because a person looked at it, not because a
> regex was confident.

Untick one row before confirming to show the audit change by exactly that course.
Then click **Confirm 7 courses**.

### Step 3 — Dashboard

| What you should see | Value |
|---|---|
| Credits earned | **23.00 of 120** |
| Credit progress | **19.2%** |
| Degree completion % | **deliberately absent** |
| Requirement blocks satisfied | **4 of 6** |
| Recommended next | COSC220, COSC281, COSC349, COSC201, COSC243, … |
| Not yet available | COSC354 (needs COSC220, COSC241), COSC352 (needs COSC220) |

> **The point to make:** the system refuses to print a degree-completion
> percentage. The encoded catalog is partial — six requirement blocks are
> verified, and the ones still awaiting department clarification are *absent,
> not failed*. A number there would be a confident lie, so there is a paragraph
> instead. This is the most important twenty seconds of the demo.

### Step 4 — Ask the advisor

Click **Ask the advisor**.

**Voice (needs `DEEPGRAM_API_KEY`).** Click the mic, say *"What can I take after
COSC 241?"*, click again. Point out that the transcript lands in the box and is
**not** sent: the student checks it first, the same rule as the transcript
review screen. Then press Ask.

---

## 5. Five questions to ask

Each is answerable with no API key. Suggestion chips cover the first four.

**1. "How many credits am I missing?"**

> You have earned 23.00 credits of the 120 your degree requires, so you still
> need 97.00. That puts you 19.2% of the way through by credit count. I am not
> giving you a 'percent of degree complete' number, because the encoded catalog
> is incomplete and any such number would be misleading.

**2. "What courses do I still need to graduate?"**

Two outstanding blocks — Required Courses for the CS Major (10 courses, 31
credits) and Supporting Courses (3 courses, 7 credits) — each with its options
listed, plus the four blocks already satisfied.

**3. "What should I take next semester?"**

Six eligible courses, each with the reason it is recommended and how many later
courses it unlocks. COSC220 leads because it unlocks six. Ends with the two
courses that are *not* yet eligible and what they are waiting on.

**4. "Have I completed my major requirements?"**

> Not yet. These major blocks are still outstanding: Required Courses for
> Computer Science Major: 10 course(s), 31 credit(s) to go.

**5. "Why is COSC 220 recommended?"**

> - required by 1 unfinished requirement: major_required_courses
> - unlocks 6 later course(s): COSC352, COSC354, COSC385, COSC460
> - You meet every prerequisite for it now.
>
> Every line above comes from the catalog's prerequisite graph and requirement
> blocks, not from a language model's opinion.

### Two more worth showing

**"What is the capital of France?"** — it refuses, says it will not guess, and
lists what it *can* answer.

**"Why is COSC 999 recommended?"** — COSC 999 does not exist:

> COSC999 is not on your recommendation list, so I have no engine-derived reason
> to give you for it. **I will not invent one.**

That refusal is the thesis of the project in one sentence.

---

## 6. Why no API key is needed

Every answer above is assembled by a deterministic engine from the student's
confirmed record and the encoded catalog. When a model *is* configured, it is
handed those facts plus the finished answer and asked only to reword it — and if
its reply mentions a course code that is not in the catalog, the reply is thrown
away and the engine's answer is shown instead.

So the label under every answer matters:

- **"Computed by the engine"** — deterministic text, no model involved.
- **"Phrased by \<model\>"** — a model reworded it; every number and course code
  still came from the engine.

Full design: [docs/advisor.md](docs/advisor.md).

To have a model phrase the answers, set `LLM_PROVIDER=gemini` and `GEMINI_API_KEY`
in `backend/.env`, or run Ollama locally (`ollama pull llama3.1`, then
`LLM_PROVIDER=ollama`) and restart the backend.

---

## 7. Known limitations

State these before being asked.

1. **No live LLM provider has ever been called.** This repository has never run
   against a real Anthropic, OpenAI, Gemini or Ollama endpoint — both student
   credit applications were still pending. The four provider integrations are
   tested against a local server that speaks their documented request and
   response shapes, and every failure path is covered, but *"a real key works"*
   is unverified. The engine path — what the demo shows — is fully tested.
2. **The encoded catalog is partial.** Six requirement blocks are verified
   against the 2026-2028 Morgan State catalog. Ten open questions for the
   department are in [`docs/catalog-open-questions.md`](docs/catalog-open-questions.md),
   including six internal contradictions found in the published catalog. Missing
   requirements are absent, not failed, and the system says so everywhere.
3. **Voice input has not met real Deepgram yet.** It is tested against a local
   server speaking Deepgram's documented shapes, like the LLM providers. Run the
   voice step above once with the real key before presenting.
4. **Scanned/photographed transcripts need an API key.** Text and text-layer PDFs
   parse deterministically with no key; a scan returns a 415 naming the reason.
5. **Chat history is in memory.** It is lost on restart and would need a shared
   store if the backend ever ran multi-worker.
6. **Nothing is deployed.** Everything is localhost; there is no URL to share.
7. **A 404 appears in the browser console on sign-in.** Expected: the app asks
   `/students/{id}/record` to decide whether to show Upload or the Dashboard, and
   a new account has no record yet. Nothing is broken.

---

## 8. If something goes wrong mid-demo

| Symptom | Fix |
|---|---|
| "Cannot reach the backend" | Backend is not running, or died. Restart it; confirm `/health`. |
| Signed out unexpectedly | `JWT_SECRET` is unset and the backend restarted. Sign in again. |
| Port already in use | Windows: `netstat -ano \| findstr :8000` then `taskkill /PID <pid> /F`. macOS/Linux: `lsof -ti:8000 \| xargs kill`. |
| Backend serving stale behaviour | uvicorn's `--reload` watcher goes stale on route changes. Restart it manually. |
| Advisor says "running on the degree engine" | Not a fault. That is the no-key mode and the answers are correct. |
| Scanned PDF rejected | Expected without an API key. Use `demo_transcript.txt`. |

---

## 9. One-paragraph summary for the professor

> The degree audit is computed by a deterministic Python rules engine from the
> real Morgan State 2026-2028 catalog, encoded as validated YAML. A language
> model cannot compute any part of it; an architectural test walks the import
> graph and fails the build if the AI layer is imported into the catalog or
> audit packages. Transcript data is extracted with provenance and cannot reach
> the audit until the student confirms it row by row. The conversational advisor
> answers from the engine's output and, where a model is configured, is allowed
> only to rephrase that answer — and any reply that mentions a course outside the
> catalog is discarded. Where the catalog is incomplete, the system refuses to
> report a completion percentage rather than estimate one.
