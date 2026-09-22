# Multimodal AI Academic Advisor

COSC 490 Senior Capstone — Morgan State University

A multimodal system that acts as a university academic advisor. It handles text,
voice, uploaded transcripts, and academic records, and answers questions about
degree progress, course selection, and student concerns.

> **All student data in this repository is fictional.** No real academic records are
> used at any point. See [Data policy](#data-policy).

---

## Quick start

`main` has everything: backend, frontend, degree engine and the conversational
advisor. Clone it and follow [Setup](#setup), or read **[DEMO_GUIDE.md](DEMO_GUIDE.md)**
for a scripted walkthrough.

```bash
git clone https://github.com/rosaiju/multimodal-academic-advisor.git
cd multimodal-academic-advisor
```

**No API key is needed.** The degree audit and the advisor both work with no
model configured - see [The core design decision](#the-core-design-decision).

---

## The core design decision

**The language model never computes degree progress. A deterministic Python rules
engine does.**

The LLM handles conversation, document extraction, and explanation. It cannot state
an academic fact unless it obtained that fact by calling a tool backed by the rules
engine. Every claim shown to a student carries a provenance tag saying where it came
from.

This is enforced mechanically, not by convention:

- `app/catalog/` and `app/audit/` are forbidden from importing the AI layer.
  `tests/test_no_llm_in_engine.py` walks the import graph and fails the build if
  anyone does.
- Degree requirements live in git-tracked, schema-validated YAML. The model cannot
  modify them.
- AI-extracted transcript data cannot reach the audit engine until a human confirms it.

A consequence worth noting: **the dashboard and degree audit run with zero LLM calls.**
A missing or expired API key cannot break the core demo.

---

## Setup

You need **two** things running: a Python backend and a Node frontend. They run as
separate processes; use the `mprocs` launcher below to manage both from one terminal.

### Prerequisites

| Tool | Version | Check with |
|---|---|---|
| Python | 3.11 – 3.14 | `python --version` |
| Node.js | 18+ | `node --version` |
| Git | any recent | `git --version` |
| mprocs | 0.9+ | `mprocs --version` |

> **Python version:** the project supports `>=3.11,<3.15` and the team develops on
> **3.14.4**. Every dependency, `pdfplumber` and `pymupdf` included, publishes cp314
> wheels. **Do not downgrade Python** to make an install work — if `pip install`
> fails, the cause is something else and downgrading will cost you a day.

### 1. Clone

```bash
git clone https://github.com/rosaiju/multimodal-academic-advisor.git
cd multimodal-academic-advisor
```

### 2. Backend

<details open>
<summary><b>Windows (PowerShell)</b></summary>

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev,ingestion]"
```

If PowerShell refuses to run the activate script
(`running scripts is disabled on this system`):

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

You can also skip activation entirely and call the interpreter directly —
`.venv\Scripts\python -m pytest` — which is what the commands below do.
</details>

<details>
<summary><b>Windows (Git Bash)</b></summary>

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate
pip install -e ".[dev,ingestion]"
```
</details>

<details>
<summary><b>macOS / Linux</b></summary>

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,ingestion]"
```

On Debian/Ubuntu, `python3 -m venv` may ask for `python3-venv`:

```bash
sudo apt install python3-venv
```
</details>

> **Install the `ingestion` extra.** `pip install -e ".[dev]"` alone omits
> `pdfplumber` and `pymupdf`, and PDF transcript upload — the main demo path — then
> fails at runtime rather than at install time. Use `".[dev,ingestion]"`.

Verify:

```bash
pytest -q          # 602 tests
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

### 4. Run both with one command

Install [mprocs](https://github.com/pvolok/mprocs) once, then from the repo root run:

```bash
mprocs
```

The project-level `mprocs.yaml` starts the backend and frontend in separate panes.
Press `q` to stop both and quit. Open **<http://localhost:5173>**.

On Windows, install mprocs with `winget install --id pvolok.mprocs --exact` (or
`scoop install mprocs`).

### Manual startup (without mprocs)

From the repo root, start each command in its own terminal.

**Terminal 1 — backend:**

```bash
# Windows
cd backend && .venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# macOS / Linux
cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Terminal 2 — frontend:**

```bash
cd frontend && npm run dev
```

Open **<http://localhost:5173>**.

Vite proxies `/api/*` to `127.0.0.1:8000`, so the browser makes same-origin requests
and CORS never applies in development. Start the backend first, or the first page
load will show a proxy error until you refresh.

Confirm the backend is healthy at <http://127.0.0.1:8000/health>, or browse the API
at <http://127.0.0.1:8000/docs>.

---

## Configuration

**Every setting is optional. The project runs with no configuration at all** — the
catalog loads, the audit engine runs, transcripts parse, and sign-in works. You only
need a `.env` to add an LLM key or to keep sessions alive across restarts.

### Where `.env` goes

```bash
cd backend
cp ../.env.example .env       # Windows PowerShell: copy ..\.env.example .env
```

> **`.env.example` lives at the repo root, but the loader only reads `backend/.env`.**
> A `.env` left at the repo root is **silently ignored** — no error, no warning. If a
> value you set appears to have no effect, check you put the file in `backend/`.

### Settings worth knowing

| Variable | Default | Why you'd set it |
|---|---|---|
| `JWT_SECRET` | *(random per process)* | Unset means a new signing key each restart, so **every restart signs everyone out**. Fine for solo dev, annoying in a demo. |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | *(empty)* | Only needed for **scanned/image** PDFs. Text-layer PDFs parse without any key. |
| `LLM_PROVIDER` | `anthropic` | Flip to whichever student credits land. |
| `ACCESS_TOKEN_TTL_MINUTES` | `720` | Tokens are stateless and cannot be revoked early — this **is** the revocation window. |

Generate a signing secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`GET /health` reports `auth_secret_is_ephemeral: true` when `JWT_SECRET` is unset, so
you can always tell which mode you're in.

> **Do not set `CATALOG_DIR` or `DATABASE_URL`** unless you genuinely need to move
> them. They resolve against your *current working directory*, while the built-in
> defaults are absolute paths derived from the repo layout and are correct from
> anywhere. They are commented out in `.env.example` for this reason.

### Secrets

`.env`, `backend/accounts/` and `backend/student_records/` are gitignored and must
stay that way. CI has a job that fails any PR adding a tracked `.env` or a string
matching a known key format — treat a red `secrets` job as "rotate that key", not
"delete the line and force-push".

**Never commit an API key.** A key pushed to a shared repo has to be rotated, not
just removed, because it remains in the git history.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| The advisor says "running on the degree engine" | Expected with no API key. Answers are still correct - they are computed, not generated. Set `GEMINI_API_KEY` (or run Ollama) to have a model phrase them. |
| PDF upload fails, tests pass | Installed without the `ingestion` extra. Re-run `pip install -e ".[dev,ingestion]"`. |
| A `.env` value has no effect | The file is at the repo root. It must be `backend/.env`. |
| Signed out after every restart | `JWT_SECRET` unset, so the key is regenerated per process. Set it in `backend/.env`. |
| Scanned PDF rejected as `DocumentNotReadable` | Expected with no API key — vision extraction is off. Text-layer PDFs still work. |
| `catalog_dir ... does not exist` | You set `CATALOG_DIR` and launched from a different directory. Comment it out. |
| Server returns old behaviour after an edit | **uvicorn's `--reload` watcher goes stale on schema/route changes** and serves old code while looking healthy. Restart it manually and confirm with `/health`. |
| Port 8000 or 5173 already in use | Another copy is still running. Windows: `netstat -ano \| findstr :8000` then `taskkill /PID <pid> /F`. macOS/Linux: `lsof -ti:8000 \| xargs kill`. |
| `pip install` fails on a wheel | Do **not** downgrade Python. Upgrade pip first: `python -m pip install --upgrade pip`. |
| PowerShell won't activate the venv | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or call `.venv\Scripts\python` directly. |

---

## Running the checks

Everything CI runs, in the order it runs it:

```bash
cd backend
pytest -q
ruff check .
black --check .

cd ../frontend
npm run build
```

CI runs on **every** pull request regardless of its base branch, on Python 3.11 and
3.14.

---

## Inspecting a catalog

`doctor` validates a catalog file and prints it in plain English. Use it while
encoding requirements — "the server starts" is a weak signal that a 200-line YAML
file is correct.

```bash
cd backend
python -m app.catalog.doctor ../data/catalog/morgan_cosc_bs_2026_2028.yaml
python -m app.catalog.doctor ../data/catalog --strict          # warnings fail, for CI
python -m app.catalog.doctor ../data/catalog/demo_university_cs.yaml --course COSC490
```

It exits non-zero on a dangling course reference, and warns about orphan courses,
over-constrained degrees, and prerequisite cycles.

> `doctor`'s **34-orphan warning on the Morgan catalog is expected, not a bug.**
> Requirement blocks are deliberately incomplete while six contradictions between
> official Morgan sources await clarification — see
> [`docs/catalog-open-questions.md`](docs/catalog-open-questions.md).

The same data is served read-only over HTTP:

| Endpoint | Returns |
|---|---|
| `GET /catalog/{program_id}/requirements` | Every block, stated in plain English |
| `GET /catalog/{program_id}/courses?subject=COSC` | Courses, optionally by subject |
| `GET /catalog/{program_id}/courses/{code}/prerequisites` | Prerequisite tree and depth |

---

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Repo, config, catalog schema + loader, `AuditResult` contract | **Done** |
| 0.5 | Requirements explorer, catalog router, `doctor` CLI | **Done** |
| 1 | Morgan COSC catalog (37 courses + prereq graph) | **Done** |
| 1 | Audit engine, optimal matcher, prereq DAG | **Done** (PR #3/#4) |
| 1 | Transcript ingestion: text, PDF, DegreeWorks, vision | **Done** (PR #3) |
| 1 | Accounts, sign-in, per-student authorisation | **Done** (PR #5) |
| 1 | React frontend: upload → review → dashboard | **Done** (PR #5) |
| 1 | **Conversational advisor** | **Done** — see [docs/advisor.md](docs/advisor.md) |
| 2 | **Voice input (Web Speech API)** | **Not started** |
| 3 | What-if simulation, multi-term planning | Not started |

**What runs end to end today** on `main`: register → sign in → upload a transcript
or DegreeWorks PDF → review and confirm rows → dashboard with credits, gaps and
recommendations → ask the advisor questions about it. 602 backend tests pass, and
the whole path is verified in a real browser.

**What does not exist yet:** voice input. The "multimodal" claim rests on text and
PDF parsing; vision extraction of scanned documents is implemented but needs an
API key to run.

**Not yet verified:** no live LLM provider has ever been called - this machine has
no key and no Ollama. The advisor answers from the degree engine, which is the
supported mode, and the model-phrasing path is tested only against a local stub.
See [docs/advisor.md](docs/advisor.md#what-is-not-tested).

See [`PROJECT_STATUS.md`](PROJECT_STATUS.md) for the detailed handover, known bugs,
and the reasoning behind decisions that look wrong but are not.

---

## Layout

```
backend/app/
  catalog/     Degree requirements: YAML schema, loader, registry.   NO LLM.
  audit/       The rules engine: blocks, matcher, prereq DAG.        NO LLM.
  ingestion/   PDF and image transcript extraction + validation.
  llm/         Provider-neutral model access: vision (provider.py) and
               chat (chat.py). Anthropic / OpenAI / Gemini / Ollama.
  advisor/     The conversational advisor. facts.py computes the grounding
               from the audit, answers.py writes the answer with NO model,
               chat.py optionally has a model rephrase it and checks the
               result. See docs/advisor.md.
  auth/        Accounts, password hashing, tokens.
  schemas/     Pydantic contracts shared across the whole system.
data/catalog/  The degree catalogs themselves. Source of truth.
frontend/      React + Vite + Tailwind.
```

Runtime data — `backend/accounts/`, `backend/student_records/`, `backend/uploads/` —
is gitignored, which means **it has no version history and no undo.** Never run a
glob delete against those directories.

---

## Adding a degree program

Drop a new YAML file in `data/catalog/`. No code changes. The loader validates it at
startup and refuses to start on a typo — a course code named in a requirement block
that does not exist in the course list is a fatal error, not a warning.

See `data/catalog/demo_university_cs.yaml` for the format and
`app/catalog/schema.py` for the full field reference.

---

## Provenance tags

| Tag | Means | Can affect a degree audit? |
|---|---|---|
| `verified` | From the catalog or computed by the engine | Yes |
| `student_confirmed` | AI-extracted, then confirmed by the student | Yes |
| `ai_suggested` | LLM advice or phrasing | No |
| `unverified_extraction` | AI-extracted, not yet confirmed | **No** |

A model-extracted row is capped at MEDIUM confidence and can never be HIGH. That cap
is what forces every model-read row through human review — **do not "fix" it.**

---

## Data policy

Student records are generated fixtures. The repository contains no FERPA-covered
data and none should ever be added. Do not upload a real transcript to a development
instance.

The wellbeing feature provides **referrals only** — it does not diagnose, assess
severity, or offer treatment. Crisis-keyword matches bypass the language model
entirely and return a fixed referral card.

---

## Team and workflow

| Owner | Area |
|---|---|
| Person 1 | Degree audit engine, catalog encoding, matcher, prereq DAG |
| Person 2 | LLM provider layer, tool-calling, prompts, wellbeing rules |
| Person 3 | Data models, transcript ingestion, validation, seed data |
| Person 4 | React frontend, API routers, integration, deployment |

Branch per feature, PR reviewed by one teammate, no direct commits to `main`.
Weekly integration session where the demo must run end to end.

Conventions, commit style and the review checklist:
[`CONTRIBUTING.md`](CONTRIBUTING.md).

> **Branch protection is not enabled** — it needs GitHub Pro, which is free for
> students via GitHub Education. Until then "no direct commits to `main`" is an
> honour-system rule, not an enforced one. Please honour it.
