# Autonomous progress log — November 5 demo

Integration branch: `integration/nov5-demo` (local only, not pushed).
Worktree: `C:\Users\rohan\advisor-ai-integration`.

## Baseline (2026-11-04)

- `main` = `5249737` (PR #10 merged 2026-10-15). 908 backend tests passed on it.
- No uncommitted work in the main checkout. One old stash (`stash@{0}`, the abandoned
  RelayFlow experiment) left untouched.
- Credentials present: `DEEPGRAM_API_KEY`, `JWT_SECRET`. **Absent:** `GEMINI_API_KEY`,
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`. No hosted language or vision model can be called.
- Local models (Ollama): `qwen2.5:7b`, `llama3.2:3b`, `qwen3:8b`, `deepseek-r1` variants,
  `phi4` variants. None of them accepts images.
- Measured local latency (qwen2.5:7b through the real advisor, 2026-11-04): 15–71 s per
  question; 2 of 6 rephrasings pass the consistency check.

## Step 1 — Sameer's branches (lead)

- `origin/feature/explain-compare-deadlines` (3f5e1e9) already contains
  `origin/feature/semester-planner` (0f9189d), so only the former was merged.
- Merged with `--no-ff` as `c43e98a`; Sameer's two commits keep his authorship.
- Conflicts in `backend/app/main.py` and `frontend/src/App.jsx` were purely additive
  (Emmanuel's Explorer tab vs Sameer's three tabs); both kept.
- After merge: **1006 backend tests pass**, ruff + black clean, **11 frontend tests pass**,
  oxlint clean, `npm run build` clean.

## Step 2 — agents

One worktree and branch per agent, all from `c43e98a`:

| Agent | Branch | Worktree | Owns |
|---|---|---|---|
| 1 Engine | `agent/engine` | `advisor-ai-agent-engine` | `backend/app/audit/**` |
| 2 Catalog | `agent/catalog` | `advisor-ai-agent-catalog` | `data/catalog/**`, `backend/app/catalog/**`, `docs/catalog-open-questions.md` |
| 3 AI | `agent/ai` | `advisor-ai-agent-ai` | `backend/app/advisor/**`, `backend/app/llm/**`, `backend/app/routers/chat.py`, `frontend/src/components/AdvisorChat.jsx`, `docs/advisor.md` |
| 4 Frontend | `agent/frontend` | `advisor-ai-agent-frontend` | `frontend/src/**` except AdvisorChat.jsx, `data/samples/**`, browser tests |

Shared files (`backend/app/main.py`, `README.md`, the final docs) are edited by the lead only.

## Requirements traceability (baseline, before agent work)

Assignment: "a multi-model AI system representing a faculty advisor ... learn, reason, or act
across multiple data types ... hear and see the student, provide accurate feedback and
recommendations about degree progress, course suggestions, and even emotion issues."

| Requirement | Where | Baseline status |
|---|---|---|
| Typed questions answered conversationally | `app/advisor/chat.py`, AdvisorChat | Implemented and verified (engine answers); model phrasing verified live with local qwen2.5 |
| Understanding natural-language questions | `answers.detect_intent` | **Partial**: keyword routing only; follow-ups fail |
| Hear the student (speech) | Deepgram, `app/speech`, PR #9/#10 | Implemented and verified with a human voice (2026-10-06) |
| See the student's documents (image) | `app/llm/transcript_vision.py` | **Implemented but unverified**: needs Anthropic/OpenAI key; none configured |
| Transcript text/PDF/DegreeWorks ingestion | `app/ingestion` | Implemented and verified |
| Accurate degree progress | `app/audit` + catalog | Implemented and verified within encoded coverage; catalog partial (open questions) |
| Course suggestions | `app/audit/planning.py` | Implemented and verified |
| Multi-semester planning | `app/audit/schedule.py` (Sameer) | Implemented, tests pass; browser-unverified |
| What-if / unlocks | Explorer (Emmanuel) | Implemented, tests pass |
| Compare courses / explain requirements / deadlines | Sameer | Implemented, tests pass; browser-unverified |
| Emotional / mentoring support | — | **Missing** |
| Gemini | `app/llm` gemini provider | **Blocked**: no `GEMINI_API_KEY` |
| Misleading credits answer (">120 credits, still need 0") | `answers.answer_credits` | **Defect** |

## Step 3 — integration so far

| Merged | Commit | Checks after merge |
|---|---|---|
| agent/frontend (5 commits) | `6965a85` | 26 frontend tests, lint, build |
| agent/engine (3 commits) | `174cd40` | 1140 backend tests; black fix to the new walkthrough script in `70b3611` |
| agent/catalog (1 commit) | `6058c14` | 2 engine tests failed: they assumed MATH141 was an unverified stub, which the catalog agent sourced as Precalculus (4 cr, prerequisite of COSC241). Tests updated to assert the correct ordering MATH141 → COSC241 → COSC354 in `e3b9a4d`. **1165 backend tests pass**, ruff, black, catalog doctor clean |

Not in the repository despite the mission brief: Docker Compose and PostgreSQL. Accounts and
records are JSON files under `backend/accounts` and `backend/student_records`.

Open item handed to the AI agent: `test_dropped_second_prerequisite` no longer exercises its
original scenario after the new prerequisites changed the engine's "not yet eligible" list.
