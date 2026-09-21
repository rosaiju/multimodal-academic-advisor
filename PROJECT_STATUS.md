# Project status — COSC 490 Multimodal AI Academic Advisor

Last updated: **2026-09-20** (end of session)
Branch: **`feature/frontend`** · HEAD: **`3120cfa`** · **3 commits ahead of `origin/feature/frontend`, nothing pushed**

This file is the handover between working sessions. It records what is true right
now, not what we intend — anything listed as working has been run.

---

## The architectural commitment

**The LLM never computes degree progress.** A deterministic Python rules engine
does, and the language model may only state academic facts by calling tools that
engine answers. `backend/tests/test_no_llm_in_engine.py` walks the import graph
and fails the build if `app/audit/` or `app/catalog/` ever imports the AI layer.

Do not propose changes that route degree logic through the model. This rule is
what makes the project a capstone rather than a chatbot wrapper.

---

## What is completed

### Phase 0 — foundation (merged)
Catalog layer, `AuditResult` contract, requirements explorer, `doctor` CLI.

### Catalog (merged, PR #2 → `91d6632`)
All 37 COSC courses plus a verified prerequisite graph in
`data/catalog/morgan_cosc_bs_2026_2028.yaml`. Requirement blocks are
**deliberately incomplete**: only uncontested blocks are encoded, and tests assert
the disputed ones stay absent. Six contradictions between official Morgan sources
are written up in `docs/catalog-open-questions.md` awaiting Dr. Wang.

### Transcript ingestion (PR #3, **open**)
Text, PDF and model-backed vision extraction; the confirmation gate; the student
record store; degree audit endpoints. Deterministic readers always get first
refusal — a scanned PDF declines with `DocumentNotReadable` and falls through to
the model. A model-extracted row is capped at MEDIUM confidence, never HIGH, which
is what forces every model row through human review. **Do not "fix" that cap.**

### Advising engine (PR #4, **open**)
Prerequisite graph, course recommendations, critical path.

### Frontend + this session's work (`feature/frontend`, **no PR yet**)
Upload → review → dashboard, plus the four commits below.

---

## This session's commits

| Commit | What it fixed |
|---|---|
| `1427c68` | DegreeWorks warning counts and over-eager format detection |
| `5490165` | **The parser was hiding 24 rows of transfer credit** |
| `d7079d2` | **Credit earned and degree completion reported as different things** |
| `3120cfa` | **Student records put behind an account** |

### `5490165` — transfer coursework the parser was dropping
A real audit parsed to 37 courses when it lists 61. Four defects:

1. `_ROW` required a bare three-digit number, so every `COSC 116TR` block-transfer
   bucket matched nothing — 24 rows invisible.
2. Result keyed on course code alone, so 14 distinct transfer courses sharing the
   bucket code `COSC116TR` collapsed into one.
3. `WINTER MINI-MESTER` wraps its year to the next line; the year anchors the
   regex, so those rows vanished.
4. `Satisfied by:` required text after the colon, so the wrapped form — label
   alone, value split above and below — matched nothing, and most transfer rows
   came back with no sending institution.

Confirmed rows now carry `source_reference` (the audit's own "Satisfied by" text),
because two bucket rows can match on code, term, grade, credits **and raw line**
and still be different courses.

**Verified against two real audits eight months apart: parsed credits reconcile
exactly with each document's own "Credits applied" total (137 and 150), and
transfer hours with its stated 88.**

### `d7079d2` — the misleading 45%
`percent_complete` divided credit applied to *encoded blocks* by the credits the
*whole degree* requires. Two different scopes in one fraction: a student holding
150 credits was shown "45% complete".

- `percent_complete` is now **`None`** whenever catalog coverage is partial. There
  is no honest degree percentage while requirements are missing, so it is withheld
  rather than repaired. Callers must render the absence.
- `total_credits_earned` (all trusted passing credit) is separated from
  `total_credits_applied` (what landed in an encoded block).
- `total_credits_in_progress` was **hardcoded to zero** — the field existed and
  always lied. Now computed.
- `outside_catalog` reports confirmed credit the catalog has no entry for — 63
  credits that were previously dropped on the floor. Reported, never applied.
- The planner recommended courses the student was **sitting in**: it filtered on
  `passed`, and an in-progress grade is non-passing, so it suggested the Senior
  Project to someone already enrolled. In-progress courses are now excluded and
  surfaced as `under_way` — deliberately **not** folded into `passed`, because an
  unfinished course must still unlock no prerequisite.

### `3120cfa` — authentication
The audit endpoints shipped with **no authentication at all**.

- The `student_id` is **issued by the server**, never accepted from a client. That
  removes a class of problem rather than guarding it: no id for a caller to claim,
  a form to ask for, or the frontend to hardcode.
- `/ingest/confirm` no longer takes `student_id` in its body — the destination
  comes from the token. Closed an authorisation hole and a path-traversal surface
  at once.
- Every `{student_id}` route depends on `authorised_student_id`. A test walks the
  live route table and fails if a new one forgets; it is checked against a
  deliberately unprotected route so it cannot pass vacuously.
- Refusals are **404, not 403** — a 403 confirms the record exists, turning these
  endpoints into a student enumerator.
- Passwords: **scrypt via the standard library**. Tokens: JWT with the algorithm
  pinned on decode. Authentication adds exactly one dependency (PyJWT).
- **No default signing secret.** A committed default is a committed credential.
  Unconfigured deployments get a random per-process secret and `/health` says so.
- **Login rate limiting**: 5 failures from one client against one address start a
  30s cooldown that doubles and is **capped** at 15 min; an idle pair is forgotten
  entirely. Keyed on the *pair* — per-email alone lets anyone lock a classmate
  out, per-IP alone locks out a campus NAT. Failures count whether or not the
  account exists, or a 429 would mean "this address is registered".

**Verified against the running server: 46 cross-account probes, all refused.**

---

## What is currently working

Run end-to-end today with a real Morgan DegreeWorks PDF:

1. Register / sign in → 2. Upload the PDF → 3. Review and confirm rows →
4. Dashboard with credits, gaps and recommendations.

- **533 backend tests pass.** `ruff check` clean. Frontend builds clean.
- Parser extracts **63 rows** from the current audit; credits reconcile exactly
  with the document's own totals.
- Review screen ticks only confirmable rows, so one unusable row cannot block the
  other 62. In-progress rows confirm without inventing a final grade. Summary
  lines (`ORTR 101 TRANSFER OF 24 CREDITS`) are refused however they are edited —
  confirming one would count 24 credits twice.
- Dashboard leads with credit progress and carries an explicit
  *"We cannot tell you how complete your degree is"* banner while coverage is
  partial.
- Auth enforced on every student endpoint, with tested cross-account isolation.

---

## Known bugs and unfinished features

### Unfinished — significant

- **There is no conversational advisor.** `app/advisor/` contains a single 0-line
  `__init__.py`. `app/llm/` does vision extraction only; its docstring says *"No
  tool use, no conversation, no streaming."* `main.py` still has
  `# app.include_router(chat.router)` commented out. The project's thesis —
  *the LLM cannot compute degree progress* — currently guards an architecture
  whose LLM half does not exist.
- **No voice input.** Browser-native Web Speech API was in the approved plan. The
  "multimodal" claim currently rests entirely on PDF parsing.
- **`llm_configured: false`** — no Anthropic or OpenAI key is set. Both student
  credit applications were last known to be pending. The chat layer can be *built*
  and tested against a stubbed provider, but cannot be *demoed* without a key.

### Unfinished — housekeeping

- **15 commits sit on a stacked branch.** `feature/frontend` is stacked on PR #3
  and PR #4 and has no PR of its own. **`main` is 15 commits behind and cannot run
  the demo.** Demoing from a pile of branches is how a live demo dies.
- Branch protection is off — needs GitHub Education (free Pro) on the @morgan.edu
  address, because the repo is private on a free account.
- One teammate's GitHub username still outstanding; two collaborator invitations
  were still pending acceptance.
- `tests/test_each_of_block.py` and `tests/test_pdf_extractor.py` are not
  `ruff format` clean. Pre-existing, untouched this session, cosmetic only.

### Known behaviour that looks wrong but is not

- **`UNIV101` recommended to a senior with 138 credits.** The official audit
  satisfies Freshman Orientation via `ORTR 101 "TRANSFER OF 24 CREDITS"` — the
  summary row we deliberately refuse to treat as coursework. Correct per what we
  encode, wrong in reality. **Catalog question for Dr. Wang, not a code bug.**
- **`ENGL112` recommended while `ENGL102` is in progress.** They are alternatives
  for the same block; defensible, but a professor will notice.
- **Dashboard shows 54 credits applied against 150 on the record.** Only 6
  requirement blocks are encoded, and transfer buckets satisfy no *named*
  requirement. The coverage caveat says so.
- **`doctor`'s 34-orphan warning is expected**, not a bug.

### Needs official clarification — do not guess these

1. The six open questions in `docs/catalog-open-questions.md` (Q1, Q2, Q4, Q7,
   Q8, Q9, Q10).
2. Which requirements the 63 credits of transfer work satisfy. Only the registrar
   can map them; inventing mappings is exactly what this system must not do.
3. **Transfer credit hours conflict**: `applied_course` credits a course at the
   *catalog's* hour value, not the transcript's. `COSC111` is 3 credits on the
   audit and 4 in the catalog; `MATH312` is 4 and 3. Registrar decision —
   deliberately not changed.

### Security limitations, accepted for now

- Password **spraying** defeats a per-pair rate limit by design.
- No password reset flow.
- Nothing verifies that an email belongs to a real Morgan student — that needs
  university SSO.
- Tokens are stateless and cannot be revoked before they expire; the 12-hour TTL
  *is* the revocation window.
- The frontend holds its token **in memory only** — never `localStorage`, which
  any script on the page can read and which outlives the tab on a shared lab
  machine. Refreshing the page signs you out. This is deliberate.

---

## Incident log

**2026-09-20 — account files deleted.** An over-broad `rm backend/accounts/*.json`
during cleanup removed both test account files (email + password hash). The
**academic record survived intact** — MD5 verified unchanged — and was
successfully re-pointed to a newly registered account: 62 courses, 138.0 credits,
all figures identical to pre-incident. Two backups are retained outside the repo.

Lesson recorded here because it will recur: `accounts/` and `student_records/` are
gitignored by design, so **they have no version history and no undo.** Never use a
glob against either directory.

---

## Environment

- Python **3.14.4**. `requires-python` is `>=3.11,<3.15`. Every dependency,
  pdfplumber and pymupdf included, publishes cp314 wheels. **Do not tell the team
  to downgrade Python.**
- `backend/.env` holds `JWT_SECRET` (generated with `secrets.token_urlsafe(48)`).
  It is gitignored and must never be committed. `/health` reports
  `auth_secret_is_ephemeral: false` when it is set correctly.
- `backend/accounts/` and `backend/student_records/` hold live data and are
  gitignored.

### Restarting the servers

Two terminals.

**Terminal 1 — backend** (from `backend/`):

```bash
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

**Terminal 2 — frontend** (from `frontend/`):

```bash
npm install      # first time only
npm run dev
```

Open **http://localhost:5173**. Vite proxies `/api/*` to `127.0.0.1:8000`, so the
browser makes same-origin requests and CORS never applies in development.

> **Warning:** uvicorn's `--reload` watcher went stale three times this session on
> schema changes, serving old code while appearing healthy. After changing a
> pydantic model or adding a route, **restart it manually** rather than trusting
> the reloader. Confirm with `curl -s localhost:8000/health`.

### Running the tests

```bash
cd backend
.venv/Scripts/python -m pytest -q          # 533 tests
.venv/Scripts/python -m ruff check app tests
cd ../frontend && npm run build
```

---

## Next development priorities

**1. Merge the stack to `main`.** Do this first regardless of anything else. ~30
minutes, pure risk reduction: open a PR for `feature/frontend`, land #3 and #4,
and make `main` the thing that runs. Today the demo only exists on a branch.

**2. Then, depending on LLM credits:**

- **Credits available → build the conversational advisor.** This is the highest
  *value* item: it turns the project's best idea from an assertion into a live
  demonstration. Plan:
  - `app/advisor/tools.py` — a tool registry wrapping existing deterministic
    functions (`run_audit`, `build_plan`, catalog lookups). Tools return engine
    output verbatim; **no tool computes anything.**
  - `app/advisor/session.py` — a bounded tool-calling loop, provider-neutral,
    capped by the `max_tokens_per_session` setting already in config.
  - `ChatProvider` protocol in `app/llm/provider.py` beside `VisionProvider`, with
    Anthropic and OpenAI implementations, so whichever credits land is a one-line
    switch.
  - `POST /advisor/chat`, authenticated. **Tools must be invoked with the
    authenticated `student_id`, never one the model supplies** — otherwise chat
    becomes a way to read other students' records by asking nicely. Prompt
    injection as IDOR. Add it to the route-guard test.
  - Tests: the model cannot fabricate a course, cannot name another student's id,
    cannot exceed the turn budget.
  - Degrade gracefully: no key → a clear "not configured" state, never a broken
    page.

- **No credits → build voice input.** Browser-native Web Speech API, no key, no
  cost, and it delivers the other half of the "multimodal" claim.

**3. Blocked on other people** — chase in parallel:
- Dr. Wang: the six catalog questions and the transfer-hours conflict.
- GitHub Education on the @morgan.edu address, to enable branch protection.
- The third teammate's GitHub username; two pending collaborator invitations.
